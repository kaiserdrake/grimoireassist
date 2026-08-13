"""Main window: capture + virtual cam (global) and a per-game OCR worker + panel.

Camera, calibration, always-on-top and game switching live behind a burger menu.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import re
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QAction, QActionGroup, QGuiApplication, QIcon, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QToolBar,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import __version__
from ..battle import OcrWorker
from ..capture import CaptureThread, FrameBuffer, list_named_devices
from ..config import Config, GameSettings
from ..games import (
    GameInfo, get_game, default_game, icon_path, import_dir, load_catalog,
    monster_names, monster_imported_data, save_game, slug_map,
    write_default_settings,
)
from ..hotkey import GlobalHotkey
from ..ocr import build_engine
from ..overlay import OverlayModel
from ..reviewbuffer import RollingRecorder
from ..virtualcam import VirtualCamSink
from .browser import BrowserPanel, SEARCH_ENGINES
from .calibrate import CalibrateDialog
from .game_select import GameSelectDialog
from .import_wizard import ImportWizard
from .controller_overlay import ControllerMapOverlay
from .controllers import LAYOUTS, LAYOUT_ORDER
from .monster_panel import MonsterNav, MonsterPanel, AutoSwitchToggle
from .preview import InputPreview
from .review import ReviewOverlay


class MainWindow(QMainWindow):
    _camera_scan_done = pyqtSignal(list)

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.resize(1400, 900)
        _icon = icon_path()
        if _icon:
            self.setWindowIcon(QIcon(_icon))

        self.model = OverlayModel()
        self.buffer = FrameBuffer()
        self.vcam = VirtualCamSink(fps=cfg.capture.fps) if cfg.virtual_camera.enabled else None
        self.engine = build_engine(cfg.ocr.engine, cfg.ocr.languages, cfg.ocr.gpu_effective())

        self.capture: Optional[CaptureThread] = None
        self.worker: Optional[OcrWorker] = None
        self.panel: Optional[MonsterPanel] = None
        self.game: Optional[GameInfo] = None
        self._auto_switch = True            # follow OCR detections to tracking view
        self._grimoire_shown = False        # is the Grimoire view currently on top
        self._detections: list = []         # latest [(name, confidence)]
        self._tracking_active = False       # OCR worker only runs when user starts it

        self._camera_devices: list = []

        self._build_menu()
        self._build_statusbar()
        self._build_shortcuts()
        self._debug_widget = self._build_debug_panel()
        self._debug_widget.setVisible(False)

        # Permanent central splitter: [main view host | browser drawer].
        # _start_game only swaps the child of _main_host, so the browser
        # (and its tabs) survives game switches.
        self._main_host = QWidget()
        _host_lay = QVBoxLayout(self._main_host)
        _host_lay.setContentsMargins(0, 0, 0, 0)
        _host_lay.setSpacing(0)
        self.browser = BrowserPanel(cfg=cfg)
        self.browser.setVisible(False)   # drawer starts closed
        self.browser.status_message.connect(
            lambda msg: self.statusBar().showMessage(msg, 4000))
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._main_host)
        self._splitter.addWidget(self.browser)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        # main-pane share of the splitter; updated when the drawer closes so
        # a user-dragged ratio survives toggling the browser off and on.
        # Loaded from config (saved on exit) so it also survives restarts.
        self._main_ratio = min(max(cfg.ui.browser_split_ratio, 0.1), 0.9)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background:#2a2a36; }")
        self.setCentralWidget(self._splitter)

        # Live input-frame PiP: floats over the main pane's bottom-left corner,
        # outside the layout. Its refresh timer only runs while it is visible.
        # Drag its top-right grip to resize; the width is persisted to config.
        self._preview = InputPreview(self.buffer, fps=cfg.ui.preview_fps,
                                     parent=self._main_host,
                                     width=cfg.ui.preview_width)
        self._preview.size_changed.connect(self._on_preview_resized)
        self._preview.clicked.connect(self._open_review)
        self._preview.setVisible(False)
        if cfg.ui.show_input_preview:
            self.act_preview.setChecked(True)  # fires _toggle_preview

        # Controller button reference: two pads side by side, floating over the
        # tracking view only. Drag to move, grip to resize, chevron to collapse;
        # all three are persisted to config.
        self._ctrl_map = ControllerMapOverlay(
            parent=self._main_host,
            left_id=cfg.ui.controller_map_left,
            right_id=cfg.ui.controller_map_right,
            width=cfg.ui.controller_map_width,
            fx=cfg.ui.controller_map_x, fy=cfg.ui.controller_map_y,
            collapsed=cfg.ui.controller_map_collapsed)
        self._ctrl_map.geometry_changed.connect(self._on_ctrl_map_geometry)
        self._ctrl_map.collapsed_changed.connect(self._on_ctrl_map_collapsed)
        self._ctrl_map.setVisible(False)
        # Reflect the stored state in the menu item and toolbar button; the
        # _set_grimoire(False) at the end of __init__ turns it visible.
        self._sync_controller_map_buttons()

        # Rolling review buffer: the last N minutes of capture, kept on disk and
        # replayed by the review screen. Started before capture so the frame sink
        # has somewhere to put the very first frame.
        self.recorder: Optional[RollingRecorder] = None
        self._review: Optional[ReviewOverlay] = None
        if cfg.review.enabled:
            self._start_recorder()
        self._sync_review_ui()

        # capture is global (one camera feeds every game)
        self._start_capture(cfg.capture.device_index)

        # Scan for devices once at startup so the Camera menu is ready immediately.
        self._populate_camera_menu()

        # load the selected game (panel + worker)
        game = get_game(cfg.selected_game, cfg._path) or default_game(cfg._path)
        self._start_game(game)

        if cfg.ui.always_on_top:
            self._apply_on_top(True)

        # Pre-warm the OCR engine in the background so the first Start click is
        # instant. The button is disabled until the model finishes loading.
        self._start_warmup()

        # camera-health tracking (drives the error status)
        self._last_seq = 0   # 0 matches the buffer's initial seq, avoiding a false
        self._camera_ok = False  # "Source Active" on the very first timer tick
        self._flow_streak = 0    # consecutive ticks with same flowing state (debounce)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)

        self._idle_secs: int = 0          # seconds elapsed with no monsters
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(1000)
        self._idle_timer.timeout.connect(self._on_idle_tick)
        # Open on the tracking view. The idle countdown to Grimoire only starts once
        # monsters have been detected and then lost — not on a fresh launch.
        self._set_grimoire(False)
        self._size_to_screen(0.6)

        if cfg.ui.auto_start_tracking and not self._tracking_active:
            self._toggle_tracking()

    def _size_to_screen(self, fraction: float) -> None:
        """Size the window to a fraction of the available screen and center it."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w = int(avail.width() * fraction)
        h = int(avail.height() * fraction)
        self.resize(w, h)
        self.move(
            avail.x() + (avail.width() - w) // 2,
            avail.y() + (avail.height() - h) // 2,
        )

    # ================= per-game lifecycle =================
    def _start_game(self, game: Optional[GameInfo]) -> None:
        if game is None:
            self.setWindowTitle(f"GrimoireAssist {__version__}")
            return
        self.game = game
        self.cfg.selected_game = game.id
        self.cfg.monster_name_list = monster_names(game.id, self.cfg._path)
        gs = self.cfg.regions_for(game.id)
        self.cfg.ocr.regions_monster_names = gs.monster_names
        self.cfg.ocr.regions_battle_end = gs.battle_end
        self.cfg.ocr.keywords_battle_end = gs.end_keywords
        self.cfg.save()
        self.setWindowTitle(f"GrimoireAssist {__version__} — {game.name}")

        # (re)build panel for this game's site + slugs
        self.model = OverlayModel()
        self._detections = []
        _imported = monster_imported_data(game.id, self.cfg._path)
        _img_base = import_dir(game.id, self.cfg._path) if self.cfg._path else None
        self.panel = MonsterPanel(
            game.site_url_template, slug_map=slug_map(),
            url_style=game.url_style, multi_joiner=game.multi_joiner,
            requires_login=game.requires_login, notes_url=game.notes_url,
            imported_data=_imported, image_base=_img_base,
            cards_per_row=game.cards_per_row)
        # Wrap panel + debug log in a single container so the debug log
        # appears below the monster panel without replacing it.
        wrapper = QWidget()
        wlay = QVBoxLayout(wrapper)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)
        wlay.addWidget(self.panel, 1)
        wlay.addWidget(self._debug_widget)
        self._set_main_widget(wrapper)
        self._refresh_panel()

        # bookmarks follow the game (covers startup and every switch)
        self.browser.set_game_bookmarks(game.bookmarks_url)

        # Only (re)start the OCR worker if tracking was already active.
        # On first load _tracking_active is False, so we wait for the user
        # to press Start before burning CPU on inference.
        if self._tracking_active:
            self._start_worker()
        else:
            self._stop_worker()

    def _set_main_widget(self, w: QWidget) -> None:
        """Swap the left (main view) pane of the splitter. The new wrapper has
        already re-parented _debug_widget into itself, so deleting the old
        wrapper is safe."""
        lay = self._main_host.layout()
        while lay.count():
            old = lay.takeAt(0).widget()
            if old is not None:
                old.deleteLater()
        lay.addWidget(w)
        # keep the floating children (PiP, controller map, review screen)
        # above the new panel
        if getattr(self, "_preview", None) is not None:
            self._preview.raise_()
        if getattr(self, "_ctrl_map", None) is not None:
            self._ctrl_map.raise_()
        if getattr(self, "_review", None) is not None:
            self._review.raise_()

    # ================= browser drawer =================
    def _toggle_browser(self) -> None:
        self._set_browser_open(not self.browser.isVisible())

    def _set_browser_open(self, open_: bool) -> None:
        if open_:
            self.browser.ensure_ready()
            self.browser.setVisible(True)
            # restore the last user-chosen split (50/50 by default)
            total = max(self._splitter.width(), 1)
            main_w = round(total * self._main_ratio)
            self._splitter.setSizes([main_w, total - main_w])
            self.browser.focus_url_bar()
        else:
            sizes = self._splitter.sizes()
            if sizes[1] > 0:
                self._main_ratio = sizes[0] / (sizes[0] + sizes[1])
            # Drop focus held inside the drawer before hiding it. Hiding the
            # focused widget makes Qt tab-focus the next widget — the grimoire
            # web view — and Chromium renders tab-focus as a focus ring around
            # the page (the white "selection" box).
            fw = QApplication.focusWidget()
            if fw is not None and self.browser.isAncestorOf(fw):
                fw.clearFocus()
            self.browser.setVisible(False)  # main pane auto-fills 100%
        self.browser_btn.setChecked(open_)

    def _start_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(1500)
            self.worker = None
        if OcrWorker is None:
            return
        self.worker = OcrWorker(self.cfg, self.buffer, self.engine)
        self.worker.monsters_changed.connect(self._on_monsters_changed)
        self.worker.monster_killed.connect(self._on_monster_killed)
        self.worker.battle_started.connect(self._on_battle_started)
        self.worker.battle_ended.connect(self._on_battle_ended)
        self.worker.error.connect(self._on_ocr_error)
        self.worker.debug_text.connect(self._on_debug_text)
        self.worker.region_status.connect(self._preview.set_region_status)
        self.worker.start()

    def _stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(1500)
            self.worker = None
        self._detections = []
        self.model = OverlayModel()
        self._refresh_panel()
        self._push_idle_regions()

    def _push_idle_regions(self) -> None:
        """Show the configured OCR regions on the preview as dim outlines
        (no matches) — the state whenever tracking isn't running."""
        rects = [(r.x, r.y, r.w, r.h, False)
                 for r in self.cfg.ocr.regions_monster_names if r.is_set()]
        end = self.cfg.ocr.regions_battle_end
        if end.is_set():
            rects.append((end.x, end.y, end.w, end.h, False))
        self._preview.set_region_status(rects)

    def _toggle_tracking(self) -> None:
        self._tracking_active = not self._tracking_active
        if self._tracking_active:
            self._start_worker()
        else:
            self._stop_worker()
        self._update_tracking_btn()

    def _toggle_auto_start_tracking(self, checked: bool) -> None:
        self.cfg.ui.auto_start_tracking = checked
        self.cfg.save()
        self.statusBar().showMessage(
            "Tracking will start automatically on launch" if checked
            else "Tracking will wait for Start on launch", 3000)

    # ================= rolling review buffer =================
    def _data_dir(self) -> Path:
        """Where side-car data lives (buffer/, recordings/, snapshots/, logs/):
        next to config.yaml, i.e. beside the exe — never the CWD."""
        return Path(self.cfg._path).parent if self.cfg._path else Path(".")

    def _capture_sink(self, frame) -> None:
        """Clean-frame fan-out, called on the capture thread.

        The review buffer goes first: `VirtualCamSink.send` paces itself with a
        sleep, and running that first would fold the wait into the buffered
        frames' timestamps. Neither call can raise into the capture loop —
        `feed` swallows its own errors and CaptureThread guards the sink.
        """
        rec = self.recorder
        if rec is not None:
            rec.feed(frame)
        if self.vcam is not None:
            self.vcam.send(frame)

    def _start_recorder(self) -> bool:
        if self.recorder is not None and self.recorder.running:
            return True
        rev = self.cfg.review
        recorder = RollingRecorder(
            self._data_dir() / "buffer",
            minutes=rev.minutes, fps=rev.fps, max_height=rev.max_height,
            quality=rev.jpeg_quality, max_disk_mb=rev.max_disk_mb,
        )
        if not recorder.start():
            self.statusBar().showMessage(
                f"Review buffer: {recorder.last_error or 'could not start'}", 6000)
            return False
        self.recorder = recorder
        return True

    def _stop_recorder(self, confirm: bool = True) -> None:
        """Stop recording and drop the buffered video (it is a live cache —
        anything worth keeping was already saved from the review screen)."""
        if self._review is not None:
            self._review.close_review(confirm=confirm)
        if self.recorder is not None:
            self.recorder.stop(discard=True)
            self.recorder = None

    def _toggle_review_buffer(self, enabled: bool) -> None:
        self.cfg.review.enabled = enabled
        self.cfg.save()
        if enabled:
            if self._start_recorder():
                self.statusBar().showMessage(
                    f"Review buffer on — keeping the last "
                    f"{self.cfg.review.minutes:g} minutes of capture", 4000)
        else:
            self._stop_recorder()
            self.statusBar().showMessage(
                "Review buffer off — buffered video discarded", 4000)
        self._sync_review_ui()

    def _sync_review_ui(self) -> None:
        """Reflect the recorder's real state in the menu, PiP hint and status bar
        (a start can fail, e.g. an unwritable buffer directory)."""
        on = self.recorder is not None
        self.act_review_buffer.blockSignals(True)   # setChecked would re-enter
        self.act_review_buffer.setChecked(on)
        self.act_review_buffer.blockSignals(False)
        self.act_open_review.setEnabled(on)
        self._update_review_status()

    def _update_review_status(self) -> None:
        """Refresh the buffered-length readouts (status bar + PiP click hint)."""
        if self.recorder is None:
            self._review_label.setText("Review buffer: off")
            self._preview.set_click_hint("")
            return
        buffered = self.recorder.stats()["seconds"]
        mins, secs = divmod(int(buffered), 60)
        self._review_label.setText(f"⏺ Review buffer: {mins}:{secs:02d}")
        self._preview.set_click_hint(f"⟲  Click to review — {mins}:{secs:02d} buffered")

    def _open_review(self) -> None:
        """Show the review screen over the main pane (PiP click / Ctrl+R)."""
        if self._review is not None:
            self._review.raise_()
            return
        if self.recorder is None:
            self.statusBar().showMessage(
                "Review buffer is off — turn it on under Review in the menu.", 5000)
            return
        # Let the encoder catch up so a click includes the newest frames.
        self.recorder.drain(timeout=0.5)
        snap = self.recorder.snapshot()
        if not snap:
            snap.close()
            self.statusBar().showMessage(
                "Nothing buffered yet — the review buffer fills as capture runs.", 4000)
            return
        self._review = ReviewOverlay(snap, self._data_dir() / "recordings",
                                     parent=self._main_host)
        self._review.closed.connect(self._on_review_closed)
        self._preview.setVisible(False)   # its refresh timer stops with it
        self._sync_controller_map()       # the review screen owns the pane
        self._review.show()
        self._review.raise_()

    def _on_review_closed(self) -> None:
        review, self._review = self._review, None
        if review is not None:
            review.deleteLater()
        self._preview.setVisible(self.act_preview.isChecked())
        if self._preview.isVisible():
            self._preview.raise_()
        self._sync_controller_map()

    def _open_recordings_folder(self) -> None:
        import subprocess
        out = self._data_dir() / "recordings"
        try:
            out.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer", str(out)])
        except Exception as exc:
            self.statusBar().showMessage(f"Could not open recordings: {exc}", 4000)

    # ================= frame snapshot =================
    def _save_snapshot(self) -> None:
        """Save the latest captured frame to snapshots/ as a timestamped PNG."""
        frame, _ = self.buffer.get()
        if frame is None:
            self.statusBar().showMessage("Snapshot: no frame captured yet", 3000)
            return
        import datetime
        from pathlib import Path
        import cv2
        out_dir = (Path(self.cfg._path).parent if self.cfg._path else Path(".")) / "snapshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        # millisecond precision so rapid macro-key presses don't collide
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = out_dir / f"snapshot_{ts}.png"
        if cv2.imwrite(str(path), frame):
            self.statusBar().showMessage(f"Snapshot saved: {path.name}", 4000)
        else:
            self.statusBar().showMessage(f"Snapshot failed to write {path.name}", 4000)

    def _add_game(self) -> None:
        from .add_game_dialog import AddGameDialog
        dlg = AddGameDialog(self.cfg._path, parent=self)
        if dlg.exec() and dlg.result_game:
            self.statusBar().showMessage(
                f"Game '{dlg.result_game.name}' added — use Switch game to select it.", 5000)

    def _switch_game(self) -> None:
        dlg = GameSelectDialog(list(load_catalog(self.cfg._path)),
                               current=self.cfg.selected_game, parent=self)
        if dlg.exec() and dlg.selected and dlg.selected != self.cfg.selected_game:
            self._start_game(get_game(dlg.selected, self.cfg._path))

    def _open_import_wizard(self) -> None:
        if not self.game:
            return
        save_dir = (import_dir(self.game.id, self.cfg._path)
                    if self.cfg._path else None)
        if save_dir is None:
            return
        profile = getattr(self.panel, "_profile", None)
        dlg = ImportWizard(
            game_id=self.game.id,
            notes_url=self.game.notes_url or self.game.site_url_template,
            profile=profile,
            save_dir=save_dir,
            parent=self,
        )
        dlg.import_done.connect(self._on_import_done)
        dlg.exec()

    def _on_import_done(self, game_id: str) -> None:
        if game_id != (self.game.id if self.game else None):
            return
        imported = monster_imported_data(game_id, self.cfg._path)
        img_base = import_dir(game_id, self.cfg._path) if self.cfg._path else None
        if self.panel:
            self.panel.update_imported_data(imported, img_base)
        # Refresh OCR monster list — import data is now the source of truth
        if self.game:
            self.cfg.monster_name_list = monster_names(game_id, self.cfg._path)
        self.statusBar().showMessage(
            f"Monster data imported for {self.game.name}", 4000)

    # ================= menu / chrome =================
    def _build_menu(self) -> None:
        tb = QToolBar("Menu")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.menu_btn = QToolButton()
        self.menu_btn.setText("☰")  # burger ≡
        self.menu_btn.setToolTip("Menu")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setStyleSheet("QToolButton { font-size:18px; padding:2px 10px; }")

        self.menu = QMenu(self)

        # ── Camera ──────────────────────────────────────────────
        self.menu.addSection("Camera")
        self.camera_menu = self.menu.addMenu("Select source…")
        self.menu.addAction("Retry camera", self._retry_camera)
        self.menu.addAction("Calibrate regions…\tF9", self._open_calibration)
        _snap_label = self.cfg.ui.snapshot_hotkey.replace(" ", "").title()
        self.menu.addAction(f"Snapshot frame\t{_snap_label}", self._save_snapshot)

        # ── Review ──────────────────────────────────────────────
        self.menu.addSection("Review")
        self.act_open_review = self.menu.addAction(
            "Review capture…\tCtrl+R", self._open_review)
        self.act_open_review.setToolTip(
            "Play back the rolling capture buffer (or click the input preview)")
        self.act_review_buffer = self.menu.addAction("Keep rolling capture buffer")
        self.act_review_buffer.setCheckable(True)
        self.act_review_buffer.setChecked(self.cfg.review.enabled)
        self.act_review_buffer.toggled.connect(self._toggle_review_buffer)
        self.menu.addAction("Open saved clips folder", self._open_recordings_folder)

        # ── OCR ─────────────────────────────────────────────────
        self.menu.addSection("OCR")
        self.act_gpu = self.menu.addAction("Use GPU")
        self.act_gpu.setCheckable(True)
        self.act_gpu.setChecked(self.cfg.ocr.gpu_effective())
        self.act_gpu.toggled.connect(self._toggle_gpu)
        conf_menu = self.menu.addMenu("Track confidence")
        conf_group = QActionGroup(conf_menu)
        conf_group.setExclusive(True)
        for level, label in (("low", "Low and up (all)"), ("mid", "Mid and up"),
                             ("high", "High only")):
            act = QAction(label, conf_menu)
            act.setCheckable(True)
            act.setChecked(self.cfg.ocr.min_confidence_level == level)
            act.triggered.connect(lambda _c, lv=level: self._set_min_confidence(lv))
            conf_group.addAction(act)
            conf_menu.addAction(act)
        self.act_auto_track = self.menu.addAction("Auto-start tracking on launch")
        self.act_auto_track.setCheckable(True)
        self.act_auto_track.setChecked(self.cfg.ui.auto_start_tracking)
        self.act_auto_track.toggled.connect(self._toggle_auto_start_tracking)

        # ── Game ─────────────────────────────────────────────────
        self.menu.addSection("Game")
        self.menu.addAction("Add game…", self._add_game)
        self.menu.addAction("Switch game…", self._switch_game)
        self.menu.addAction("Import monster data…", self._open_import_wizard)

        # ── Browser ──────────────────────────────────────────────
        self.menu.addSection("Browser")
        engine_menu = self.menu.addMenu("Search engine")
        engine_group = QActionGroup(engine_menu)
        engine_group.setExclusive(True)
        current_engine = getattr(self.cfg.ui, "search_engine", "google")
        for key, (label, _tmpl) in SEARCH_ENGINES.items():
            act = QAction(label, engine_menu)
            act.setCheckable(True)
            act.setChecked(current_engine == key)
            act.triggered.connect(lambda _c, k=key: self.browser.set_search_engine(k))
            engine_group.addAction(act)
            engine_menu.addAction(act)

        # ── Window ───────────────────────────────────────────────
        self.menu.addSection("Window")
        self.act_on_top = self.menu.addAction("Always on top")
        self.act_on_top.setCheckable(True)
        self.act_on_top.setChecked(self.cfg.ui.always_on_top)
        self.act_on_top.toggled.connect(self._toggle_on_top)
        self.act_fullscreen = self.menu.addAction("Fullscreen\tF11")
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.triggered.connect(self._toggle_fullscreen)
        # Starts unchecked because the preview widget doesn't exist yet when the
        # menu is built; __init__ re-checks it from config after creating it.
        self.act_preview = self.menu.addAction("Input preview")
        self.act_preview.setCheckable(True)
        self.act_preview.setChecked(False)
        self.act_preview.toggled.connect(self._toggle_preview)
        # Same story: the overlay doesn't exist yet, so __init__ re-checks this
        # from config once it does.
        self.act_ctrl_map = self.menu.addAction("Controller button map")
        self.act_ctrl_map.setCheckable(True)
        self.act_ctrl_map.setChecked(False)
        self.act_ctrl_map.toggled.connect(self._set_controller_map_enabled)
        pads_menu = self.menu.addMenu("Controller map")
        for side, current in (("left", self.cfg.ui.controller_map_left),
                              ("right", self.cfg.ui.controller_map_right)):
            side_menu = pads_menu.addMenu(f"{side.capitalize()} pad")
            group = QActionGroup(side_menu)
            group.setExclusive(True)
            for pad_id in LAYOUT_ORDER:
                act = QAction(LAYOUTS[pad_id].name, side_menu)
                act.setCheckable(True)
                act.setChecked(current == pad_id)
                act.triggered.connect(
                    lambda _c, s=side, p=pad_id: self._set_controller_pad(s, p))
                group.addAction(act)
                side_menu.addAction(act)

        # ── Debug ────────────────────────────────────────────────
        self.menu.addSection("Debug")
        self.act_debug = self.menu.addAction("Show OCR debug log")
        self.act_debug.setCheckable(True)
        self.act_debug.setChecked(False)
        self.act_debug.toggled.connect(self._toggle_debug)
        self.act_log_file = self.menu.addAction("Log to file")
        self.act_log_file.setCheckable(True)
        self.act_log_file.setChecked(self.cfg.logging.to_file)
        self.act_log_file.toggled.connect(self._toggle_file_logging)

        self.menu.aboutToShow.connect(self._show_camera_menu)
        self._camera_scan_done.connect(self._rebuild_camera_menu)

        self.menu_btn.setMenu(self.menu)

        # Navbar = burger menu (far left) + detection result + grimoire toggle (right).
        tb.addWidget(self.menu_btn)
        tb.addSeparator()

        self.nav = MonsterNav()
        self.nav.monster_selected.connect(self._on_monster_selected)
        tb.addWidget(self.nav)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # Start / Stop tracking button.
        self.tracking_btn = QToolButton()
        self.tracking_btn.setCheckable(True)
        self.tracking_btn.setChecked(False)
        self.tracking_btn.clicked.connect(self._toggle_tracking)
        self._update_tracking_btn()
        tb.addWidget(self.tracking_btn)
        tb.addSeparator()

        # Auto Switch toggle: when on, OCR detections switch to the tracking view.
        self.auto_switch_toggle = AutoSwitchToggle()
        self.auto_switch_toggle.set_on(self._auto_switch)
        self.auto_switch_toggle.toggled.connect(self._on_auto_switch_toggled)
        tb.addWidget(self.auto_switch_toggle)
        tb.addSeparator()

        # Manual Grimoire view toggle — independent of Auto Switch; showing the
        # Grimoire never changes the Auto Switch state.
        self.grimoire_btn = QToolButton()
        self.grimoire_btn.setCheckable(True)
        self.grimoire_btn.setStyleSheet(
            "QToolButton { font-size:15px; padding:2px 8px; }")
        self.grimoire_btn.clicked.connect(self._toggle_grimoire_view)
        self._update_grimoire_btn()
        tb.addWidget(self.grimoire_btn)
        tb.addSeparator()

        # Controller button map toggle — mirrors act_ctrl_map in the menu.
        self.ctrl_map_btn = QToolButton()
        self.ctrl_map_btn.setText("🎮")
        self.ctrl_map_btn.setCheckable(True)
        self.ctrl_map_btn.setToolTip("Toggle the controller button map")
        self.ctrl_map_btn.setStyleSheet(
            "QToolButton { font-size:15px; padding:2px 8px; }")
        self.ctrl_map_btn.clicked.connect(self._set_controller_map_enabled)
        tb.addWidget(self.ctrl_map_btn)
        tb.addSeparator()

        # Browser drawer toggle.
        self.browser_btn = QToolButton()
        self.browser_btn.setText("🌐")
        self.browser_btn.setCheckable(True)
        self.browser_btn.setToolTip("Toggle browser (Ctrl+B)")
        self.browser_btn.setStyleSheet(
            "QToolButton { font-size:15px; padding:2px 8px; }")
        self.browser_btn.clicked.connect(self._toggle_browser)
        tb.addWidget(self.browser_btn)

    def _start_warmup(self) -> None:
        """Pre-load the OCR model in the background. Start is always enabled
        immediately — warmup only speeds up the first inference, it is never a gate."""
        engine_name = type(self.engine).__name__.replace("Engine", "")
        self.statusBar().showMessage(f"OCR engine: {engine_name}")

        if getattr(self.engine, "ready", True):
            return  # already ready (Tesseract etc.), nothing to pre-load

        self.statusBar().showMessage(
            f"Pre-loading {engine_name} model in background…"
            " (first OCR may be slow if not done)")

        def _load():
            try:
                self.engine.warmup()
            except Exception as exc:
                QTimer.singleShot(0, lambda: self.statusBar().showMessage(
                    f"{engine_name} model load failed: {exc}", 6000))
                return
            QTimer.singleShot(0, lambda: self.statusBar().showMessage(
                f"{engine_name} model ready", 3000))

        threading.Thread(target=_load, daemon=True).start()

    def _update_tracking_btn(self) -> None:
        if self._tracking_active:
            self.tracking_btn.setText("■ Stop")
            self.tracking_btn.setToolTip("Stop OCR tracking")
            self.tracking_btn.setStyleSheet(
                "QToolButton { background:#8b2020; color:#fff; border:none;"
                " border-radius:4px; padding:4px 10px; font-size:13px; font-weight:600; }"
                "QToolButton:hover { background:#a02828; }")
        else:
            self.tracking_btn.setText("▶ Start")
            self.tracking_btn.setToolTip("Start OCR tracking")
            self.tracking_btn.setStyleSheet(
                "QToolButton { background:#2e9e54; color:#fff; border-radius:4px;"
                " padding:4px 10px; font-size:13px; font-weight:600; }"
                "QToolButton:hover { background:#37b862; }")
        self.tracking_btn.setChecked(self._tracking_active)

    def _on_monster_selected(self, name: str) -> None:
        if self.panel is not None:
            self.panel.show_monster(name)

    def _show_camera_menu(self) -> None:
        """Render the camera submenu from the cached device list (instant, no I/O)."""
        self._rebuild_camera_menu(self._camera_devices)

    def _populate_camera_menu(self) -> None:
        """Kick off a background device scan; rebuild the menu when done."""
        self.camera_menu.clear()
        self.camera_menu.addAction("Scanning devices…").setEnabled(False)

        def _scan():
            try:
                devices = list_named_devices()
            except Exception:
                devices = []
            self._camera_scan_done.emit(devices)

        threading.Thread(target=_scan, daemon=True).start()

    def _rebuild_camera_menu(self, devices: list) -> None:
        self._camera_devices = devices
        self.camera_menu.clear()
        group = QActionGroup(self.camera_menu)
        group.setExclusive(True)
        for idx, name in devices:
            if "obs virtual camera" in name.lower():
                continue
            act = QAction(f"{name}  (#{idx})", self.camera_menu)
            act.setCheckable(True)
            act.setChecked(idx == self.cfg.capture.device_index)
            act.triggered.connect(lambda _c, i=idx: self._switch_device(i))
            group.addAction(act)
            self.camera_menu.addAction(act)
        self.camera_menu.addSeparator()
        self.camera_menu.addAction("Refresh device list", self._populate_camera_menu)

    def _build_statusbar(self) -> None:
        self._review_label = QLabel()
        self._review_label.setToolTip(
            "Rolling capture buffer — click the input preview to review it")
        self.statusBar().addPermanentWidget(self._review_label)
        self._vcam_label = QLabel()
        self.statusBar().addPermanentWidget(self._vcam_label)
        self._update_vcam_label()

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_F9),  self, activated=self._open_calibration)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self._toggle_browser)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._open_review)
        # Snapshot hotkey is registered system-wide (RegisterHotKey) so it fires
        # even when the app is unfocused — e.g. from a StreamDeck / macro key.
        self._snapshot_hotkey = GlobalHotkey(self._save_snapshot)
        seq = self.cfg.ui.snapshot_hotkey
        if seq and not self._snapshot_hotkey.register(seq):
            # Combination taken by another app (or invalid) — fall back to an
            # in-app shortcut so the menu entry's key still works when focused.
            QShortcut(QKeySequence(seq), self, activated=self._save_snapshot)
            self.statusBar().showMessage(
                f"Could not register global hotkey '{seq}' (in use by another app?)"
                " — snapshot works only while this window has focus", 8000)

    # ================= debug log =================
    def _open_log_file(self):
        """Return an open append-mode file handle for the session log, creating it once.

        Returns None when file logging is disabled in settings."""
        if not self.cfg.logging.to_file:
            return None
        if getattr(self, "_log_fh", None) is None:
            import datetime
            from pathlib import Path
            log_dir = Path(self.cfg._path).parent / "logs" if self.cfg._path else Path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"ocr_{ts}.log"
            self._log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
            self._log_path = log_path
            self.statusBar().showMessage(f"Logging to {log_path}", 4000)
        return self._log_fh

    def _build_debug_panel(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet("QWidget { background:#0d0d12; }")
        lay = QVBoxLayout(container)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        from PyQt6.QtWidgets import QHBoxLayout

        # ── Manual OCR input ────────────────────────────────────────────
        inject_row = QWidget()
        ilay = QHBoxLayout(inject_row)
        ilay.setContentsMargins(0, 0, 0, 0)
        ilay.setSpacing(6)
        inject_lbl = QLabel("Test OCR:")
        inject_lbl.setStyleSheet("color:#9a9aa3; font-size:11px; font-weight:600;")
        ilay.addWidget(inject_lbl)
        self._ocr_input = QLineEdit()
        self._ocr_input.setPlaceholderText(
            "Type monster name(s) to test matching — comma-separated for several…")
        self._ocr_input.setStyleSheet(
            "QLineEdit { background:#1a1a24; color:#c8ffc8; border:1px solid #2a2a36;"
            " border-radius:3px; padding:2px 6px; font-size:11px;"
            " font-family:Consolas,monospace; }")
        self._ocr_input.returnPressed.connect(self._inject_ocr)
        ilay.addWidget(self._ocr_input, 1)
        inject_btn = QPushButton("Inject")
        inject_btn.setFixedWidth(54)
        inject_btn.setStyleSheet(
            "QPushButton { background:#2a2a36; color:#9a9aa3; border:none;"
            " border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#3a3a50; }")
        inject_btn.clicked.connect(self._inject_ocr)
        ilay.addWidget(inject_btn)
        clear_inject_btn = QPushButton("Clear")
        clear_inject_btn.setFixedWidth(42)
        clear_inject_btn.setStyleSheet(
            "QPushButton { background:#2a2a36; color:#9a9aa3; border:none;"
            " border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#3a3a50; }")
        clear_inject_btn.clicked.connect(self._clear_injected)
        ilay.addWidget(clear_inject_btn)
        lay.addWidget(inject_row)

        # ── OCR log ─────────────────────────────────────────────────────
        row = QWidget()
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("OCR Debug Log")
        lbl.setStyleSheet("color:#9a9aa3; font-size:11px; font-weight:600;")
        rlay.addWidget(lbl)
        rlay.addStretch()
        open_btn = QPushButton("Open log")
        open_btn.setFixedWidth(70)
        open_btn.setStyleSheet(
            "QPushButton { background:#2a2a36; color:#9a9aa3; border:none;"
            " border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#3a3a50; }")
        open_btn.clicked.connect(self._open_log_folder)
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(54)
        clear_btn.setStyleSheet(
            "QPushButton { background:#2a2a36; color:#9a9aa3; border:none;"
            " border-radius:3px; padding:2px 6px; font-size:11px; }"
            "QPushButton:hover { background:#3a3a50; }")
        rlay.addWidget(open_btn)
        rlay.addWidget(clear_btn)
        lay.addWidget(row)

        self._debug_log = QPlainTextEdit()
        self._debug_log.setReadOnly(True)
        self._debug_log.setMaximumBlockCount(200)
        self._debug_log.setStyleSheet(
            "QPlainTextEdit { background:#0d0d12; color:#c8ffc8;"
            " font-family: Consolas, monospace; font-size:11px; border:none; }")
        self._debug_log.setFixedHeight(110)
        clear_btn.clicked.connect(self._debug_log.clear)
        lay.addWidget(self._debug_log)
        return container

    def _inject_ocr(self) -> None:
        """Feed the typed text through the OCR matching pipeline and show the result.

        Several objects can be tested at once by separating them with commas,
        semicolons or newlines ("anjanath, rathalos, azuros"); each term is
        matched on its own and every hit is injected as one detection set, so
        the multi-card view can be exercised without a live capture."""
        from ..battle import match_known
        raw = self._ocr_input.text().strip()
        if not raw:
            return
        known = self.cfg.monster_name_list
        cutoff = self.cfg.ocr.match_cutoff
        terms = [t.strip() for t in re.split(r"[,;\n]+", raw) if t.strip()]
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")

        matched: list = []   # (name, 1.0), deduped, in typed order
        missed: list = []
        for term in terms:
            hit = match_known(term, known, cutoff=cutoff)
            self._log_line(
                f"[{ts}] inject:  raw={term!r}  →  "
                + (f"matched={hit!r}" if hit else f"no match (cutoff={cutoff})"))
            if hit is None:
                missed.append(term)
            elif hit not in [n for n, _ in matched]:
                matched.append((hit, 1.0))

        if matched:
            self._on_monsters_changed(matched)
        if missed:
            self.statusBar().showMessage(
                f"No match for {', '.join(repr(m) for m in missed)}"
                f" (cutoff {cutoff})", 3000)

    def _clear_injected(self) -> None:
        """Remove injected monsters and return to the idle state."""
        self._ocr_input.clear()
        self._on_monsters_changed([])

    def _open_log_folder(self) -> None:
        import subprocess
        from pathlib import Path
        log_fh = getattr(self, "_log_fh", None)
        if log_fh:
            subprocess.Popen(["explorer", "/select,", str(self._log_path)])
        else:
            from pathlib import Path
            log_dir = Path(self.cfg._path).parent / "logs" if self.cfg._path else Path("logs")
            subprocess.Popen(["explorer", str(log_dir)])

    def _on_ocr_error(self, msg: str) -> None:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.statusBar().showMessage(msg.splitlines()[0], 5000)
        try:
            fh = self._open_log_file()
            if fh:
                fh.write(f"[{ts}] ERROR: {msg}\n")
        except Exception:
            pass

    def _toggle_debug(self, visible: bool) -> None:
        self._debug_widget.setVisible(visible)
        # Keep the PiP preview clear of the debug panel (and its buttons).
        self._preview.set_bottom_inset(
            self._debug_widget.sizeHint().height() if visible else 0)

    def _toggle_preview(self, visible: bool) -> None:
        self._preview.setVisible(visible)
        if visible:
            self._preview.raise_()
        self.cfg.ui.show_input_preview = visible
        self.cfg.save()

    def _on_preview_resized(self, width: int) -> None:
        """Persist a drag-resize of the PiP (emitted once, on mouse release)."""
        self.cfg.ui.preview_width = int(width)
        self.cfg.save()

    # ================= controller button map =================
    def _sync_controller_map_buttons(self) -> None:
        """Point the menu item and the toolbar button at the stored state
        without re-entering their own toggled/clicked slots."""
        for widget in (self.act_ctrl_map, self.ctrl_map_btn):
            widget.blockSignals(True)
            widget.setChecked(self.cfg.ui.show_controller_map)
            widget.blockSignals(False)

    def _sync_controller_map(self) -> None:
        """The overlay belongs to the tracking view only — never the Grimoire,
        never over the review screen. Every view change funnels through here."""
        show = (self.cfg.ui.show_controller_map
                and not self._grimoire_shown
                and getattr(self, "_review", None) is None)
        self._ctrl_map.setVisible(show)
        if show:
            self._ctrl_map.raise_()

    def _set_controller_map_enabled(self, on: bool) -> None:
        """Single entry point for the two toggles, so they can't drift apart."""
        self.cfg.ui.show_controller_map = bool(on)
        self.cfg.save()
        self._sync_controller_map_buttons()
        self._sync_controller_map()

    def _set_controller_pad(self, side: str, pad_id: str) -> None:
        if side == "left":
            self.cfg.ui.controller_map_left = pad_id
        else:
            self.cfg.ui.controller_map_right = pad_id
        self.cfg.save()
        self._ctrl_map.set_pair(self.cfg.ui.controller_map_left,
                                self.cfg.ui.controller_map_right)

    def _on_ctrl_map_geometry(self, fx: float, fy: float, width: int) -> None:
        """Persist a move or resize of the overlay (once, on mouse release)."""
        self.cfg.ui.controller_map_x = float(fx)
        self.cfg.ui.controller_map_y = float(fy)
        self.cfg.ui.controller_map_width = int(width)
        self.cfg.save()

    def _on_ctrl_map_collapsed(self, collapsed: bool) -> None:
        self.cfg.ui.controller_map_collapsed = bool(collapsed)
        self.cfg.save()

    def _toggle_file_logging(self, enabled: bool) -> None:
        self.cfg.logging.to_file = enabled
        self.cfg.save()
        if not enabled:
            # Stop writing and release the current session log.
            fh = getattr(self, "_log_fh", None)
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
            self._log_fh = None
            self._log_path = None

    def _log_line(self, line: str) -> None:
        """Write one line to both the on-screen widget and the log file."""
        self._debug_log.appendPlainText(line)
        try:
            fh = self._open_log_file()
            if fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def _on_debug_text(self, raw: str, monsters: list) -> None:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        matched = ", ".join(monsters) if monsters else "—"
        self._log_line(f"[{ts}]  raw:     {raw}")
        self._log_line(f"         matched: {matched}")

    # ================= ocr engine =================
    def _toggle_gpu(self, checked: bool) -> None:
        """Switch OCR between GPU and CPU: rebuild the engine and restart the worker."""
        self.cfg.ocr.gpu = checked
        self.cfg.save()
        self.engine = build_engine(self.cfg.ocr.engine, self.cfg.ocr.languages, checked)
        self._start_worker()
        self.statusBar().showMessage(
            f"OCR now using {'GPU' if checked else 'CPU'} (model reloads on first read)", 4000)

    # ================= camera =================
    def _retry_camera(self) -> None:
        """Force a fresh open of the current device (e.g. after another app released it)."""
        self.nav.set_source("Connecting…", None)
        self.statusBar().showMessage("Reconnecting camera…", 2000)
        self._camera_ok = True
        self._last_seq = -1
        self._start_capture(self.cfg.capture.device_index)

    def _switch_device(self, device_index: int) -> None:
        self.cfg.capture.device_index = device_index
        self.cfg.capture.video_file = None
        self.cfg.save()
        self._start_capture(device_index)

    def _start_capture(self, device_index: int) -> None:
        if self.capture is not None:
            self.capture.stop()
            self.capture.join(timeout=2.0)
        if self.vcam:
            self.vcam.close()
        self.capture = CaptureThread(
            device_index=device_index,
            width=self.cfg.capture.width, height=self.cfg.capture.height,
            fps=self.cfg.capture.fps,
            buffer=self.buffer,
            on_frame=self._capture_sink,
            video_file=self.cfg.capture.video_file,
        )
        self.capture.start()
        # Reset flow-detection state so the debounce starts fresh for this device.
        self._last_seq = self.buffer.current_seq()
        self._last_flowing = False
        self._flow_streak = 0
        self._camera_ok = False
        src = self.cfg.capture.video_file or f"device {device_index}"
        self.statusBar().showMessage(f"Capturing from {src}", 3000)

    # ================= always on top =================
    def _toggle_on_top(self, checked: bool) -> None:
        self.cfg.ui.always_on_top = checked
        self.cfg.save()
        self._apply_on_top(checked)

    def _apply_on_top(self, checked: bool) -> None:
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # ================= view mode (auto switch + manual grimoire) =================
    def _on_auto_switch_toggled(self, on: bool) -> None:
        self._auto_switch = on
        if on:
            # Re-evaluate the view from the current detections.
            if self._detections:
                self._cancel_idle()
                self._set_grimoire(False)
            else:
                self._start_idle()
        else:
            # Manual mode: stop any pending auto-revert and leave the view as-is.
            self._cancel_idle()

    def _toggle_grimoire_view(self) -> None:
        """Manually show/hide the Grimoire view. Independent of Auto Switch."""
        self._cancel_idle()
        self._set_grimoire(not self._grimoire_shown)

    def _set_min_confidence(self, level: str) -> None:
        self.cfg.ocr.min_confidence_level = level
        self.cfg.save()
        self.statusBar().showMessage(f"Tracking confidence: {level} and up", 3000)

    # ================= fullscreen =================
    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self.act_fullscreen.setChecked(self.isFullScreen())

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowStateChange:
            self.act_fullscreen.setChecked(self.isFullScreen())

    def _update_vcam_label(self) -> None:
        if not self.vcam:
            self._vcam_label.setText("Virtual cam: off")
        elif self.vcam.device_name:
            self._vcam_label.setText(f"Virtual cam: {self.vcam.device_name}")
        elif self.vcam.last_error:
            self._vcam_label.setText("Virtual cam: error")
        else:
            self._vcam_label.setText("Virtual cam: starting…")

    # ================= loops / signals =================
    def _refresh_status(self) -> None:
        self._update_vcam_label()
        self._update_review_status()
        seq = self.buffer.current_seq()
        flowing = seq != self._last_seq
        self._last_seq = seq

        # Debounce: _flow_streak counts consecutive ticks of the same `flowing`
        # value. Only update _camera_ok after 2 in a row so a single dropped/
        # spurious frame doesn't flip the pill.
        if flowing == getattr(self, "_last_flowing", None):
            self._flow_streak = min(self._flow_streak + 1, 4)
        else:
            self._flow_streak = 1
        self._last_flowing = flowing
        if self._flow_streak >= 2:
            self._camera_ok = flowing

        err = self.capture.last_error if self.capture else None
        if self._camera_ok:
            self.nav.set_source("Source Active", True)
            if self._tracking_active:
                self.nav.set_tracking("Tracking", True)
            else:
                self.nav.set_tracking("Stopped", False)
        else:
            if err:
                self.nav.set_source("No Source", False)
                self.statusBar().showMessage(f"Camera: {err}", 2000)
            else:
                self.nav.set_source("Connecting…", None)
            self.nav.set_tracking("Stopped" if not self._tracking_active else "Idle", None)

    def _refresh_panel(self) -> None:
        # navbar shows the confidence-coloured buttons; the panel shows the page for
        # all detected monsters at once (source/tracking pills come from _refresh_status).
        self.nav.set_monsters(self._detections)
        if self.panel:
            self.panel.show_monsters([n for n, _ in self._detections])

    def _on_battle_started(self) -> None:
        self.model.battle_started()
        self._refresh_panel()

    def _on_battle_ended(self) -> None:
        self.model.battle_ended()
        self._refresh_panel()

    def _on_monsters_changed(self, detections: list) -> None:
        # detections = [(name, confidence)]
        self._detections = detections
        names = [n for n, _ in detections]
        self.model.set_monsters(names)
        self._refresh_panel()
        if not self._auto_switch:
            return  # locked to Grimoire; navbar still updates, view doesn't switch
        if names:
            self._cancel_idle()
            # monsters detected — switch back to OCR view
            self._set_grimoire(False)
        else:
            self._start_idle()

    def _on_monster_killed(self, name: str) -> None:
        self.model.remove_monster(name)
        self._refresh_panel()
        if self._auto_switch and not self.model.monsters:
            self._start_idle()

    # ================= idle / auto-switch =================
    @property
    def _idle_timeout(self) -> int:
        """Seconds with no monster before switching to Grimoire (ui.idle_switch_s)."""
        return max(1, int(getattr(self.cfg.ui, "idle_switch_s", 60)))

    def _start_idle(self) -> None:
        if not self._idle_timer.isActive():
            self._idle_secs = 0
            self._idle_timer.start()
            self._update_countdown()

    def _cancel_idle(self) -> None:
        self._idle_timer.stop()
        self._idle_secs = 0
        if self.panel:
            self.panel.set_countdown(None)

    def _on_idle_tick(self) -> None:
        self._idle_secs += 1
        if self._idle_secs >= self._idle_timeout:
            self._idle_timer.stop()
            if self.panel:
                self.panel.set_countdown(None)
            if self._auto_switch:
                self._set_grimoire(True)
        else:
            self._update_countdown()

    def _update_countdown(self) -> None:
        if self.panel:
            remaining = self._idle_timeout - self._idle_secs
            self.panel.set_countdown(remaining)

    def _set_grimoire(self, visible: bool) -> None:
        self._grimoire_shown = visible
        if self.panel:
            self.panel.set_grimoire_visible(visible)
        self._update_grimoire_btn()
        self._sync_controller_map()

    def _update_grimoire_btn(self) -> None:
        """Reflect the current view in the manual view button: the icon shows
        where you are, the tooltip says where a click takes you."""
        if not hasattr(self, "grimoire_btn"):
            return
        self.grimoire_btn.setChecked(self._grimoire_shown)
        if self._grimoire_shown:
            self.grimoire_btn.setText("📖")
            self.grimoire_btn.setToolTip("Grimoire view — click for Tracking view")
        else:
            self.grimoire_btn.setText("🎯")
            self.grimoire_btn.setToolTip("Tracking view — click for Grimoire view")

    # ================= calibration =================
    def _open_calibration(self) -> None:
        frame, _ = self.buffer.get()
        if frame is None:
            QMessageBox.information(self, "Calibrate", "No frame captured yet.")
            return
        dlg = CalibrateDialog(self.cfg, frame, self)
        if dlg.exec():
            # CalibrateDialog updated the active regions; persist them for this game.
            # The worker reads the live OcrConfig, so changes apply without a restart.
            if self.cfg.selected_game:
                gs = GameSettings(
                    monster_names=self.cfg.ocr.regions_monster_names,
                    battle_end=self.cfg.ocr.regions_battle_end,
                    end_keywords=self.cfg.ocr.keywords_battle_end,
                )
                self.cfg.set_regions_for(self.cfg.selected_game, gs)
            self.cfg.save()
            self.statusBar().showMessage("Regions saved", 2000)

    # ================= shutdown =================
    def closeEvent(self, event) -> None:
        # Persist the browser split ratio (kept in memory while the user drags;
        # only written to config here, once, on exit).
        if self.browser.isVisible():
            sizes = self._splitter.sizes()
            if sizes[1] > 0:
                self._main_ratio = sizes[0] / (sizes[0] + sizes[1])
        if self._main_ratio != self.cfg.ui.browser_split_ratio:
            self.cfg.ui.browser_split_ratio = self._main_ratio
            self.cfg.save()
        if getattr(self, "_snapshot_hotkey", None):
            self._snapshot_hotkey.unregister()
        # Stop the recorder before capture, so no frame arrives for a buffer
        # whose segment files have already been removed.
        self._stop_recorder(confirm=False)
        if self.worker:
            self.worker.stop()
            self.worker.wait(1500)
        if self.capture:
            self.capture.stop()
            self.capture.join(timeout=1.5)
        if self.vcam:
            self.vcam.close()
        if getattr(self, "_log_fh", None):
            self._log_fh.close()
        super().closeEvent(event)
