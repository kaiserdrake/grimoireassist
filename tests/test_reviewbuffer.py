"""Tests for the rolling review buffer (disk-backed DVR). No camera, no GUI.

Frames are fed with explicit timestamps so decimation and retention are
deterministic instead of depending on how fast the test machine runs.
"""
import threading

import cv2
import numpy as np
import pytest

from grimoireassist.reviewbuffer import (
    ExportCancelled, RollingRecorder, export,
)


def _frame(value: int, w: int = 64, h: int = 48) -> np.ndarray:
    """A solid-colour frame; `value` is recoverable after JPEG round-tripping."""
    return np.full((h, w, 3), value, dtype=np.uint8)


def _recorder(tmp_path, **kw) -> RollingRecorder:
    opts = dict(minutes=1.0, fps=10.0, max_height=0, quality=90)
    opts.update(kw)
    rec = RollingRecorder(tmp_path / "buffer", **opts)
    assert rec.start()
    return rec


def _feed(rec: RollingRecorder, count: int, start_t: float = 100.0,
          step: float = 0.1) -> None:
    """Feed `count` frames spaced `step` seconds apart, then wait for the writer."""
    for i in range(count):
        rec.feed(_frame(i % 250), t=start_t + i * step, block=True)
    assert rec.drain(timeout=10.0)


def test_frames_round_trip_through_disk(tmp_path):
    rec = _recorder(tmp_path)
    try:
        _feed(rec, 5)
        with rec.snapshot() as snap:
            assert len(snap) == 5
            for i in range(5):
                img = snap.frame(i)
                assert img is not None and img.shape == (48, 64, 3)
                # JPEG is lossy, but a flat frame stays within a couple of levels.
                assert abs(int(img[0, 0, 0]) - i) <= 2
    finally:
        rec.stop()


def test_decimates_to_target_fps(tmp_path):
    """A 30 fps source feeding a 10 fps buffer keeps roughly every third frame."""
    rec = _recorder(tmp_path, fps=10.0)
    try:
        for i in range(30):                     # 1 second of 30 fps
            rec.feed(_frame(i), t=200.0 + i / 30.0, block=True)
        assert rec.drain(timeout=10.0)
        with rec.snapshot() as snap:
            assert 9 <= len(snap) <= 11
    finally:
        rec.stop()


def test_evicts_frames_past_the_retention_window(tmp_path):
    """Only the newest `minutes` worth survives; the oldest frames roll off."""
    rec = _recorder(tmp_path, minutes=1.0 / 60.0)   # 1 second of retention
    try:
        _feed(rec, 40, start_t=300.0, step=0.1)     # 4 seconds of frames
        with rec.snapshot() as snap:
            assert snap.duration_s <= 1.0 + 0.15
            assert 0 < len(snap) < 40
            # What survived is the tail, so the newest frame is still there.
            assert abs(int(snap.frame(len(snap) - 1)[0, 0, 0]) - 39) <= 2
    finally:
        rec.stop()


def test_evicted_segment_files_are_deleted(tmp_path):
    """Disk use is bounded: rolled-off segments go away, not just index entries."""
    rec = _recorder(tmp_path, minutes=1.0 / 60.0, fps=20.0)
    try:
        # _SEGMENT_SECONDS is 20 s of capture time, so walk the clock well past it.
        _feed(rec, 200, start_t=400.0, step=0.5)    # 100 seconds
        assert rec.stats()["segments"] <= 3
        assert len(list((tmp_path / "buffer").glob("*.gaseg"))) <= 3
    finally:
        rec.stop()


def test_size_budget_also_evicts(tmp_path):
    """With a tiny disk budget the window shrinks below the time budget."""
    rec = _recorder(tmp_path, minutes=60.0, max_disk_mb=64)
    rec.max_bytes = 6 * 1024                        # ~a few frames' worth
    try:
        _feed(rec, 40)
        stats = rec.stats()
        assert stats["indexed_bytes"] <= 6 * 1024
        assert 0 < stats["frames"] < 40
    finally:
        rec.stop()


