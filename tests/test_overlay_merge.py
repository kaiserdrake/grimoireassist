"""Merging the PiP and the controller map into one tabbed panel.

The panel is the controller-map overlay with the preview docked into it as a
second page: one frame, one position, one width, two tabs. These cover the
docking contract — who owns geometry, what the tabs do, and that separating
puts the preview back the way it was.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from grimoireassist.capture import FrameBuffer
from grimoireassist.config import Config
from grimoireassist.ui.controller_overlay import (INPUT_TAB, PAD_TAB,
                                                  ControllerMapOverlay)
from grimoireassist.ui.preview import InputPreview


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app):
    widget = QWidget()
    widget.resize(900, 600)
    widget.show()
    yield widget
    widget.close()


@pytest.fixture
def merged(host):
    """A panel with the preview docked in as its Input page."""
    panel = ControllerMapOverlay(host, width=420, fx=0.0, fy=0.0)
    pip = InputPreview(FrameBuffer(), fps=10.0, parent=host, width=240)
    panel.attach_guest(pip)
    panel.show()
    return panel, pip


def _click(widget, pos: QPointF) -> None:
    widget.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def _tab_point(panel, key: str) -> QPointF:
    rect = dict(panel._tab_rects())[key]
    return QPointF(rect.center())


# ---- docking ------------------------------------------------------------
def test_attaching_takes_the_preview_in(merged):
    panel, pip = merged
    assert panel.has_guest()
    assert pip.parentWidget() is panel
    assert pip.is_docked()
    assert [k for k, _ in panel.tabs()] == [PAD_TAB, INPUT_TAB]


def test_detaching_hands_it_back(merged, host):
    panel, pip = merged
    returned = panel.detach_guest(host)
    assert returned is pip
    assert not panel.has_guest()
    assert pip.parentWidget() is host
    assert pip.is_docked() is False
    assert [k for k, _ in panel.tabs()] == [PAD_TAB]


def test_docking_never_touches_the_standalone_width(merged, host):
    """The user's own PiP width is config state; docking must not spend it."""
    panel, pip = merged
    assert pip.width_setting() == 240
    panel.set_active_tab(INPUT_TAB)
    assert pip.width() == panel.width()   # the panel drives it while docked
    panel.detach_guest(host)
    assert pip.width_setting() == 240
    assert pip.width() == 240


# ---- tabs ---------------------------------------------------------------
def test_only_the_active_page_is_shown(merged):
    panel, pip = merged
    panel.set_active_tab(PAD_TAB)
    assert pip.isVisible() is False       # hidden means its timer is stopped
    panel.set_active_tab(INPUT_TAB)
    assert pip.isVisible() is True
    assert pip.pos().y() > 0              # sits below the header


def test_clicking_a_tab_switches_and_reports(merged):
    panel, _pip = merged
    seen = []
    panel.tab_changed.connect(seen.append)
    _click(panel, _tab_point(panel, INPUT_TAB))
    assert panel.active_tab() == INPUT_TAB
    assert seen == [INPUT_TAB]
    _click(panel, _tab_point(panel, INPUT_TAB))   # already there: a no-op
    assert seen == [INPUT_TAB]
    _click(panel, _tab_point(panel, PAD_TAB))
    assert seen == [INPUT_TAB, PAD_TAB]


def test_the_body_follows_the_page_aspect(merged):
    """Each page has its own shape, so the frame's height changes with the tab
    while the width — the thing the user dragged — stays put."""
    panel, _pip = merged
    panel.set_active_tab(PAD_TAB)
    width, pad_h = panel.width(), panel.height()
    panel.set_active_tab(INPUT_TAB)
    assert panel.width() == width
    assert panel.height() != pad_h


def test_collapsing_folds_the_guest_away(merged):
    panel, pip = merged
    panel.set_active_tab(INPUT_TAB)
    panel.set_collapsed(True)
    assert pip.isVisible() is False
    panel.set_collapsed(False)
    assert pip.isVisible() is True


def test_pad_off_leaves_the_panel_to_the_preview(merged):
    """Merged, with the pad map switched off, the panel is just the PiP's
    frame — the Input page has to survive on its own."""
    panel, pip = merged
    panel.set_pad_enabled(False)
    assert [k for k, _ in panel.tabs()] == [INPUT_TAB]
    assert panel.active_tab() == INPUT_TAB
    assert pip.isVisible() is True
    assert panel._tab_rects() == []       # one page: a title, not a tab strip


def test_the_dock_grip_falls_through_to_the_panel(merged):
    """The guest covers the panel's resize corner, so it must not swallow the
    press there — that is how a drag on that corner still resizes the panel."""
    panel, pip = merged
    panel.set_active_tab(INPUT_TAB)
    pip.set_click_hint("click me")        # otherwise nothing claims a press
    fired = []
    pip.clicked.connect(lambda: fired.append(True))
    corner = QPointF(pip.width() - 4, pip.height() - 4)
    _click(pip, corner)
    assert pip._press is None             # not taken as a click-to-review
    assert fired == []


# ---- config -------------------------------------------------------------
def test_ui_config_merge_defaults():
    ui = Config().ui
    assert ui.merge_overlays is False
    assert ui.overlay_tab == "pad"


def test_ui_config_merge_roundtrip():
    cfg = Config()
    cfg.ui.merge_overlays = True
    cfg.ui.overlay_tab = "input"
    back = Config.from_dict(cfg.to_dict()).ui
    assert back.merge_overlays is True
    assert back.overlay_tab == "input"
    assert Config.from_dict({"ui": {"overlay_tab": "nope"}}).ui.overlay_tab == "pad"
