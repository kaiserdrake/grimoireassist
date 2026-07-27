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
