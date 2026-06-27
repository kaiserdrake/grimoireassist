"""Main window: capture + virtual cam (global) and a per-game OCR worker + panel.

Camera, calibration, always-on-top and game switching live behind a burger menu.
"""
from __future__ import annotations

from typing import List, Optional

import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QSizePolicy, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from ..battle import OcrWorker
from ..capture import CaptureThread, FrameBuffer, list_named_devices
from ..config import Config, GameSettings
from ..games import (
    GameInfo, get_game, default_game, icon_path, import_dir, load_catalog,
    monster_names, monster_imported_data, save_game, slug_map,
    write_default_settings,
)
from ..ocr import build_engine
from ..overlay import OverlayModel
from ..virtualcam import VirtualCamSink
from .calibrate import CalibrateDialog
from .game_select import GameSelectDialog
from .import_wizard import ImportWizard
from .monster_panel import MonsterNav, MonsterPanel, ViewModeSwitch


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
        self.engine = build_engine(cfg.ocr.engine, cfg.ocr.languages, cfg.ocr.gpu)

        self.capture: Optional[CaptureThread] = None
        self.worker: Optional[OcrWorker] = None
        self.panel: Optional[MonsterPanel] = None
        self.game: Optional[GameInfo] = None
        self._auto_switch = True            # Auto Switch vs Grimoire-locked
        self._detections: list = []         # latest [(name, confidence)]
        self._tracking_active = False       # OCR worker only runs when user starts it

        self._camera_devices: list = []

        self._build_menu()
        self._build_statusbar()
        self._build_shortcuts()
        self._debug_widget = self._build_debug_panel()
        self._debug_widget.setVisible(False)

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
        # Panel is built by _start_game above, so we can start counting right away.
        self._start_idle()
        self.showMaximized()

    # ================= per-game lifecycle =================
    def _start_game(self, game: Optional[GameInfo]) -> None:
        if game is None:
            self.setWindowTitle("GrimoireAssist")
            return
        self.game = game
        self.cfg.selected_game = game.id
        self.cfg.monster_name_list = monster_names(game.id, self.cfg._path)
        gs = self.cfg.regions_for(game.id)
        self.cfg.ocr.regions_monster_names = gs.monster_names
        self.cfg.ocr.regions_battle_end = gs.battle_end
        self.cfg.ocr.keywords_battle_end = gs.end_keywords
        self.cfg.save()
        self.setWindowTitle(f"GrimoireAssist — {game.name}")

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
        self.setCentralWidget(wrapper)
        self._refresh_panel()

        # Only (re)start the OCR worker if tracking was already active.
        # On first load _tracking_active is False, so we wait for the user
        # to press Start before burning CPU on inference.
        if self._tracking_active:
            self._start_worker()
        else:
            self._stop_worker()

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
        self.worker.start()

    def _stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(1500)
            self.worker = None
        self._detections = []
        self.model = OverlayModel()
        self._refresh_panel()

    def _toggle_tracking(self) -> None:
        self._tracking_active = not self._tracking_active
        if self._tracking_active:
            self._start_worker()
        else:
            self._stop_worker()
        self._update_tracking_btn()

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

        # ── OCR ─────────────────────────────────────────────────
        self.menu.addSection("OCR")
        self.act_gpu = self.menu.addAction("Use GPU")
        self.act_gpu.setCheckable(True)
        self.act_gpu.setChecked(self.cfg.ocr.gpu)
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

        # ── Game ─────────────────────────────────────────────────
        self.menu.addSection("Game")
        self.menu.addAction("Add game…", self._add_game)
        self.menu.addAction("Switch game…", self._switch_game)
        self.menu.addAction("Import monster data…", self._open_import_wizard)

        # ── Window ───────────────────────────────────────────────
        self.menu.addSection("Window")
        self.act_on_top = self.menu.addAction("Always on top")
        self.act_on_top.setCheckable(True)
        self.act_on_top.setChecked(self.cfg.ui.always_on_top)
        self.act_on_top.toggled.connect(self._toggle_on_top)
        self.act_fullscreen = self.menu.addAction("Fullscreen\tF11")
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.triggered.connect(self._toggle_fullscreen)

        # ── Debug ────────────────────────────────────────────────
        self.menu.addSection("Debug")
        self.act_debug = self.menu.addAction("Show OCR debug log")
        self.act_debug.setCheckable(True)
        self.act_debug.setChecked(False)
        self.act_debug.toggled.connect(self._toggle_debug)

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

        # View-mode switch: Auto Switch (auto transition) vs Grimoire (locked).
        self.view_switch = ViewModeSwitch()
        self.view_switch.set_mode("auto" if self._auto_switch else "grimoire")
        self.view_switch.mode_changed.connect(self._on_view_mode)
        tb.addWidget(self.view_switch)

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
        self._vcam_label = QLabel()
        self.statusBar().addPermanentWidget(self._vcam_label)
        self._update_vcam_label()

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_F9),  self, activated=self._open_calibration)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self._toggle_fullscreen)

    # ================= debug log =================
    def _open_log_file(self):
        """Return an open append-mode file handle for the session log, creating it once."""
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
        self._ocr_input.setPlaceholderText("Type monster name to test matching…")
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
        """Feed the typed text through the OCR matching pipeline and show the result."""
        from ..battle import match_known
        raw = self._ocr_input.text().strip()
        if not raw:
            return
        known = self.cfg.monster_name_list
        cutoff = self.cfg.ocr.match_cutoff
        matched = match_known(raw, known, cutoff=cutoff)
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if matched:
            self._log_line(f"[{ts}] inject:  raw={raw!r}  →  matched={matched!r}")
            self._on_monsters_changed([(matched, 1.0)])
        else:
            self._log_line(f"[{ts}] inject:  raw={raw!r}  →  no match (cutoff={cutoff})")
            self.statusBar().showMessage(
                f"No match for {raw!r} (cutoff {cutoff})", 3000)

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
            self._open_log_file().write(f"[{ts}] ERROR: {msg}\n")
        except Exception:
            pass

    def _toggle_debug(self, visible: bool) -> None:
        self._debug_widget.setVisible(visible)

    def _log_line(self, line: str) -> None:
        """Write one line to both the on-screen widget and the log file."""
        self._debug_log.appendPlainText(line)
        try:
            self._open_log_file().write(line + "\n")
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
            on_frame=(self.vcam.send if self.vcam else None),
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

    # ================= view mode (auto-switch vs grimoire) =================
    def _on_view_mode(self, mode: str) -> None:
        self._auto_switch = (mode == "auto")
        if mode == "grimoire":
            # lock to the Grimoire view; no auto transitions
            self._cancel_idle()
            self._set_grimoire(True)
        else:
            # auto: re-evaluate the view from the current detections
            if self._detections:
                self._cancel_idle()
                self._set_grimoire(False)
            else:
                self._start_idle()

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
        if not self.model.monsters:
            self._start_idle()

    # ================= idle / auto-switch =================
    _IDLE_TIMEOUT = 4

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
        if self._idle_secs >= self._IDLE_TIMEOUT:
            self._idle_timer.stop()
            if self.panel:
                self.panel.set_countdown(None)
            if self._auto_switch:
                self._set_grimoire(True)
        else:
            self._update_countdown()

    def _update_countdown(self) -> None:
        if self.panel:
            remaining = self._IDLE_TIMEOUT - self._idle_secs
            self.panel.set_countdown(remaining)

    def _set_grimoire(self, visible: bool) -> None:
        if self.panel:
            self.panel.set_grimoire_visible(visible)

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
