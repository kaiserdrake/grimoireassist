"""Review screen behaviour: playback clock, scrubbing, speeds, save, close.

Runs on Qt's offscreen platform, so it needs no display and no camera. Widgets
are driven directly rather than through synthetic mouse events — the point is
the playback/seek logic, not Qt's own event delivery.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from grimoireassist.capture import FrameBuffer
from grimoireassist.reviewbuffer import RollingRecorder
from grimoireassist.ui.preview import InputPreview
from grimoireassist.ui.review import SPEEDS, ReviewOverlay


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def buffered(tmp_path):
    """A recorder holding 20 frames, one every 100 ms (10 fps, 1.9 s span)."""
    rec = RollingRecorder(tmp_path / "buffer", minutes=5.0, fps=10.0,
                          max_height=0, quality=90)
    assert rec.start()
    for i in range(20):
        frame = np.full((48, 64, 3), 10 + i, dtype=np.uint8)
        rec.feed(frame, t=1000.0 + i * 0.1, block=True)
    assert rec.drain(timeout=10.0)
    yield rec
    rec.stop()


@pytest.fixture
def overlay(app, buffered, tmp_path):
    host = QWidget()
    host.resize(900, 600)
    ov = ReviewOverlay(buffered.snapshot(), tmp_path / "recordings", parent=host)
    yield ov
    if ov._export is not None:
        ov._export.cancel.set()
        ov._export.wait(5000)
        ov._export = None
    ov.close_review(confirm=False)


def test_opens_on_the_newest_frame(overlay):
    assert len(overlay._snap) == 20
    assert overlay._index == 19
    assert overlay._video._pixmap is not None   # a frame was decoded and shown
    assert overlay._scrub.maximum() == 19
    assert not overlay._playing


def test_scrub_bar_seeks_to_a_frame(overlay):
    overlay._scrub.setValue(4)                  # what a drag/click ends up doing
    assert overlay._index == 4
    assert overlay._shown_index == 4
    assert overlay._time_label.text() == "0:00 / 0:02"   # 0.4 s in, 1.9 s span
    overlay._scrub.setValue(19)
    assert overlay._index == 19
    assert overlay._time_label.text() == "0:02 / 0:02"


def test_scrub_bar_maps_position_to_frame(overlay):
    bar = overlay._scrub
    bar.resize(201, 22)
    assert bar._value_at(0) == 0
    assert bar._value_at(200) == 19
    assert bar._value_at(100) == pytest.approx(10, abs=1)
    assert bar._value_at(-50) == 0               # clamped, not negative
    assert bar._value_at(9999) == 19


def test_stepping_moves_one_frame_and_clamps(overlay):
    overlay._seek_index(5)
    overlay._step(1)
    assert overlay._index == 6
    overlay._step(-1)
    assert overlay._index == 5
    overlay._seek_index(0)
    overlay._step(-1)
    assert overlay._index == 0                  # no wrap past the oldest frame
    overlay._seek_index(19)
    overlay._step(1)
    assert overlay._index == 19


def test_home_and_end_jump_without_pausing(app, overlay):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    def press(key):
        overlay.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key,
                                        Qt.KeyboardModifier.NoModifier))

    overlay._seek_index(10)
    overlay._set_playing(True)
    press(Qt.Key.Key_Home)
    assert overlay._index == 0 and overlay._playing
    press(Qt.Key.Key_End)
    assert overlay._index == 19 and overlay._playing
    press(Qt.Key.Key_Space)
    assert not overlay._playing


def test_speed_selection_is_exclusive(overlay):
    for speed in SPEEDS:
        overlay._set_speed(speed)
        assert overlay._speed == speed
        checked = [b.text() for b in overlay._speed_buttons if b.isChecked()]
        assert checked == [f"{speed:g}×"]


@pytest.mark.parametrize("speed", SPEEDS)
def test_playback_advances_in_proportion_to_speed(overlay, speed):
    """One second of wall time must cover `speed` seconds of the timeline."""
    overlay._seek_index(0)
    overlay._set_speed(speed)
    overlay._set_playing(True)
    assert overlay._playing
    overlay._play_origin -= 1.0                 # pretend a second of wall time passed
    overlay._tick()
    assert overlay._snap.rel_time(overlay._index) == pytest.approx(speed, abs=0.11)


def test_playback_stops_at_the_end(overlay):
    overlay._seek_index(0)
    overlay._set_playing(True)
    overlay._play_origin -= 60.0                # far past the end of the buffer
    overlay._tick()
    assert overlay._index == 19
    assert not overlay._playing
    assert overlay._play_btn.text() == "▶"


def test_toggle_play_replays_from_the_start_at_the_end(overlay):
    overlay._seek_index(19)
    overlay._toggle_play()
    assert overlay._playing and overlay._index == 0


def test_scrubbing_while_playing_continues_from_there(overlay):
    overlay._set_playing(True)
    overlay._seek_index(15)
    assert overlay._playing
    assert overlay._play_rel == pytest.approx(overlay._snap.rel_time(15))


def test_save_writes_a_clip(app, overlay, tmp_path):
    overlay._on_save_clicked()
    assert overlay._export is not None
    assert overlay._save_btn.text().endswith("Cancel save")
    assert overlay._export.wait(30000)
    app.processEvents()                         # deliver the worker's signals
    written = list((tmp_path / "recordings").glob("review_*"))
    assert len(written) == 1 and written[0].stat().st_size > 0
    assert written[0].name in overlay._status.text()
    assert overlay._export is None
    assert overlay._save_btn.text().endswith("Save clip")


def test_close_releases_the_snapshot_and_signals(app, buffered, tmp_path):
    host = QWidget()
    host.resize(800, 500)
    ov = ReviewOverlay(buffered.snapshot(), tmp_path / "recordings", parent=host)
    seen = []
    ov.closed.connect(lambda: seen.append(True))
    ov._set_playing(True)
    ov.close_review()
    assert seen == [True]
    assert not ov._playing
    assert ov.isHidden()
    assert ov._snap.frame(0) is None            # snapshot closed, reads refused


def test_empty_buffer_is_not_playable(app, tmp_path):
    rec = RollingRecorder(tmp_path / "buffer", minutes=5.0)
    assert rec.start()
    try:
        host = QWidget()
        host.resize(600, 400)
        ov = ReviewOverlay(rec.snapshot(), tmp_path / "recordings", parent=host)
        assert len(ov._snap) == 0
        assert not ov._scrub.isEnabled()
        assert not ov._save_btn.isEnabled()
        ov._set_playing(True)
        assert not ov._playing                  # nothing to play, no timer running
        ov._step(1)                             # must not raise
        ov.close_review()
    finally:
        rec.stop()


def test_pip_click_emits_only_when_clickable(app):
    """The PiP is the entry point to the review screen, so its click must fire
    once for a plain click and never for a resize drag."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    host = QWidget()
    host.resize(900, 600)
    pip = InputPreview(FrameBuffer(), fps=10.0, parent=host, width=240)
    fired = []
    pip.clicked.connect(lambda: fired.append(True))

    def press_release(pos: QPointF) -> None:
        for kind in ("press", "release"):
            ev = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress if kind == "press"
                else QMouseEvent.Type.MouseButtonRelease,
                pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier)
            if kind == "press":
                pip.mousePressEvent(ev)
            else:
                pip.mouseReleaseEvent(ev)

    middle = QPointF(pip.width() / 2, pip.height() / 2)
    press_release(middle)
    assert fired == []                          # no hint set: not clickable yet

    pip.set_click_hint("⟲  Click to review")
    press_release(middle)
    assert fired == [True]

    grip = QPointF(pip.width() - 4, 4)          # the resize handle, not a click
    press_release(grip)
    assert fired == [True]


