"""Minimap tab: the enlarged live view of the minimap region, its calibration
slot and settings, and the Monster | Minimap tab strip.

Runs on Qt's offscreen platform, so it needs no display.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from grimoireassist.capture import FrameBuffer
from grimoireassist.config import (Config, GameSettings, Region,
                                   _load_game_settings, _save_game_settings)
from grimoireassist.ui.calibrate import CalibrateDialog
from grimoireassist.ui.minimap_view import MinimapView

MainWindow = pytest.importorskip("grimoireassist.ui.main_window").MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _frame():
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[20:120, 30:130] = (0, 0, 255)          # the "minimap": red square
    return frame


# ---- the view ------------------------------------------------------------
def test_crop_is_clipped_to_the_frame():
    frame = _frame()
    assert MinimapView.crop(frame, Region(30, 20, 100, 100)).shape == (100, 100, 3)
    assert MinimapView.crop(frame, Region(600, 300, 100, 100)).shape == (60, 40, 3)
    assert MinimapView.crop(frame, Region(700, 400, 50, 50)) is None


def test_the_region_is_drawn_enlarged_and_centred(app):
    buf = FrameBuffer()
    region = Region(30, 20, 100, 100)
    view = MinimapView(buf, lambda: region)
    view.resize(400, 300)
    buf.set(_frame())
    view._tick()
    assert view._pixmap is not None and view._pixmap.width() == 100
    rect = view.target_rect()
    assert rect.width() == rect.height() == 300          # scaled up 3x, square kept
    assert rect.left() == 50                             # centred
    assert view._pixmap.toImage().pixelColor(50, 50).red() == 255


def test_no_region_or_no_frame_shows_a_hint(app):
    buf = FrameBuffer()
    region = Region()
    view = MinimapView(buf, lambda: region)
    view._tick()
    assert view._pixmap is None
    region.x, region.y, region.w, region.h = 30, 20, 100, 100   # recalibrated live
    buf.set(_frame())
    view._tick()
    assert view._pixmap is not None


def test_polls_only_while_shown(app):
    view = MinimapView(FrameBuffer(), lambda: Region(0, 0, 10, 10))
    assert not view._timer.isActive()
    view.show()
    assert view._timer.isActive()
    view.hide()
    assert not view._timer.isActive()


# ---- the region ----------------------------------------------------------
def test_minimap_region_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    _save_game_settings(path, GameSettings(minimap=Region(1, 2, 300, 200)))
    assert _load_game_settings(path).minimap == Region(1, 2, 300, 200)
    assert _load_game_settings(path).labels == []


def test_calibrate_has_a_minimap_slot(app):
    cfg = Config()
    cfg.ocr.regions_minimap = Region(5, 5, 50, 50)
    dlg = CalibrateDialog(cfg, np.zeros((360, 640, 3), np.uint8))
    assert dlg.canvas.regions["minimap"] == Region(5, 5, 50, 50)
    dlg.canvas.regions["minimap"] = Region(7, 7, 70, 70)
    dlg._save()
    assert cfg.ocr.regions_minimap == Region(7, 7, 70, 70)


# ---- the tabs ------------------------------------------------------------
class _Win:
    """Just what _build_tracking_tabs touches."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.buffer = FrameBuffer()
        self.panel = QWidget()
        self.saves = 0
        cfg.save = self._save

    def _save(self):
        self.saves += 1

    _build_tracking_tabs = MainWindow._build_tracking_tabs
    _on_tracking_tab_changed = MainWindow._on_tracking_tab_changed


def test_tabs_remember_the_minimap_tab(app):
    win = _Win(Config())
    win.box = win._build_tracking_tabs()
    assert win._tracking_tabs.currentIndex() == 0
    win._tracking_tabs.setCurrentIndex(1)
    assert win.cfg.ui.tracking_tab == "minimap" and win.saves
    win2 = _Win(win.cfg)
    win2.box = win2._build_tracking_tabs()
    assert win2._tracking_tabs.currentIndex() == 1


def test_tracking_tab_round_trips_and_rejects_junk():
    assert Config().ui.tracking_tab == "monster"
    assert Config.from_dict({"ui": {"tracking_tab": "Minimap"}}).ui.tracking_tab == "minimap"
    assert Config.from_dict({"ui": {"tracking_tab": "x"}}).ui.tracking_tab == "monster"
