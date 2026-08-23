"""The sliding ON/OFF toolbar control (Auto Switch, controller map).

Offscreen Qt: handlers are called directly, since the point is the state
machine and the signal contract, not Qt's event delivery.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from grimoireassist.ui.slide_toggle import AutoSwitchToggle, SlideToggle


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _click(toggle, x: float) -> None:
    pos = QPointF(x, toggle.height() / 2)
    toggle.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def test_starts_in_the_requested_state(app):
    assert SlideToggle(on=True).is_on() is True
    assert SlideToggle(on=False).is_on() is False


def test_halves_select_their_own_state(app):
    toggle = SlideToggle(on=True)
    seen = []
    toggle.toggled.connect(seen.append)
    quarter = toggle.width() / 4
    _click(toggle, quarter * 3)          # right half = off
    assert toggle.is_on() is False
    _click(toggle, quarter * 3)          # already off: no-op, stays silent
    assert seen == [False]
    _click(toggle, quarter)              # left half = on
    assert toggle.is_on() is True
    assert seen == [False, True]


def test_set_on_is_silent(app):
    """Hosts push config state back in without re-entering their own slot."""
    toggle = SlideToggle(on=True)
    seen = []
    toggle.toggled.connect(seen.append)
    toggle.set_on(False)
    assert toggle.is_on() is False
    assert seen == []


def test_space_flips_it(app):
    from PyQt6.QtGui import QKeyEvent
    toggle = SlideToggle(on=False)
    seen = []
    toggle.toggled.connect(seen.append)
    toggle.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space,
                                   Qt.KeyboardModifier.NoModifier))
    assert seen == [True]


def test_width_follows_the_labels(app):
    """Both halves are sized to the widest label, so neither clips."""
    narrow = SlideToggle(labels=("On", "Off"))
    wide = SlideToggle(labels=("Something On", "Something Off"))
    assert wide.width() > narrow.width()
    assert wide.width() % 2 == 0          # two equal halves


def test_auto_switch_keeps_its_labels(app):
    toggle = AutoSwitchToggle()
    assert toggle.is_on() is True
    assert toggle._labels == ("Auto On", "Auto Off")
