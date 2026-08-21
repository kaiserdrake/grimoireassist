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
    assert [text for _seq, _ts, _h, text in win._dialogue_entries] == [
        "Three.", "Two.", "One."]


def test_the_oldest_line_is_dropped_once_the_log_is_full(app):
    """The cap must trim the far end; trimming the near end would eat the
    newest line the instant the log filled up."""
    win = _LogWindow()
    for i in range(_DIALOGUE_MAX_LINES + 5):
        _say(win, f"Line {i}.")
    texts = [text for _seq, _ts, _h, text in win._dialogue_entries]
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
    still = [seq for seq, _ts, _h, text in win._dialogue_entries if text == "First."]
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
    return next(seq for seq, _ts, _h, line in win._dialogue_entries if line == text)


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


# ---------------------------------------------------- tracking view only
class _ViewWindow:
    """The slice of MainWindow that decides whether the strip is on screen."""

    def __init__(self):
        self._dialogue_widget = QWidget()
        self._dialogue_wanted = False
        self._grimoire_shown = False
        self._debug_widget = QWidget()
        self._debug_widget.setVisible(False)
        self._preview = _FakePreview()

    def _sync_preview_inset(self):
        MainWindow._sync_preview_inset(self)

    def _apply_dialogue_visibility(self):
        MainWindow._apply_dialogue_visibility(self)

    def _shown(self):
        return self._dialogue_widget.isVisibleTo(
            self._dialogue_widget.parentWidget())


class _FakePreview:
    def __init__(self):
        self.inset = None

    def set_bottom_inset(self, value):
        self.inset = value


def _want(win, wanted):
    MainWindow._toggle_dialogue_panel(win, wanted)


def _grimoire(win, shown):
    win._grimoire_shown = shown
    MainWindow._apply_dialogue_visibility(win)


def test_the_strip_shows_on_the_tracking_view(app):
    win = _ViewWindow()
    _want(win, True)
    assert win._shown()


def test_the_strip_is_hidden_on_the_grimoire_view(app):
    """The Grimoire gets the whole pane; the transcript belongs to tracking."""
    win = _ViewWindow()
    _want(win, True)
    _grimoire(win, True)
    assert not win._shown()


def test_it_comes_back_when_returning_to_tracking(app):
    win = _ViewWindow()
    _want(win, True)
    _grimoire(win, True)
    _grimoire(win, False)
    assert win._shown()


def test_turning_it_on_while_on_the_grimoire_is_remembered(app):
    """Toggling it on there should take effect on the way back, not immediately."""
    win = _ViewWindow()
    _grimoire(win, True)
    _want(win, True)
    assert not win._shown()      # still the Grimoire view
    _grimoire(win, False)
    assert win._shown()          # honoured on return


def test_turning_it_off_stays_off_across_a_view_switch(app):
    win = _ViewWindow()
    _want(win, True)
    _want(win, False)
    _grimoire(win, True)
    _grimoire(win, False)
    assert not win._shown()


# -------------------------------------------------------- empty-log guidance
class _PlaceholderWindow:
    def __init__(self):
        self.cfg = Config()
        self._dialogue_log = QTextEdit()
        self._dialogue_entries = deque(maxlen=_DIALOGUE_MAX_LINES)
        self._dialogue_selected = None

    def _dialogue_placeholder(self):
        return MainWindow._dialogue_placeholder(self)

    def text(self):
        MainWindow._render_dialogue_log(self)
        return self._dialogue_log.toPlainText()


def test_an_empty_log_says_narration_is_off(app):
    win = _PlaceholderWindow()
    assert "off" in win.text().lower()


def test_an_empty_log_points_at_calibration_when_no_region_is_set(app):
    """The commonest cause of 'it never detects anything'."""
    from grimoireassist.config import Region
    win = _PlaceholderWindow()
    win.cfg.speech.enabled = True
    body = win.text()
    assert "F9" in body and "dialogue" in body.lower()

    win.cfg.ocr.regions_dialogue = Region(90, 520, 1100, 150)
    assert "F9" not in win.text()