# ============================ save a single frame ============================
def test_saving_a_frame_writes_a_png(overlay, tmp_path):
    overlay._seek_index(7)
    overlay._on_save_frame()
    written = list((tmp_path / "recordings").glob("frame_*.png"))
    assert len(written) == 1
    assert written[0].stat().st_size > 0
    assert "Saved" in overlay._status.text()


def test_the_saved_frame_is_the_one_on_screen(overlay, tmp_path):
    """Each buffered frame is a flat shade, so the pixels identify the frame."""
    import cv2
    overlay._seek_index(3)
    expected = overlay._snap.frame(3)
    overlay._on_save_frame()
    written = list((tmp_path / "recordings").glob("frame_*.png"))[0]
    got = cv2.imread(str(written))
    assert got.shape == expected.shape
    assert int(abs(got.astype(int) - expected.astype(int)).max()) <= 1


def test_saving_a_frame_reports_a_failure_rather_than_dying(overlay, monkeypatch):
    monkeypatch.setattr(overlay._snap, "frame", lambda i: None)
    overlay._on_save_frame()
    assert "could not be read" in overlay._status.text()


# ================================ trim markers ===============================
def test_the_trim_starts_covering_the_whole_buffer(overlay):
    assert (overlay._in_point, overlay._out_point) == (0, 19)
    assert not overlay._trimmed()
    assert overlay._save_btn.text() == "💾  Save clip"


