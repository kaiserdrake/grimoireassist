"""The dialogue panel's "Test line" box.

Lets narration be tried with fixed input — no capture source, no game, no
calibrated region. It runs the typed text through the same cleaning a real OCR
read gets, so what you hear is what the pipeline would actually produce.

`_inject_dialogue` touches only `self.cfg`, `self._dialogue_input`,
`self.statusBar()` and `self._on_dialogue_text`, so it is driven here against a
stand-in rather than a whole MainWindow.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from PyQt6.QtWidgets import QApplication, QLineEdit

from grimoireassist.config import Config
from grimoireassist.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, timeout=0):
        self.messages.append(text)


class _Window:
    """The slice of MainWindow that _inject_dialogue actually uses."""

    def __init__(self):
        self.cfg = Config()
        self._dialogue_input = QLineEdit()
        self._bar = _FakeStatusBar()
        self.narrated = []

    def statusBar(self):
        return self._bar

    def _on_dialogue_text(self, text):
        self.narrated.append(text)


def _inject(win, text):
    win._dialogue_input.setText(text)
    MainWindow._inject_dialogue(win)


def test_a_typed_line_is_narrated_and_the_box_is_cleared(app):
    win = _Window()
    _inject(win, "Careful with that one. It bites when cornered.")
    assert win.narrated == ["Careful with that one. It bites when cornered."]
    assert win._dialogue_input.text() == ""


def test_game_ui_decoration_is_stripped_before_narrating(app):
    """Paste a line straight off the screen, arrow and all."""
    win = _Window()
    _inject(win, "The elder waits by the bridge. ▼ |")
    assert win.narrated == ["The elder waits by the bridge."]


def test_a_fragment_is_refused_with_a_reason(app):
    win = _Window()
    _inject(win, "Hm")
    assert win.narrated == []
    assert any("Too fragmentary" in m for m in win._bar.messages)


def test_a_refused_line_stays_in_the_box_for_editing(app):
    """Clearing it would make the user retype the whole line."""
    win = _Window()
    _inject(win, "Hm")
    assert win._dialogue_input.text() == "Hm"


def test_an_empty_box_does_nothing(app):
    win = _Window()
    _inject(win, "   ")
    assert win.narrated == [] and win._bar.messages == []


def test_the_min_chars_setting_is_respected(app):
    win = _Window()
    win.cfg.speech.min_chars = 2
    _inject(win, "Hm?")
    assert win.narrated == ["Hm?"]
