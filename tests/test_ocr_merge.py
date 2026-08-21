"""Unit tests for EasyOCR box-merging (no torch/model needed)."""
from grimoireassist.ocr.easyocr_engine import _merge_adjacent_boxes


def _box(text, left, right, ycenter=100.0, height=40.0, conf=0.9):
    return {"text": text, "conf": conf, "left": left, "right": right,
            "ycenter": ycenter, "height": height}


def test_split_name_on_same_line_is_merged():
    # "Ivory" and "Lagiacrus" sit side-by-side on one line -> one name,
    # with a box spanning both parts.
    boxes = [_box("Ivory", 0, 80), _box("Lagiacrus", 90, 260)]
    assert _merge_adjacent_boxes(boxes) == [
        ("Ivory Lagiacrus", 0.9, (0, 80, 260, 40))]


def test_distant_text_on_same_line_stays_separate():
    # A wide horizontal gap (UI label far to the right) is not absorbed.
    boxes = [_box("Lagiacrus", 0, 170), _box("Plasma Zone", 600, 800)]
    out = [t for t, _, _ in _merge_adjacent_boxes(boxes)]
    assert out == ["Lagiacrus", "Plasma Zone"]


def test_names_on_different_lines_stay_separate():
    # Two monsters stacked vertically remain two entries with their own boxes.
    merged = _merge_adjacent_boxes([_box("Rathalos", 0, 150, ycenter=100.0),
                                    _box("Lagiacrus", 0, 170, ycenter=200.0)])
    assert [t for t, _, _ in merged] == ["Rathalos", "Lagiacrus"]
    assert [b for _, _, b in merged] == [(0, 80, 150, 40), (0, 180, 170, 40)]


def test_empty_input():
    assert _merge_adjacent_boxes([]) == []


def test_line_parts_with_jittered_tops_keep_their_order():
    """The parts of one visual line rarely share an exact vertical centre.

    When the right-hand part sits a shade higher — which is what the OCR
    actually reports — ordering on the raw centre emits it first, reversing the
    halves of the sentence. This is the geometry observed in a real read.
    """
    boxes = [_box("into the water-", 261, 380, ycenter=60.5),
             _box("If something strange ever got", 7, 250, ycenter=62.0)]
    merged = _merge_adjacent_boxes(boxes)
    assert " ".join(t for t, _c, _b in merged) ==         "If something strange ever got into the water-"


def test_a_jittered_two_word_name_is_not_reversed():
    """The same merge feeds monster-name matching, so a reversed pair there
    means the wrong monster (or none at all)."""
    boxes = [_box("Lagiacrus", 90, 260, ycenter=99.0),
             _box("Ivory", 0, 80, ycenter=101.0)]
    assert _merge_adjacent_boxes(boxes)[0][0] == "Ivory Lagiacrus"


def test_a_real_next_line_is_still_a_separate_entry():
    """The row tolerance must not swallow the line below."""
    boxes = [_box("first line", 0, 150, ycenter=100.0, height=40.0),
             _box("second line", 0, 150, ycenter=160.0, height=40.0)]
    assert [t for t, _c, _b in _merge_adjacent_boxes(boxes)] == [
        "first line", "second line"]
