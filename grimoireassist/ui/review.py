"""Review screen: scrub and play back the rolling capture buffer.

Opened by clicking the PiP preview. Like the PiP it is a child of the main
pane rather than a member of its layout, so it floats over whatever view is
showing and a game switch (which rebuilds the layout) leaves it alone.

It reads from a `BufferSnapshot` — the frame list frozen at the moment the
screen opened — so the timeline stays put while the recorder keeps rolling
underneath. Playback is driven by the frames' own capture timestamps rather
than a frame counter, which makes the speed multipliers exact and holds the
last frame across a gap in the capture source.
"""
from __future__ import annotations

import datetime
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
from PyQt6.QtCore import (
    Qt, QEvent, QObject, QRect, QThread, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from ..reviewbuffer import BufferSnapshot, ExportCancelled, export

SPEEDS = (0.5, 0.75, 1.0, 1.5, 2.0)
_DEFAULT_SPEED = 1.0


def _fmt_clock(seconds: float) -> str:
    """Seconds as m:ss, or h:mm:ss once the buffer passes an hour."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _VideoView(QWidget):
    """Letterboxed frame display. Also the click target for play/pause."""

    clicked = pyqtSignal()
    stepped = pyqtSignal(int)  # wheel notches: +1 forward, -1 back

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._placeholder = "no frames buffered yet"
        self.setMinimumHeight(120)

    def set_image(self, image: Optional[QImage]) -> None:
        self._pixmap = QPixmap.fromImage(image) if image is not None else None
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        if self._pixmap is None or self._pixmap.isNull():
            p.setPen(QColor(107, 107, 117))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        w, h = max(1, int(pw * scale)), max(1, int(ph * scale))
        p.drawPixmap(QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h),
                     self._pixmap)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev) -> None:
        notches = ev.angleDelta().y()
        if notches:
            self.stepped.emit(1 if notches < 0 else -1)
            ev.accept()
            return
        super().wheelEvent(ev)


class _ScrubBar(QSlider):
    """Frame-index slider that jumps straight to a clicked position.

    A stock QSlider pages towards the click; for a video timeline the pointer
    position *is* the wanted frame, and holding the button then drags from
    there. Value changes are reported through the usual `valueChanged`.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys are handled by the overlay
        self.setFixedHeight(22)
        self.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px; background: #2a2a36; border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                height: 6px; background: #5b3fa6; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 12px; margin: -5px 0; border-radius: 6px;
                background: #e8e8ec;
            }
            QSlider::handle:horizontal:hover { background: #ffffff; }
        """)

    def _value_at(self, x: int) -> int:
        span = max(1, self.width() - 1)
        ratio = min(max(x / span, 0.0), 1.0)
        return self.minimum() + round(ratio * (self.maximum() - self.minimum()))

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(int(ev.position().x())))
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(int(ev.position().x())))
            ev.accept()
            return
        super().mouseMoveEvent(ev)


class _ExportWorker(QThread):
    """Writes a snapshot to a video file off the GUI thread."""

    progressed = pyqtSignal(int, int)
    saved = pyqtSignal(str)
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, snapshot: BufferSnapshot, path: Path, parent=None) -> None:
        super().__init__(parent)
        # Its own file handles, so a long export doesn't serialise against the
        # scrubbing reads on the GUI thread.
        self._snap = snapshot.independent_reader()
        self._path = path
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            out = export(self._snap, self._path,
                         progress=lambda i, n: self.progressed.emit(i, n),
                         cancel=self.cancel)
        except ExportCancelled:
            self.stopped.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.saved.emit(str(out))
        finally:
            self._snap.close()


class ReviewOverlay(QWidget):
    """Full-pane playback view over the rolling buffer."""

    closed = pyqtSignal()

    def __init__(self, snapshot: BufferSnapshot, recordings_dir: Path,
                 parent: QWidget) -> None:
        super().__init__(parent)
        self._snap = snapshot
        self._recordings_dir = Path(recordings_dir)
        self._index = max(0, len(snapshot) - 1)   # open on the newest frame
        self._playing = False
        self._speed = _DEFAULT_SPEED
        self._syncing = False        # guards programmatic scrub-bar updates
        self._shown_index = -1       # last index actually decoded
        self._play_origin = 0.0      # monotonic time when playback started
        self._play_rel = 0.0         # buffer time at that moment
        self._export: Optional[_ExportWorker] = None

        self.setAutoFillBackground(True)
        self.setStyleSheet("QWidget { background: #15151b; }")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._build_ui()
        self._speed_buttons[SPEEDS.index(_DEFAULT_SPEED)].setChecked(True)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        parent.installEventFilter(self)
        self._fit_to_parent()
        self._sync_scrub()
        self._render_current()
        self._update_labels()

    # ================= construction =================
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_header())

        self._video = _VideoView(self)
        self._video.clicked.connect(self._toggle_play)
        self._video.stepped.connect(self._step)
        if not self._snap:
            self._video.set_placeholder(
                "Nothing buffered yet — the review buffer fills as the capture runs.")
        lay.addWidget(self._video, 1)

        lay.addWidget(self._build_controls())

    def _build_header(self) -> QWidget:
        bar = QWidget(self)
        bar.setStyleSheet(
            "QWidget { background:#1a1a24; border-bottom:1px solid #2a2a36; }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        title = QLabel(f"⟲  Review — last {_fmt_clock(self._snap.duration_s)} "
                       f"({len(self._snap)} frames)")
        title.setStyleSheet("color:#e8e8ec; font-size:13px; font-weight:600;")
        row.addWidget(title)
        row.addStretch()

        self._status = QLabel("")
        self._status.setStyleSheet("color:#6b6b75; font-size:11px;")
        row.addWidget(self._status)

        self._save_btn = QPushButton("💾  Save clip")
        self._save_btn.setToolTip("Write everything in the buffer to a video file")
        self._save_btn.clicked.connect(self._on_save_clicked)
        self._save_btn.setEnabled(bool(self._snap))
        row.addWidget(self._save_btn)

        folder_btn = QPushButton("📁")
        folder_btn.setToolTip("Open the recordings folder")
        folder_btn.setFixedWidth(38)
        folder_btn.clicked.connect(self._open_recordings_folder)
        row.addWidget(folder_btn)

        close_btn = QPushButton("✕  Close")
        close_btn.setToolTip("Back to the live preview (Esc)")
        close_btn.clicked.connect(self.close_review)
        row.addWidget(close_btn)
        return bar

    def _build_controls(self) -> QWidget:
        bar = QWidget(self)
        bar.setStyleSheet("QWidget { background:#1a1a24; border-top:1px solid #2a2a36; }")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(12, 6, 12, 8)
        outer.setSpacing(6)

        self._scrub = _ScrubBar(bar)
        self._scrub.setRange(0, max(0, len(self._snap) - 1))
        self._scrub.setSingleStep(1)
        self._scrub.setEnabled(bool(self._snap))
        self._scrub.valueChanged.connect(self._on_scrub_changed)
        outer.addWidget(self._scrub)

        row = QHBoxLayout()
        row.setSpacing(4)

        def transport(text: str, tip: str, slot) -> QToolButton:
            b = QToolButton(bar)
            b.setText(text)
            b.setToolTip(tip)
            b.setStyleSheet(
                "QToolButton { color:#e8e8ec; background:transparent; border:none;"
                " border-radius:4px; padding:3px 8px; font-size:14px; }"
                "QToolButton:hover { background:#2a2a36; }"
                "QToolButton:disabled { color:#4a4a55; }")
            b.clicked.connect(slot)
            b.setEnabled(bool(self._snap))
            row.addWidget(b)
            return b

        transport("⏮", "Jump to the oldest frame (Home)", lambda: self._seek_index(0))
        transport("◀|", "Previous frame (←)", lambda: self._step(-1))
        self._play_btn = transport("▶", "Play / pause (Space)", self._toggle_play)
        transport("|▶", "Next frame (→)", lambda: self._step(1))
        transport("⏭", "Jump to the newest frame (End)",
                  lambda: self._seek_index(len(self._snap) - 1))

        row.addSpacing(12)
        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setStyleSheet(
            "color:#e8e8ec; font-size:12px; font-family:Consolas,monospace;")
        row.addWidget(self._time_label)
        self._wall_label = QLabel("")
        self._wall_label.setStyleSheet(
            "color:#6b6b75; font-size:11px; font-family:Consolas,monospace;")
        row.addWidget(self._wall_label)

        row.addStretch()

        speed_lbl = QLabel("Speed")
        speed_lbl.setStyleSheet("color:#6b6b75; font-size:11px;")
        row.addWidget(speed_lbl)

        self._speed_buttons: list[QToolButton] = []
        for speed in SPEEDS:
            b = QToolButton(bar)
            b.setText(f"{speed:g}×")
            b.setCheckable(True)
            b.setToolTip(f"Play at {speed:g}× speed")
            b.setStyleSheet(
                "QToolButton { color:#9a9aa3; background:#2a2a36; border:none;"
                " border-radius:4px; padding:3px 9px; font-size:11px; font-weight:600; }"
                "QToolButton:hover { background:#3a3a50; color:#e8e8ec; }"
                "QToolButton:checked { background:#5b3fa6; color:#fff; }")
            b.clicked.connect(lambda _c, s=speed: self._set_speed(s))
            self._speed_buttons.append(b)
            row.addWidget(b)

        outer.addLayout(row)
        return bar

    # ================= playback =================
    def _toggle_play(self) -> None:
        self._set_playing(not self._playing)

    def _set_playing(self, playing: bool) -> None:
        if playing and not self._snap:
            return
        if playing and self._index >= len(self._snap) - 1:
            self._index = 0  # replay from the oldest frame
        self._playing = playing
        self._play_btn.setText("⏸" if playing else "▶")
        if playing:
            self._restart_clock()
            self._timer.start(self._interval_ms())
        else:
            self._timer.stop()

    def _interval_ms(self) -> int:
        """Tick fast enough to show every stored frame at the current speed."""
        rate = max(1.0, self._snap.fps) * self._speed
        return int(min(100, max(8, round(1000.0 / rate))))

    def _restart_clock(self) -> None:
        self._play_origin = time.monotonic()
        self._play_rel = self._snap.rel_time(self._index)

    def _tick(self) -> None:
        rel = self._play_rel + (time.monotonic() - self._play_origin) * self._speed
        if rel >= self._snap.duration_s:
            self._seek_index(len(self._snap) - 1)
            self._set_playing(False)
            return
        self._seek_index(self._snap.index_for_time(rel), keep_clock=True)

    def _set_speed(self, speed: float) -> None:
        self._speed = speed
        for b, s in zip(self._speed_buttons, SPEEDS):
            b.setChecked(s == speed)
        if self._playing:
            self._restart_clock()          # rebase so the new rate starts here
            self._timer.start(self._interval_ms())

    def _step(self, delta: int) -> None:
        self._set_playing(False)
        self._seek_index(self._index + delta)

    def _seek_index(self, index: int, keep_clock: bool = False) -> None:
        if not self._snap:
            return
        index = max(0, min(int(index), len(self._snap) - 1))
        self._index = index
        if not keep_clock and self._playing:
            self._restart_clock()          # a scrub while playing continues from there
        self._sync_scrub()
        self._render_current()
        self._update_labels()

    def _sync_scrub(self) -> None:
        self._syncing = True
        try:
            self._scrub.setValue(self._index)
        finally:
            self._syncing = False

    def _on_scrub_changed(self, value: int) -> None:
        if self._syncing:
            return
        self._seek_index(value)

    def _render_current(self) -> None:
        if self._shown_index == self._index or not self._snap:
            return
        frame = self._snap.frame(self._index)
        if frame is None:
            return
        self._shown_index = self._index
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._video.set_image(
            QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())

    def _update_labels(self) -> None:
        pos = self._snap.rel_time(self._index)
        self._time_label.setText(
            f"{_fmt_clock(pos)} / {_fmt_clock(self._snap.duration_s)}")
        if self._snap:
            wall = datetime.datetime.fromtimestamp(self._snap.wall_time(self._index))
            self._wall_label.setText(
                f"  ·  {wall.strftime('%H:%M:%S')}  ·  frame {self._index + 1}/{len(self._snap)}")

    # ================= save =================
    def _on_save_clicked(self) -> None:
        if self._export is not None:
            self._export.cancel.set()
            self._status.setText("Cancelling…")
            return
        if not self._snap:
            self._status.setText("Nothing to save yet.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self._recordings_dir / f"review_{ts}.mp4"
        self._export = _ExportWorker(self._snap, target, parent=self)
        self._export.progressed.connect(self._on_export_progress)
        self._export.saved.connect(self._on_export_saved)
        self._export.failed.connect(self._on_export_failed)
        self._export.stopped.connect(self._on_export_stopped)
        self._export.finished.connect(self._on_export_finished)
        self._save_btn.setText("✕  Cancel save")
        self._status.setText(
            f"Saving {_fmt_clock(self._snap.duration_s)} of video…")
        self._export.start()

    def _on_export_progress(self, done: int, total: int) -> None:
        pct = round(100 * done / max(1, total))
        self._status.setText(f"Saving… {pct}%  ({done}/{total} frames)")

    def _on_export_saved(self, path: str) -> None:
        self._status.setText(f"Saved {Path(path).name}")

    def _on_export_failed(self, message: str) -> None:
        self._status.setText(f"Save failed: {message}")

    def _on_export_stopped(self) -> None:
        self._status.setText("Save cancelled.")

    def _on_export_finished(self) -> None:
        self._export = None
        self._save_btn.setText("💾  Save clip")

    def _open_recordings_folder(self) -> None:
        import subprocess
        try:
            self._recordings_dir.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer", str(self._recordings_dir)])
        except Exception as exc:
            self._status.setText(f"Could not open folder: {exc}")

    # ================= input =================
    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if key == Qt.Key.Key_Escape:
            self.close_review()
        elif key == Qt.Key.Key_Space:
            self._toggle_play()
        elif key == Qt.Key.Key_Left:
            self._step(-round(self._snap.fps) if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else -1)
        elif key == Qt.Key.Key_Right:
            self._step(round(self._snap.fps) if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
        elif key == Qt.Key.Key_Home:
            self._seek_index(0)                    # jumping doesn't pause, as on ⏮
        elif key == Qt.Key.Key_End:
            self._seek_index(len(self._snap) - 1)
        else:
            super().keyPressEvent(ev)
            return
        ev.accept()

    # ================= placement / lifetime =================
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self._fit_to_parent()
        return False

    def _fit_to_parent(self) -> None:
        host = self.parentWidget()
        if host is not None:
            self.setGeometry(0, 0, host.width(), host.height())
            self.raise_()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._fit_to_parent()
        self.setFocus()

    def close_review(self, confirm: bool = True) -> None:
        """Stop playback, release the snapshot, and tell the host to bring the
        live preview back. An unfinished save is confirmed first — it can be
        minutes of work to throw away. `confirm=False` skips the prompt, for
        application shutdown where the user has already decided."""
        self._set_playing(False)
        if self._export is not None and confirm:
            from PyQt6.QtWidgets import QMessageBox
            answer = QMessageBox.question(
                self, "Save in progress",
                "The clip is still being written. Close the review and cancel it?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Discard,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
            self._export.blockSignals(True)   # no callbacks into a dying widget
            self._export.cancel.set()
            self._export.wait(3000)
            self._export = None
        self._snap.close()
        self.hide()
        self.closed.emit()
