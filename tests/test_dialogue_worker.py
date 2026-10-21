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


# ── labels region (map / menu) ──────────────────────────────────────────────
def _labels_worker(engine, speech=False):
    cfg = Config()
    cfg.speech.enabled = speech          # labels don't depend on narration
    cfg.ocr.regions_labels = [Region(10, 150, 300, 60)]
    worker = OcrWorker(cfg, None, engine)
    seen = []
    worker.labels_seen.connect(seen.append)
    return worker, seen


def _menu():
    return [("Iron Sword", 0.9, (0, 0, 120, 18)), ("Mega Potion x3", 0.9, (0, 22, 140, 18))]


def test_a_label_is_sent_after_two_matching_reads_and_only_once():
    worker, seen = _labels_worker(_FakeEngine(_menu()))
    worker._read_labels(_textured_frame(), 1.0)
    assert seen == []                                   # one read: not trusted yet
    worker._read_labels(_textured_frame(), 2.5)          # heartbeat re-read
    assert [[t.text for t in batch] for batch in seen] == [["Iron Sword", "Mega Potion"]]
    worker._read_labels(_textured_frame(), 4.0)
    assert len(seen) == 1                               # same screen: not again


def test_a_still_labels_region_is_not_re_read_before_the_heartbeat():
    engine = _FakeEngine(_menu())
    worker, _seen = _labels_worker(engine)
    worker._read_labels(_textured_frame(), 1.0)
    worker._read_labels(_textured_frame(), 1.2)
    assert engine.reads == 1


def test_labels_come_back_after_the_screen_closes():
    script = [_menu(), _menu(), [], _menu(), _menu()]
    worker, seen = _labels_worker(_FakeEngine(script=script))
    for i in range(5):
        worker._read_labels(_textured_frame(), 1.0 + 2 * i)
    assert len(seen) == 2


def test_no_labels_region_means_no_reads():
    engine = _FakeEngine(_menu())
    worker, _seen = _labels_worker(engine)
    worker.cfg.ocr.regions_labels = []
    worker._read_labels(_textured_frame(), 1.0)
    assert engine.reads == 0


# ── several labels regions ──────────────────────────────────────────────────
_TOP = Region(10, 20, 300, 60)      # dimmer panel: crop mean ≈ 95
_BOTTOM = Region(10, 150, 300, 60)  # brighter panel: crop mean ≈ 140


class _PanelEngine:
    """Answers per panel, told apart by brightness. `top` / `bottom` are the
    lines each panel currently shows; change them to open or close a screen."""

    def __init__(self, top, bottom):
        self.top, self.bottom = list(top), list(bottom)

    def read_lines(self, image):
        return list(self.bottom if float(image.mean()) > 117 else self.top)


def _two_panel_frame():
    frame = _textured_frame()
    frame[20:80, 10:310] = 60
    frame[30:70, 20:280] = 120
    return frame


def _two_region_worker(engine):
    cfg = Config()
    cfg.ocr.regions_labels = [_TOP, _BOTTOM]
    worker = OcrWorker(cfg, None, engine)
    seen = []
    worker.labels_seen.connect(seen.append)
    return worker, seen


def _texts(seen):
    return [[t.text for t in batch] for batch in seen]


def test_every_labels_region_is_read():
    engine = _PanelEngine(top=[("Iron Sword", 0.9, (0, 0, 120, 18))],
                          bottom=[("Mega Potion", 0.9, (0, 0, 120, 18))])
    worker, seen = _two_region_worker(engine)
    worker._read_labels(_two_panel_frame(), 1.0)
    worker._read_labels(_two_panel_frame(), 2.5)
    assert _texts(seen) == [["Iron Sword", "Mega Potion"]]
    assert len(worker._lbl_boxes) == 2            # one line from each region


def test_the_same_label_in_two_regions_is_sent_once():
    line = [("Iron Sword", 0.9, (0, 0, 120, 18))]
    worker, seen = _two_region_worker(_PanelEngine(top=line, bottom=line))
    worker._read_labels(_two_panel_frame(), 1.0)
    worker._read_labels(_two_panel_frame(), 2.5)
    assert _texts(seen) == [["Iron Sword"]]


def test_labels_regions_keep_their_own_screen_state():
    """One menu closing and reopening re-sends its labels — but not those of
    the map that stayed up in the other region."""
    engine = _PanelEngine(top=[("Iron Sword", 0.9, (0, 0, 120, 18))],
                          bottom=[("Mega Potion", 0.9, (0, 0, 120, 18))])
    worker, seen = _two_region_worker(engine)
    t = 1.0
    for _ in range(2):
        worker._read_labels(_two_panel_frame(), t)
        t += 2
    engine.bottom = []                             # the menu closes
    worker._read_labels(_two_panel_frame(), t)
    t += 2
    engine.bottom = [("Mega Potion", 0.9, (0, 0, 120, 18))]  # ...and reopens
    for _ in range(2):
        worker._read_labels(_two_panel_frame(), t)
        t += 2
    assert _texts(seen) == [["Iron Sword", "Mega Potion"], ["Mega Potion"]]


def test_removing_a_labels_region_drops_its_boxes():
    engine = _PanelEngine(top=[("Iron Sword", 0.9, (0, 0, 120, 18))],
                          bottom=[("Mega Potion", 0.9, (0, 0, 120, 18))])
    worker, _seen = _two_region_worker(engine)
    worker._read_labels(_two_panel_frame(), 1.0)
    worker.cfg.ocr.regions_labels = [_TOP]
    worker._read_labels(_two_panel_frame(), 1.2)
    assert worker._lbl_boxes == [(_TOP.x, _TOP.y, 120, 18)]


def test_a_requested_scan_reads_the_whole_frame_once():
    engine = _FakeEngine(_menu())
    worker, _seen = _labels_worker(engine)
    found = []
    worker.screen_terms.connect(found.append)
    worker._scan_if_requested(_textured_frame())
    assert found == [] and engine.reads == 0            # nothing asked for
    worker.request_scan()
    worker._scan_if_requested(_textured_frame())
    worker._scan_if_requested(_textured_frame())
    assert [[t.text for t in batch] for batch in found] == [["Iron Sword", "Mega Potion"]]
    assert found[0][0].source == "screen"
