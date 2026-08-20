"""Dialogue strip behaviour: newest-first ordering and the collapse chevron.

The strip sits at the top of the main pane, so the line just spoken belongs
against the top edge rather than at the bottom of a scrolling log. The chevron
folds it to its header, matching the controller map overlay.

These drive the MainWindow methods against stand-ins holding the real widgets —
the widgets are the point, a whole window is not.
"""
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from collections import deque

from PyQt6.QtWidgets import QApplication, QPushButton, QTextEdit, QWidget

from grimoireassist.config import Config
from grimoireassist.ui.main_window import (
    _DIALOGUE_MAX_H, _DIALOGUE_MAX_LINES, _DIALOGUE_MIN_H, _DIALOGUE_ROWS,
    _DIALOGUE_SELECTED, _DIALOGUE_STAMP, MainWindow, _DialogueGrip,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _LogWindow:
    """The slice of MainWindow that _on_dialogue_text uses."""

    def __init__(self):
        self.cfg = Config()
        self.cfg.speech.muted = True     # keep the speaker out of it entirely
        self._dialogue_log = QTextEdit()
        self._dialogue_entries = deque(maxlen=_DIALOGUE_MAX_LINES)
        self._dialogue_seq = 0
        self._dialogue_selected = None
        self.speaker = None
        self.logged = []
        self.spoken = []
        self.replay_btn = QPushButton()
        self._bar = _FakeStatusBar()

    def statusBar(self):
        return self._bar

    def _ensure_speaker(self):
        win = self

        class _Speaker:
            def say(self, text):
                win.spoken.append(text)
        return _Speaker()

    def _log_line(self, text):
        self.logged.append(text)

    def _render_dialogue_log(self):
        MainWindow._render_dialogue_log(self)

    def html(self):
        return self._dialogue_log.toHtml()


class _FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, timeout=0):
        self.messages.append(text)


class _FoldWindow:
    """The slice of MainWindow that the collapse chevron uses."""

    def __init__(self):
        self.cfg = Config()
        self.cfg.save = lambda *a, **k: self.saves.append(1)
        self.saves = []
        self._dialogue_body = QWidget()
        self.dialogue_fold_btn = QPushButton()

    def _apply_dialogue_collapsed(self):
        MainWindow._apply_dialogue_collapsed(self)


def _say(win, text):
    MainWindow._on_dialogue_text(win, text)


def _lines(win):
    return [ln for ln in win._dialogue_log.toPlainText().strip().splitlines() if ln]


# ------------------------------------------------------------- newest first
def test_the_newest_line_is_at_the_top(app):
    win = _LogWindow()
    for line in ("First.", "Second.", "Third."):
        _say(win, line)
    shown = _lines(win)
    assert "Third." in shown[0]
    assert "First." in shown[-1]


def test_every_line_is_kept_in_order(app):
    win = _LogWindow()
    for line in ("One.", "Two.", "Three."):
        _say(win, line)
    assert [text for _seq, _ts, text in win._dialogue_entries] == [
        "Three.", "Two.", "One."]


def test_the_oldest_line_is_dropped_once_the_log_is_full(app):
    """The cap must trim the far end; trimming the near end would eat the
    newest line the instant the log filled up."""
    win = _LogWindow()
    for i in range(_DIALOGUE_MAX_LINES + 5):
        _say(win, f"Line {i}.")
    texts = [text for _seq, _ts, text in win._dialogue_entries]
    assert len(texts) == _DIALOGUE_MAX_LINES
    assert texts[0] == f"Line {_DIALOGUE_MAX_LINES + 4}."      # newest kept
    assert "Line 0." not in texts                              # oldest dropped


# --------------------------------------------------------------- presentation
def test_the_timestamp_is_bracketed_and_set_apart(app):
    win = _LogWindow()
    _say(win, "Careful there.")
    body = win._dialogue_log.toPlainText()
    assert "[" in body and "]" in body
    stamp = body[body.index("["):body.index("]") + 1]
    assert len(stamp) == len("[HH:MM:SS]")
    html = win.html().lower()
    assert _DIALOGUE_STAMP.lower() in html      # its own colour, not the prose colour


def _span_sizes(html_text):
    """Declared font sizes of the timestamp span and the prose span.

    Read from the spans themselves rather than from the order sizes appear in
    the document — Qt emits a default size of its own first, and comparing
    against that would pass even if both spans were styled identically."""
    stamp = prose = None
    for style, text in re.findall(r'<span style="([^"]*)"[^>]*>(.*?)</span>',
                                  html_text, re.S):
        match = re.search(r"font-size:\s*([0-9.]+)pt", style)
        if not match:
            continue
        if text.strip().startswith("["):
            stamp = float(match.group(1))
        elif text.strip():
            prose = float(match.group(1))
    return stamp, prose


