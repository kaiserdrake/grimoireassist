"""Main window: capture + virtual cam (global) and a per-game OCR worker + panel.

Camera, calibration, always-on-top and game switching live behind a burger menu.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QLabel, QMainWindow, QMenu, QMessageBox, QToolBar, QToolButton,
)

from ..battle import OcrWorker
from ..capture import CaptureThread, FrameBuffer, list_named_devices
from ..config import Config
from ..games import GameInfo, get_game, default_game, load_catalog, monster_names, slug_map
from ..ocr import build_engine
from ..overlay import OverlayModel
from ..virtualcam import VirtualCamSink
from .calibrate import CalibrateDialog
from .game_select import GameSelectDialog
from .monster_panel import MonsterPanel


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.resize(440, 600)

        self.model = OverlayModel()
        self.buffer = FrameBuffer()
        self.vcam = VirtualCamSink(fps=cfg.capture.fps) if cfg.virtual_camera.enabled else None
        self.engine = build_engine(cfg.ocr.engine, cfg.ocr.languages, cfg.ocr.gpu)

        self.capture: Optional[CaptureThread] = None
        self.worker: Optional[OcrWorker] = None
        self.panel: Optional[MonsterPanel] = None
        self.game: Optional[GameInfo] = None

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

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)

    # ================= per-game lifecycle =================
    def _start_game(self, game: Optional[GameInfo]) -> None:
        if game is None:
            self.setWindowTitle("GrimoireAssist")
            return
        self.game = game
        self.cfg.selected_game = game.id
        self.cfg.monster_name_list = monster_names(game.monsters)
        self.cfg.ocr.regions_monster_names = self.cfg.regions_for(game.id)
        self.cfg.save()
        self.setWindowTitle(f"GrimoireAssist — {game.name}")

        # (re)build panel for this game's site + slugs
        self.model = OverlayModel()
        if self.cfg.ocr.continuous:
            self.model.status_text = "Watching OCR area…"
        self.panel = MonsterPanel(game.site_url_template, slug_map=slug_map(game.monsters))
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
        self.menu.addAction("Calibrate regions…\tF9", self._open_calibration)
        self.act_on_top = self.menu.addAction("Always on top")
        self.act_on_top.setCheckable(True)
        self.act_on_top.setChecked(self.cfg.ui.always_on_top)
        self.act_on_top.toggled.connect(self._toggle_on_top)
        self.menu.addSeparator()
        self.menu.addAction("Switch game…", self._switch_game)
        self.menu.aboutToShow.connect(self._populate_camera_menu)

        self.menu_btn.setMenu(self.menu)
        tb.addWidget(self.menu_btn)

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
        QShortcut(QKeySequence(Qt.Key.Key_F9), self, activated=self._open_calibration)

    # ================= camera =================
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
        if self.capture and self.capture.last_error:
            self.statusBar().showMessage(self.capture.last_error, 2000)

    def _refresh_panel(self) -> None:
        if self.panel is None:
            return
        self.panel.set_status(self.model.status_text, self.model.in_battle)
        self.panel.set_monsters(self.model.monsters)

    def _on_battle_started(self) -> None:
        self.model.battle_started()
        self._refresh_panel()

    def _on_battle_ended(self) -> None:
        self.model.battle_ended()
        self._refresh_panel()

    def _on_monsters_changed(self, names: List[str]) -> None:
        self.model.set_monsters(names)
        if self.cfg.ocr.continuous:
            self.model.in_battle = bool(names)
            self.model.status_text = (
                f"{len(names)} detected" if names else "Watching OCR area…")
        self._refresh_panel()

    def _on_monster_killed(self, name: str) -> None:
        self.model.remove_monster(name)
        self._refresh_panel()

    # ================= calibration =================
    def _open_calibration(self) -> None:
        frame, _ = self.buffer.get()
        if frame is None:
            QMessageBox.information(self, "Calibrate", "No frame captured yet.")
            return
        dlg = CalibrateDialog(self.cfg, frame, self)
        if dlg.exec():
            # CalibrateDialog updated cfg.ocr.regions_monster_names (active);
            # persist them under the current game. The worker reads the live list.
            if self.cfg.selected_game:
                self.cfg.set_regions_for(self.cfg.selected_game,
                                         self.cfg.ocr.regions_monster_names)
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