def test_marking_in_and_out_sets_the_span(overlay):
    overlay._seek_index(5)
    overlay._mark_in()
    overlay._seek_index(12)
    overlay._mark_out()
    assert (overlay._in_point, overlay._out_point) == (5, 12)
    assert overlay._trimmed()


def test_the_save_button_shows_the_trimmed_duration(overlay):
    overlay._seek_index(4)
    overlay._mark_in()
    overlay._seek_index(14)
    overlay._mark_out()
    assert "(" in overlay._save_btn.text()      # duration is spelled out


def test_marking_in_past_the_end_carries_the_end_along(overlay):
    """Otherwise the pair would cross over and select nothing."""
    overlay._seek_index(3)
    overlay._mark_out()
    overlay._seek_index(15)
    overlay._mark_in()
    assert overlay._in_point == 15
    assert overlay._out_point >= overlay._in_point


def test_marking_out_before_the_start_carries_the_start_along(overlay):
    overlay._seek_index(15)
    overlay._mark_in()
    overlay._seek_index(2)
    overlay._mark_out()
    assert overlay._out_point == 2
    assert overlay._in_point <= overlay._out_point


def test_clearing_the_trim_restores_the_whole_buffer(overlay):
    overlay._seek_index(5)
    overlay._mark_in()
    assert overlay._trimmed()
    overlay._clear_trim()
    assert (overlay._in_point, overlay._out_point) == (0, 19)
    assert not overlay._trimmed()


def test_dragging_a_marker_updates_the_overlay(overlay):
    overlay._scrub.markers_changed.emit(6, 11)
    assert (overlay._in_point, overlay._out_point) == (6, 11)
    assert overlay._trimmed()


def test_the_markers_cannot_cross_when_dragged(overlay):
    """The bar pushes rather than swaps, so in stays <= out at all times."""
    overlay._scrub.set_markers(5, 12)
    overlay._scrub._drag = "in"
    overlay._scrub._move_marker(overlay._scrub._x_at(18))   # drag past the end
    assert overlay._scrub.markers()[0] <= overlay._scrub.markers()[1]
    overlay._scrub._drag = "out"
    overlay._scrub._move_marker(overlay._scrub._x_at(0))    # drag past the start
    assert overlay._scrub.markers()[0] <= overlay._scrub.markers()[1]


def test_only_the_marked_span_is_exported(overlay, tmp_path):
    import cv2
    overlay._seek_index(4)
    overlay._mark_in()
    overlay._seek_index(9)
    overlay._mark_out()
    overlay._on_save_clicked()
    assert overlay._export is not None
    overlay._export.wait(20000)
    written = sorted((tmp_path / "recordings").glob("review_*.*"))
    assert written, "nothing was written"
    cap = cv2.VideoCapture(str(written[0]))
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert count == 6, f"expected frames 4..9 inclusive, got {count}"


def test_an_untrimmed_save_still_writes_everything(overlay, tmp_path):
    import cv2
    overlay._on_save_clicked()
    overlay._export.wait(20000)
    written = sorted((tmp_path / "recordings").glob("review_*.*"))[0]
    cap = cv2.VideoCapture(str(written))
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert count == 20