def test_an_empty_log_says_it_is_listening_once_set_up(app):
    from grimoireassist.config import Region
    win = _PlaceholderWindow()
    win.cfg.speech.enabled = True
    win.cfg.ocr.regions_dialogue = Region(90, 520, 1100, 150)
    assert "listening" in win.text().lower()


def test_the_placeholder_gives_way_to_real_lines(app):
    from grimoireassist.config import Region
    win = _PlaceholderWindow()
    win.cfg.speech.enabled = True
    win.cfg.ocr.regions_dialogue = Region(90, 520, 1100, 150)
    win._dialogue_entries.appendleft((1, "12:00:00", "", "Careful there."))
    body = win.text()
    assert "Careful there." in body and "listening" not in body.lower()


# ------------------------------------------------------------- mute hotkey
class _MuteWindow:
    """The slice of MainWindow the mute hotkey drives."""

    def __init__(self, muted=False):
        self.cfg = Config()
        self.cfg.speech.muted = muted
        self.cfg.save = lambda *a, **k: None
        self.mute_btn = QPushButton()
        self.mute_btn.setCheckable(True)
        self.mute_btn.setChecked(muted)
        self.speaker = None
        self.flushes = []
        self._bar = _FakeStatusBar()
        self.synced = 0

    def statusBar(self):
        return self._bar

    def _sync_speech_ui(self):
        self.synced += 1

    def _set_muted(self, muted):
        MainWindow._set_muted(self, muted)

    def _toggle_mute(self):
        MainWindow._toggle_mute(self)


def test_the_hotkey_mutes_when_unmuted(app):
    win = _MuteWindow(muted=False)
    win._toggle_mute()
    assert win.cfg.speech.muted is True
    assert any("muted" in m for m in win._bar.messages)


def test_the_hotkey_unmutes_when_muted(app):
    win = _MuteWindow(muted=True)
    win._toggle_mute()
    assert win.cfg.speech.muted is False
    assert any("unmuted" in m for m in win._bar.messages)


def test_the_hotkey_keeps_toggling(app):
    win = _MuteWindow(muted=False)
    for expected in (True, False, True, False):
        win._toggle_mute()
        assert win.cfg.speech.muted is expected


def test_the_hotkey_follows_the_config_not_the_button(app):
    """The button can be out of step (menu, or an earlier press); the config is
    the thing that actually decides whether audio plays."""
    win = _MuteWindow(muted=True)
    win.mute_btn.setChecked(False)          # stale button state
    win._toggle_mute()
    assert win.cfg.speech.muted is False    # toggled from the config's True


def test_muting_by_hotkey_cuts_the_line_in_progress(app):
    class _Speaker:
        def __init__(self):
            self.flushed = 0

        def flush(self):
            self.flushed += 1

    win = _MuteWindow(muted=False)
    win.speaker = _Speaker()
    win._toggle_mute()
    assert win.speaker.flushed == 1


# --------------------------------------------- offering narration after F9
class _OfferWindow:
    """The slice of MainWindow that _offer_narration uses."""

    def __init__(self):
        self.cfg = Config()
        self.asked = []
        self.act_speech = QPushButton()      # stands in for the checkable action
        self.act_speech.setCheckable(True)

    def _offer(self, answer):
        import grimoireassist.ui.main_window as mw
        from PyQt6.QtWidgets import QMessageBox
        real = QMessageBox.question

        def fake(*args, **kwargs):
            self.asked.append(args[1] if len(args) > 1 else "")
            return answer
        QMessageBox.question = staticmethod(fake)
        try:
            mw.MainWindow._offer_narration(self)
        finally:
            QMessageBox.question = real


def test_no_offer_when_no_dialogue_region_is_set(app):
    from PyQt6.QtWidgets import QMessageBox
    win = _OfferWindow()
    win._offer(QMessageBox.StandardButton.Yes)
    assert win.asked == []                    # nothing to offer


def test_no_offer_when_narration_is_already_on(app):
    from PyQt6.QtWidgets import QMessageBox
    from grimoireassist.config import Region
    win = _OfferWindow()
    win.cfg.ocr.regions_dialogue = Region(4, 679, 831, 401)
    win.cfg.speech.enabled = True
    win._offer(QMessageBox.StandardButton.Yes)
    assert win.asked == []