def test_the_timestamp_is_smaller_than_the_line(app):
    win = _LogWindow()
    _say(win, "Careful there.")
    stamp, prose = _span_sizes(win.html())
    assert stamp is not None and prose is not None, win.html()[:500]
    assert stamp < prose, f"timestamp {stamp}pt is not smaller than prose {prose}pt"


def test_neighbouring_lines_are_banded_differently(app):
    win = _LogWindow()
    _say(win, "First.")
    _say(win, "Second.")
    html = win.html().lower()
    for shade in _DIALOGUE_ROWS:
        assert shade.lower() in html, f"{shade} missing from {html[:400]}"


def test_a_line_keeps_its_band_as_newer_lines_push_it_down(app):
    """Banding follows the entry, not the row position — otherwise every line
    would change shade each time a new one arrived."""
    win = _LogWindow()
    _say(win, "First.")
    first_seq = win._dialogue_entries[0][0]
    _say(win, "Second.")
    _say(win, "Third.")
    still = [seq for seq, _ts, text in win._dialogue_entries if text == "First."]
    assert still == [first_seq]


def test_a_muted_line_is_still_logged_and_displayed(app):
    win = _LogWindow()
    _say(win, "Heard but not spoken.")
    assert "Heard but not spoken." in win._dialogue_log.toPlainText()
    assert any("muted" in entry for entry in win.logged)


# ----------------------------------------------------------------- collapse
def test_collapsing_folds_the_body_away(app):
    win = _FoldWindow()
    win.cfg.ui.dialogue_collapsed = True
    MainWindow._apply_dialogue_collapsed(win)
    assert not win._dialogue_body.isVisibleTo(win._dialogue_body.parentWidget())
    assert win.dialogue_fold_btn.text() == "▼"   # points down: click to unfold


def test_expanding_brings_the_body_back(app):
    win = _FoldWindow()
    win.cfg.ui.dialogue_collapsed = False
    MainWindow._apply_dialogue_collapsed(win)
    assert win.dialogue_fold_btn.text() == "▲"   # points up: click to fold


def test_the_collapsed_state_is_persisted(app):
    win = _FoldWindow()
    MainWindow._set_dialogue_collapsed(win, True)
    assert win.cfg.ui.dialogue_collapsed is True
    assert win.saves == [1]


def test_setting_the_same_state_twice_does_not_re_save(app):
    win = _FoldWindow()
    MainWindow._set_dialogue_collapsed(win, True)
    MainWindow._set_dialogue_collapsed(win, True)
    assert win.saves == [1]


def test_the_chevron_round_trips(app):
    win = _FoldWindow()
    MainWindow._set_dialogue_collapsed(win, True)
    assert win.dialogue_fold_btn.text() == "▼"
    MainWindow._set_dialogue_collapsed(win, False)
    assert win.dialogue_fold_btn.text() == "▲"
    assert win.cfg.ui.dialogue_collapsed is False


# ------------------------------------------------------------- test line box
class _TestBoxWindow:
    """The slice of MainWindow that the test-line toggle uses."""

    def __init__(self):
        self.cfg = Config()
        self.cfg.save = lambda *a, **k: self.saves.append(1)
        self.saves = []
        self._dialogue_test_row = QWidget()


def test_the_test_line_box_can_be_hidden(app):
    win = _TestBoxWindow()
    MainWindow._toggle_dialogue_test_box(win, False)
    assert win.cfg.ui.dialogue_test_box is False
    assert not win._dialogue_test_row.isVisibleTo(
        win._dialogue_test_row.parentWidget())
    assert win.saves == [1]


def test_hiding_the_test_line_box_is_persisted_and_reversible(app):
    win = _TestBoxWindow()
    MainWindow._toggle_dialogue_test_box(win, False)
    MainWindow._toggle_dialogue_test_box(win, True)
    assert win.cfg.ui.dialogue_test_box is True
    assert win.saves == [1, 1]


def test_the_test_line_box_is_shown_by_default(app):
    assert Config().ui.dialogue_test_box is True


# ------------------------------------------------------------ pick and replay
def _pick(win, seq):
    MainWindow._select_dialogue_entry(win, seq)


def _seq_of(win, text):
    return next(seq for seq, _ts, line in win._dialogue_entries if line == text)


def test_clicking_a_line_picks_it(app):
    win = _LogWindow()
    win.cfg.speech.muted = False
    _say(win, "First.")
    _say(win, "Second.")
    _pick(win, _seq_of(win, "First."))
    assert win._dialogue_selected == _seq_of(win, "First.")
    assert win.replay_btn.isEnabled()


def test_the_picked_line_is_shown_picked_out(app):
    win = _LogWindow()
    _say(win, "First.")
    _pick(win, _seq_of(win, "First."))
    assert _DIALOGUE_SELECTED.lower() in win.html().lower()


def test_clicking_the_picked_line_again_unpicks_it(app):
    win = _LogWindow()
    _say(win, "First.")
    seq = _seq_of(win, "First.")
    _pick(win, seq)
    _pick(win, seq)
    assert win._dialogue_selected is None
    assert not win.replay_btn.isEnabled()


