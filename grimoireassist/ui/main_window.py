"""Main window: capture + virtual cam (global) and a per-game OCR worker + panel.

Camera, calibration, always-on-top and game switching live behind a burger menu.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QLabel, QMainWindow, QMenu, QMessageBox, QSizePolicy, QToolBar, QToolButton,
    QWidget,
)

from ..battle import OcrWorker
from ..capture import CaptureThread, FrameBuffer, list_named_devices
from ..config import Config, GameSettings
from ..games import (
    GameInfo, get_game, default_game, icon_path, load_catalog, monster_names, slug_map,
)
from ..ocr import build_engine
from ..overlay import OverlayModel
from ..virtualcam import VirtualCamSink
from .calibrate import CalibrateDialog
from .game_select import GameSelectDialog
from .monster_panel import MonsterNav, MonsterPanel, ViewModeSwitch


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.resize(440, 600)
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

        self._build_menu()
        self._build_statusbar()
        self._build_shortcuts()

        # capture is global (one camera feeds every game)
        self._start_capture(cfg.capture.device_index)

        # load the selected game (panel + worker)
        game = get_game(cfg.selected_game) or default_game()
        self._start_game(game)

        if cfg.ui.always_on_top:
            self._apply_on_top(True)

        # camera-health tracking (drives the error status)
        self._last_seq = -1
        self._camera_ok = True

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)

        self._idle_secs: int = 0          # seconds elapsed with no monsters
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(1000)
        self._idle_timer.timeout.connect(self._on_idle_tick)
        # Panel is built by _start_game above, so we can start counting right away.
        self._start_idle()

    # ================= per-game lifecycle =================
    def _start_game(self, game: Optional[GameInfo]) -> None:
        if game is None:
            self.setWindowTitle("GrimoireAssist")
            return
        self.game = game
        self.cfg.selected_game = game.id
        self.cfg.monster_name_list = monster_names(game.monsters)
        gs = self.cfg.regions_for(game.id)
        self.cfg.ocr.regions_monster_names = gs.monster_names
        self.cfg.ocr.regions_battle_end = gs.battle_end
        self.cfg.ocr.keywords_battle_end = gs.end_keywords
        self.cfg.save()
        self.setWindowTitle(f"GrimoireAssist — {game.name}")

        # (re)build panel for this game's site + slugs
        self.model = OverlayModel()
        self._detections = []
        self.panel = MonsterPanel(
            game.site_url_template, slug_map=slug_map(game.monsters),
            url_style=game.url_style, multi_joiner=game.multi_joiner,
            requires_login=game.requires_login, notes_url=game.notes_url)
        self.setCentralWidget(self.panel)
        self._refresh_panel()

        # (re)start OCR worker with the new game's name list / regions
        self._start_worker()

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
        self.worker.error.connect(lambda m: self.statusBar().showMessage(m, 5000))
        self.worker.start()

    def _switch_game(self) -> None:
        dlg = GameSelectDialog(list(load_catalog()),
                               current=self.cfg.selected_game, parent=self)
        if dlg.exec() and dlg.selected and dlg.selected != self.cfg.selected_game:
            self._start_game(get_game(dlg.selected))

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
        self.camera_menu = self.menu.addMenu("Camera")
        self.menu.addAction("Retry camera", self._retry_camera)
        self.menu.addAction("Calibrate regions…\tF9", self._open_calibration)
        self.act_gpu = self.menu.addAction("Use GPU for OCR")
        self.act_gpu.setCheckable(True)
        self.act_gpu.setChecked(self.cfg.ocr.gpu)
        self.act_gpu.toggled.connect(self._toggle_gpu)
        # minimum OCR confidence required to track a monster
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
        self.act_on_top = self.menu.addAction("Always on top")
        self.act_on_top.setCheckable(True)
        self.act_on_top.setChecked(self.cfg.ui.always_on_top)
        self.act_on_top.toggled.connect(self._toggle_on_top)
        self.act_fullscreen = self.menu.addAction("Fullscreen\tF11")
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.triggered.connect(self._toggle_fullscreen)
        self.menu.addSeparator()
        self.menu.addAction("Switch game…", self._switch_game)
        self.menu.aboutToShow.connect(self._populate_camera_menu)

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

        # View-mode switch: Auto Switch (auto transition) vs Grimoire (locked).
        self.view_switch = ViewModeSwitch()
        self.view_switch.set_mode("auto" if self._auto_switch else "grimoire")
        self.view_switch.mode_changed.connect(self._on_view_mode)
        tb.addWidget(self.view_switch)

    def _on_monster_selected(self, name: str) -> None:
        if self.panel is not None:
            self.panel.show_monster(name)

    def _populate_camera_menu(self) -> None:
        self.camera_menu.clear()
        group = QActionGroup(self.camera_menu)
        group.setExclusive(True)
        for idx, name in list_named_devices():
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
        # Detect whether frames are actually arriving from the camera, and reflect
        # source + tracking state in the navbar pills.
        seq = self.buffer.current_seq()
        flowing = seq != self._last_seq
        self._last_seq = seq
        err = self.capture.last_error if self.capture else None
        if flowing:
            self.nav.set_source("Source Active", True)
            self.nav.set_tracking("Tracking", self.worker is not None)
            self._camera_ok = True
        else:
            self._camera_ok = False
            if err:
                self.nav.set_source("No Source", False)
                self.statusBar().showMessage(f"Camera: {err}", 2000)
            else:
                self.nav.set_source("Connecting…", None)
            self.nav.set_tracking("Idle", None)

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
    _IDLE_TIMEOUT = 16

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
        super().closeEvent(event)
