"""The OCR worker's dialogue path: when it reads, what it emits, when it resets.

Drives `OcrWorker._read_dialogue` directly with a fake engine — no camera, no
model, and the thread is never started.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from grimoireassist.config import Config, Region  # noqa: E402

OcrWorker = pytest.importorskip("grimoireassist.battle").OcrWorker


class _FakeEngine:
    """Returns canned lines and counts how often it was asked to read.

    `script` (when given) supplies a different set of lines per call, standing in
    for a line of dialogue typing itself out."""

    def __init__(self, lines=(), script=None):
        self.lines = list(lines)
        self.script = list(script) if script else None
        self.reads = 0

    def read_lines(self, image):
        self.reads += 1
        if self.script is None:
            return list(self.lines)
        idx = min(self.reads - 1, len(self.script) - 1)
        return list(self.script[idx])

    def read_text(self, image):
        return " ".join(t for t, _c, _b in self.read_lines(image))


def _textured_frame():
    """A frame with real contrast — a flat one is skipped as 'no box on screen'."""
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[150:210, 10:310] = 30          # the dialogue box
    frame[160:200, 20:280] = 220         # "text" inside it
    return frame


def _flat_frame():
    return np.full((240, 320, 3), 17, dtype=np.uint8)


def _line(text):
    return [(text, 0.9, (0, 0, 200, 20))]


def _worker(engine, enabled=True, region=Region(10, 150, 300, 60)):
    cfg = Config()
    cfg.speech.enabled = enabled
    cfg.speech.stable_frames = 2
    cfg.ocr.regions_dialogue = region
    worker = OcrWorker(cfg, None, engine)
    said = []
    worker.dialogue_text.connect(said.append)
    return worker, cfg, said


def test_statement_is_emitted_once_the_text_settles():
    worker, _cfg, said = _worker(_FakeEngine(_line("Careful, that one bites.")))
    worker._read_dialogue(_textured_frame(), 1.0)
    assert said == []                       # first read: not settled yet
    worker._read_dialogue(_textured_frame(), 2.0)
    assert said == ["Careful, that one bites."]


def test_a_half_typed_line_is_not_spoken():
    """The regression that matters: only the finished sentence gets narrated.

    The region looks near-identical from frame to frame while the text types
    out, so settling has to be decided on the text, not on the pixels."""
    script = [_line(t) for t in ("Careful,", "Careful, that one",
                                 "Careful, that one bites.",
                                 "Careful, that one bites.")]
    worker, _cfg, said = _worker(_FakeEngine(script=script))
    for i in range(4):
        worker._read_dialogue(_textured_frame(), float(i))
    assert said == ["Careful, that one bites."]


def test_the_region_is_read_every_poll_while_narration_is_on():
    engine = _FakeEngine(_line("The village is this way."))
    worker, _cfg, _said = _worker(engine)
    for i in range(3):
        worker._read_dialogue(_textured_frame(), float(i))
    assert engine.reads == 3


def test_a_flat_region_is_never_sent_to_the_engine():
    """No box on screen costs no inference."""
    engine = _FakeEngine(_line("unreachable"))
    worker, _cfg, said = _worker(engine)
    worker._read_dialogue(_flat_frame(), 1.0)
    worker._read_dialogue(_flat_frame(), 2.0)
    assert engine.reads == 0 and said == []


def test_nothing_is_read_while_narration_is_off():
    engine = _FakeEngine(_line("Careful, that one bites."))
    worker, _cfg, said = _worker(engine, enabled=False)
    worker._read_dialogue(_textured_frame(), 1.0)
    worker._read_dialogue(_textured_frame(), 2.0)
    assert said == [] and engine.reads == 0


def test_nothing_is_read_when_no_dialogue_region_is_calibrated():
    engine = _FakeEngine(_line("Careful, that one bites."))
    worker, _cfg, _said = _worker(engine, region=Region())
    worker._read_dialogue(_textured_frame(), 1.0)
    assert engine.reads == 0


def test_switching_narration_off_and_on_re_reads_the_box():
    engine = _FakeEngine(_line("Hold on, something's coming."))
    worker, cfg, said = _worker(engine)
    worker._read_dialogue(_textured_frame(), 1.0)
    cfg.speech.enabled = False
    worker._read_dialogue(_textured_frame(), 2.0)   # off: state cleared
    cfg.speech.enabled = True
    worker._read_dialogue(_textured_frame(), 3.0)
    worker._read_dialogue(_textured_frame(), 4.0)
    assert said == ["Hold on, something's coming."]


def test_dialogue_boxes_are_reported_in_frame_coordinates():
    engine = _FakeEngine([("Careful.", 0.9, (5, 7, 100, 20))])
    worker, _cfg, _said = _worker(engine, region=Region(10, 150, 300, 60))
    worker._read_dialogue(_textured_frame(), 1.0)
    assert worker._dlg_boxes == [(15, 157, 100, 20)]