def test_replaying_speaks_that_line_again(app):
    win = _LogWindow()
    win.cfg.speech.muted = False
    _say(win, "Careful there.")
    _say(win, "The elder waits.")
    win.spoken.clear()          # ignore the first pass; the replay is the point
    MainWindow._replay_entry(win, _seq_of(win, "Careful there."))
    assert win.spoken == ["Careful there."]


def test_replaying_while_muted_explains_itself_instead(app):
    """Silently doing nothing would just look broken."""
    win = _LogWindow()
    win.cfg.speech.muted = True
    _say(win, "Careful there.")
    MainWindow._replay_entry(win, _seq_of(win, "Careful there."))
    assert win.spoken == []
    assert any("muted" in m for m in win._bar.messages)


def test_replaying_also_picks_the_line(app):
    """Double-click replays directly; the line should end up picked too."""
    win = _LogWindow()
    win.cfg.speech.muted = False
    _say(win, "Careful there.")
    seq = _seq_of(win, "Careful there.")
    MainWindow._replay_entry(win, seq)
    assert win._dialogue_selected == seq


def test_replaying_a_line_that_has_scrolled_out_of_the_log_does_nothing(app):
    win = _LogWindow()
    win.cfg.speech.muted = False
    _say(win, "Careful there.")
    win.spoken.clear()
    MainWindow._replay_entry(win, 99999)
    assert win.spoken == []


def test_every_row_carries_its_own_anchor(app):
    """The click target is the anchor, so each row needs a distinct one."""
    win = _LogWindow()
    _say(win, "First.")
    _say(win, "Second.")
    anchors = re.findall(r'<a href="(\d+)"', win.html())
    assert len(set(anchors)) == 2


# -------------------------------------------------------------- resize grip
class _GripHost:
    """A stand-in target the grip resizes, plus the host that persists it."""

    def __init__(self):
        self.cfg = Config()
        self.cfg.save = lambda *a, **k: self.saves.append(1)
        self.saves = []


def _drag(grip, dy):
    """Press, move by dy pixels, release — as a mouse drag would."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    def _event(kind, y):
        return QMouseEvent(kind, QPointF(0, 0), QPointF(0, y),
                           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)

    from PyQt6.QtCore import QEvent
    grip.mousePressEvent(_event(QEvent.Type.MouseButtonPress, 0))
    grip.mouseMoveEvent(_event(QEvent.Type.MouseMove, dy))
    grip.mouseReleaseEvent(_event(QEvent.Type.MouseButtonRelease, dy))


def _grip(start_height=132):
    target = QTextEdit()
    target.setFixedHeight(start_height)
    return _DialogueGrip(target), target


def test_dragging_down_makes_the_dialogue_box_taller(app):
    grip, target = _grip(132)
    _drag(grip, 60)
    assert target.height() == 192


def test_dragging_up_makes_it_shorter(app):
    grip, target = _grip(200)
    _drag(grip, -80)
    assert target.height() == 120


def test_it_cannot_be_dragged_smaller_than_the_minimum(app):
    """Below this the transcript shows less than two lines and stops being useful."""
    grip, target = _grip(132)
    _drag(grip, -5000)
    assert target.height() == _DIALOGUE_MIN_H


def test_it_cannot_be_dragged_past_the_maximum(app):
    grip, target = _grip(132)
    _drag(grip, 5000)
    assert target.height() == _DIALOGUE_MAX_H


def test_the_height_is_persisted_once_on_release(app):
    """Saving on every mouse move would write config hundreds of times a drag."""
    host = _GripHost()
    grip, target = _grip(132)
    live = []
    grip.resized.connect(live.append)
    grip.committed.connect(lambda h: MainWindow._on_dialogue_resized(host, h))
    _drag(grip, 40)
    assert live == [172]              # reported live while dragging
    assert host.cfg.ui.dialogue_height == 172
    assert host.saves == [1]          # written exactly once


def test_a_stray_move_without_a_press_is_ignored(app):
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    grip, target = _grip(132)
    grip.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(0, 0), QPointF(0, 300),
        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))
    assert target.height() == 132


# --------------------------------------------------------------- stop button
class _StopWindow:
    """The slice of MainWindow that the Stop button uses."""

    def __init__(self, speaker=None):
        self.speaker = speaker


class _FakeSpeaker:
    def __init__(self):
        self.flushes = 0

    def flush(self):
        self.flushes += 1


def test_stop_cuts_the_line_being_spoken(app):
    speaker = _FakeSpeaker()
    MainWindow._stop_speaking(_StopWindow(speaker))
    assert speaker.flushes == 1


def test_stop_is_harmless_before_anything_has_been_spoken(app):
    """The speaker is built lazily, so Stop can be clicked while there is none."""
    MainWindow._stop_speaking(_StopWindow(None))   # must not raise
