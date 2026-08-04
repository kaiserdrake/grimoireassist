"""Live input-frame preview: a small PiP overlay pinned to the host's corner.

Refreshes at a configurable rate from the shared FrameBuffer (`ui.preview_fps`,
default 10, capped at 30); the timer only runs while the widget is visible, so
a hidden preview costs nothing.
Optionally draws OCR results on top — translucent tints over the configured
regions, green boxes hugging the text that matched.

The widget is resizable by dragging its top-right grip (it's anchored to the
bottom-left, so that corner is the free one). Only the width is a degree of
freedom — the height always follows the source frame's aspect ratio. The
chosen width is reported via `size_changed` so the host can persist it.
"""
from __future__ import annotations

from typing import Optional

import cv2
from PyQt6.QtCore import Qt, QEvent, QObject, QRect, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from ..capture import FrameBuffer

DEFAULT_WIDTH = 240  # preview width; height follows the frame's aspect ratio
MIN_WIDTH = 120      # below this the OCR overlay stops being readable
_MARGIN = 12         # gap to the host's bottom-left corner
_GRIP = 16           # size of the top-right resize handle, in px


class InputPreview(QWidget):
    """Small live view of the raw capture frames, floating over the parent's
    bottom-left corner (outside its layout)."""

    size_changed = pyqtSignal(int)  # new width, emitted when a drag-resize ends

    def __init__(self, buffer: FrameBuffer, fps: float, parent: QWidget,
                 width: int = DEFAULT_WIDTH) -> None:
        super().__init__(parent)
        self._buffer = buffer
        self._pixmap: Optional[QPixmap] = None
        self._last_seq = -1
        self._frame_size: Optional[tuple[int, int]] = None  # (w, h) of source
        self._regions: list = []  # [(x, y, w, h, matched)] in frame coords
        self._bottom_inset = 0    # extra bottom gap (e.g. for the debug panel)
        self._aspect = 16 / 9     # replaced by the real ratio on the first frame
        self._width = 0           # width actually laid out (may be host-capped)
        self._wanted = DEFAULT_WIDTH  # width the user asked for / config holds
        self._drag: Optional[tuple] = None  # (grab point, width at grab), while resizing
        self.setMouseTracking(True)         # so the grip can change the cursor
        self._apply_width(width)
        self._timer = QTimer(self)
        self._timer.setInterval(round(1000 / min(max(fps, 1.0), 30.0)))
        self._timer.timeout.connect(self._tick)
        parent.installEventFilter(self)

    @pyqtSlot(list)
    def set_region_status(self, rects: list) -> None:
        """Update the OCR overlay: [(x, y, w, h, matched), ...] in frame
        coordinates — configured regions as matched=False outlines plus a
        matched=True box per recognised text. Emitted by the OCR worker;
        also used to show idle regions while tracking is stopped."""
        self._regions = rects
        self.update()

    def set_bottom_inset(self, px: int) -> None:
        """Keep the preview this many pixels above the host's bottom edge
        (used when the debug panel occupies the bottom of the window)."""
        self._bottom_inset = max(0, px)
        self._refit()  # the shorter host may cap the width

    # ---- sizing ----------------------------------------------------------
    def _clamp_width(self, width: int) -> int:
        """Keep the preview at least MIN_WIDTH and small enough to fit the
        host with its margins (both dimensions — height follows the aspect)."""
        host = self.parentWidget()
        max_w = 4096
        if host is not None and host.width() > 0:
            max_w = host.width() - 2 * _MARGIN
            avail_h = host.height() - 2 * _MARGIN - self._bottom_inset
            if avail_h > 0:
                max_w = min(max_w, round(avail_h * self._aspect))
        return int(max(MIN_WIDTH, min(int(width), max(MIN_WIDTH, max_w))))

    def _apply_width(self, width: int) -> None:
        """Request `width` (from config or a drag) and lay out at it."""
        self._wanted = max(MIN_WIDTH, int(width))
        self._refit()

    def _refit(self) -> None:
        """Lay out at the requested width, capped to what the host can hold.
        The request is kept intact, so growing the host restores the full size."""
        width = self._clamp_width(self._wanted)
        height = max(1, round(width / self._aspect))
        if (width, height) != (self.width(), self.height()) or width != self._width:
            self._width = width
            self.setFixedSize(width, height)
            self._last_seq = -1  # re-render the frame at the new resolution
        self._reposition()

    def width_setting(self) -> int:
        """Current preview width — what the host persists to config."""
        return self._wanted

    # ---- frame updates -------------------------------------------------
    def _tick(self) -> None:
        if self._buffer.current_seq() == self._last_seq:
            return  # source stalled — skip the copy/convert entirely
        frame, seq = self._buffer.get()
        if frame is None:
            return
        self._last_seq = seq
        h, w = frame.shape[:2]
        self._frame_size = (w, h)
        aspect = w / max(1, h)
        if abs(aspect - self._aspect) > 1e-3:
            self._aspect = aspect
            self._refit()  # height follows the new ratio
        pw, ph = self.width(), self.height()
        # Downscale first so the RGB conversion and QImage copy work on
        # preview-sized data. INTER_LINEAR over INTER_AREA: ~13x faster and the
        # quality gap doesn't matter for a monitoring thumbnail.
        small = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        self._pixmap = QPixmap.fromImage(
            QImage(rgb.data, pw, ph, 3 * pw,
                   QImage.Format.Format_RGB888).copy())
        self.update()

    # ---- painting --------------------------------------------------------
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self._pixmap is not None:
            p.drawPixmap(self.rect(), self._pixmap)
        else:
            p.fillRect(self.rect(), QColor(10, 10, 14))
            p.setPen(QColor(120, 120, 130))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "no signal")
        if self._regions and self._frame_size:
            fw, fh = self._frame_size
            sx = self.width() / max(1, fw)
            sy = self.height() / max(1, fh)
            for x, y, w, h, matched in self._regions:
                rect = QRect(round(x * sx), round(y * sy),
                             round(w * sx), round(h * sy))
                if matched:
                    # matched text: green fill + crisp border
                    p.fillRect(rect, QColor(46, 204, 113, 70))
                    p.setPen(QPen(QColor("#2ecc71"), 2))
                    p.drawRect(rect)
                else:
                    # configured region: subtle translucent tint, no border
                    p.fillRect(rect, QColor(90, 140, 255, 40))
        p.setPen(QColor(42, 42, 54))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        self._paint_grip(p)

    def _paint_grip(self, p: QPainter) -> None:
        """Three short diagonals in the top-right corner: the resize handle."""
        p.setPen(QPen(QColor(255, 255, 255, 110 if self._drag else 70), 1))
        right = self.width() - 1
        for off in (5, 9, 13):
            p.drawLine(right - 2, off, right - off, 2)

    # ---- resizing ----------------------------------------------------------
    def _in_grip(self, pos) -> bool:
        return pos.x() >= self.width() - _GRIP and pos.y() <= _GRIP

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self._in_grip(ev.position()):
            self._drag = (ev.globalPosition().toPoint(), self._width)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is None:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor
                           if self._in_grip(ev.position())
                           else Qt.CursorShape.ArrowCursor)
            super().mouseMoveEvent(ev)
            return
        start, base = self._drag
        pos = ev.globalPosition().toPoint()
        dx, dy = pos.x() - start.x(), pos.y() - start.y()
        # The grabbed corner travels (+1, -1/aspect) per pixel of added width,
        # so project the mouse delta onto that direction: dragging up-right grows
        # the preview, and a free-hand drag still tracks the pointer closely.
        k = 1.0 / self._aspect
        self._apply_width(round(base + (dx - dy * k) / (1 + k * k)))
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag is not None:
            self._drag = None
            self._wanted = self._width  # drop any slack past the host-imposed cap
            self.size_changed.emit(self._width)
            self.update()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev) -> None:
        self.unsetCursor()
        super().leaveEvent(ev)

    # ---- placement ---------------------------------------------------------
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self._refit()  # a shrunken host caps the width; a grown one frees it
        return False

    def _reposition(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        self.move(_MARGIN,
                  host.height() - self.height() - _MARGIN - self._bottom_inset)
        self.raise_()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._last_seq = -1  # force a fresh frame on the first tick
        self._reposition()
        self._timer.start()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._timer.stop()