def test_a_new_dialogue_region_offers_to_turn_narration_on(app):
    from PyQt6.QtWidgets import QMessageBox
    from grimoireassist.config import Region
    win = _OfferWindow()
    win.cfg.ocr.regions_dialogue = Region(4, 679, 831, 401)
    win._offer(QMessageBox.StandardButton.Yes)
    assert len(win.asked) == 1
    assert win.act_speech.isChecked()          # accepted -> switched on


def test_declining_the_offer_leaves_narration_off(app):
    from PyQt6.QtWidgets import QMessageBox
    from grimoireassist.config import Region
    win = _OfferWindow()
    win.cfg.ocr.regions_dialogue = Region(4, 679, 831, 401)
    win._offer(QMessageBox.StandardButton.No)
    assert len(win.asked) == 1
    assert not win.act_speech.isChecked()


# --------------------------------------------------------- headers in the log
def test_a_header_is_stored_alongside_its_line(app):
    win = _LogWindow()
    MainWindow._on_dialogue_text(win, "At sunset, I heard dogs barking.",
                                 "Heart-to-Heart Info")
    seq, ts, header, text = win._dialogue_entries[0]
    assert header == "Heart-to-Heart Info"
    assert "Heart-to-Heart" not in text


def test_a_header_is_shown_in_its_own_colour(app):
    from grimoireassist.ui.main_window import _DIALOGUE_HEADER
    win = _LogWindow()
    MainWindow._on_dialogue_text(win, "At sunset, I heard dogs barking.",
                                 "Heart-to-Heart Info")
    assert _DIALOGUE_HEADER.lower() in win.html().lower()
    assert "Heart-to-Heart Info" in win._dialogue_log.toPlainText()


def test_a_header_is_never_spoken(app):
    win = _LogWindow()
    win.cfg.speech.muted = False
    MainWindow._on_dialogue_text(win, "At sunset, I heard dogs barking.",
                                 "Heart-to-Heart Info")
    assert win.spoken == ["At sunset, I heard dogs barking."]


def test_replaying_a_line_does_not_speak_its_header_either(app):
    win = _LogWindow()
    win.cfg.speech.muted = False
    MainWindow._on_dialogue_text(win, "At sunset, I heard dogs barking.",
                                 "Heart-to-Heart Info")
    win.spoken.clear()
    MainWindow._replay_entry(win, win._dialogue_entries[0][0])
    assert win.spoken == ["At sunset, I heard dogs barking."]


def test_a_line_with_no_header_renders_without_one(app):
    win = _LogWindow()
    MainWindow._on_dialogue_text(win, "Just a plain line of dialogue.")
    assert win._dialogue_entries[0][2] == ""
    assert win._dialogue_log.toPlainText().strip().endswith(
        "Just a plain line of dialogue.")


def test_the_header_survives_a_real_signal_hop(app):
    """Qt honours the slot's *declared* signature: a @pyqtSlot(str) on the
    receiver drops the header the signal is sending, and it arrives as the
    default "" with nothing raised. Only driving a real signal catches that, so
    the slot is exercised here through one rather than called directly."""
    from PyQt6.QtCore import QObject, pyqtSignal

    class _Emitter(QObject):
        dialogue_text = pyqtSignal(str, str)

    class _Receiver(QObject):
        # the real slot, decorator and all
        _on_dialogue_text = MainWindow._on_dialogue_text

        def __init__(self):
            super().__init__()
            self.cfg = Config()
            self.cfg.speech.muted = True
            self._dialogue_log = QTextEdit()
            self._dialogue_entries = deque(maxlen=_DIALOGUE_MAX_LINES)
            self._dialogue_seq = 0
            self._dialogue_selected = None
            self.speaker = None

        def _log_line(self, text):
            pass

        def _render_dialogue_log(self):
            MainWindow._render_dialogue_log(self)

    emitter, receiver = _Emitter(), _Receiver()
    emitter.dialogue_text.connect(receiver._on_dialogue_text)
    emitter.dialogue_text.emit("At sunset, I heard dogs barking.",
                               "Heart-to-Heart Info")
    assert receiver._dialogue_entries[0][2] == "Heart-to-Heart Info"