def test_snapshot_is_frozen_and_keeps_its_data(tmp_path):
    """A snapshot pins its segments, so a review session outlives eviction."""
    rec = _recorder(tmp_path, minutes=1.0 / 60.0)
    try:
        _feed(rec, 10, start_t=500.0, step=0.1)
        snap = rec.snapshot()
        pinned = len(snap)
        assert pinned == 10
        # Push everything the snapshot holds out of the retention window.
        _feed(rec, 10, start_t=600.0, step=0.1)
        assert len(snap) == pinned                  # frame list is frozen
        assert snap.frame(0) is not None            # ...and still readable
        snap.close()
        with rec.snapshot() as live:
            assert len(live) < 20                   # the live buffer did roll
    finally:
        rec.stop()


def test_index_for_time_holds_the_last_frame_across_a_gap(tmp_path):
    rec = _recorder(tmp_path, fps=10.0)
    try:
        for i, t in enumerate([700.0, 700.1, 700.2, 705.0, 705.1]):
            rec.feed(_frame(i), t=t, block=True)
        assert rec.drain(timeout=10.0)
        with rec.snapshot() as snap:
            assert len(snap) == 5
            assert snap.duration_s == pytest.approx(5.1, abs=0.01)
            assert snap.index_for_time(0.0) == 0
            assert snap.index_for_time(0.15) == 1
            assert snap.index_for_time(3.0) == 2    # mid-gap: hold frame 2
            assert snap.index_for_time(5.0) == 3
            assert snap.index_for_time(99.0) == 4   # past the end: clamp
    finally:
        rec.stop()


def test_downscales_tall_sources(tmp_path):
    rec = _recorder(tmp_path, max_height=120)
    try:
        rec.feed(np.full((1080, 1920, 3), 90, dtype=np.uint8), t=800.0, block=True)
        assert rec.drain(timeout=10.0)
        with rec.snapshot() as snap:
            h, w = snap.frame(0).shape[:2]
            assert h == 120 and w == 212           # 16:9, rounded to even
    finally:
        rec.stop()


def test_feed_is_ignored_when_not_running(tmp_path):
    rec = RollingRecorder(tmp_path / "buffer")
    assert rec.feed(_frame(1), t=900.0) is False
    assert rec.stats()["frames"] == 0


def test_stop_discards_the_buffer(tmp_path):
    rec = _recorder(tmp_path)
    _feed(rec, 5)
    rec.stop(discard=True)
    assert list((tmp_path / "buffer").glob("*.gaseg")) == []
    assert rec.stats()["frames"] == 0


def test_start_purges_a_previous_run(tmp_path):
    stale = tmp_path / "buffer"
    stale.mkdir()
    (stale / "seg_000000.gaseg").write_bytes(b"junk from a crashed run")
    rec = _recorder(tmp_path)
    try:
        assert list(stale.glob("*.gaseg")) == []
    finally:
        rec.stop()


def test_export_writes_a_playable_file(tmp_path):
    rec = _recorder(tmp_path)
    try:
        _feed(rec, 12)
        with rec.snapshot() as snap:
            out = export(snap, tmp_path / "clip.mp4")
        assert out.exists() and out.stat().st_size > 0
        cap = cv2.VideoCapture(str(out))
        try:
            assert cap.isOpened()
            read = 0
            while True:
                ok, _ = cap.read()
                if not ok:
                    break
                read += 1
        finally:
            cap.release()
        assert read == 12
    finally:
        rec.stop()


def test_export_reports_progress_and_honours_cancel(tmp_path):
    rec = _recorder(tmp_path)
    try:
        _feed(rec, 40)
        with rec.snapshot() as snap:
            seen = []
            cancel = threading.Event()

            def progress(done, total):
                seen.append((done, total))
                if done >= 16:
                    cancel.set()

            with pytest.raises(ExportCancelled):
                export(snap, tmp_path / "cancelled.mp4",
                       progress=progress, cancel=cancel)
        assert seen and seen[0][1] == 40
        # The partial file must not be left behind for the user to find.
        assert not (tmp_path / "cancelled.mp4").exists()
    finally:
        rec.stop()


def test_export_rejects_an_empty_buffer(tmp_path):
    rec = _recorder(tmp_path)
    try:
        with rec.snapshot() as snap:
            with pytest.raises(ValueError):
                export(snap, tmp_path / "empty.mp4")
    finally:
        rec.stop()
