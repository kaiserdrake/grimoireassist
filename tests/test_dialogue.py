"""Unit tests for dialogue-region statement assembly (no OCR engine needed)."""
from grimoireassist.dialogue import (
    StatementReader, assemble_statement, clean_statement, is_speakable,
)


def _line(text, x=0, y=0, w=200, h=30, conf=0.9):
    return (text, conf, (x, y, w, h))


# ---------------------------------------------------------------- cleaning
def test_advance_arrow_and_box_glyphs_are_stripped():
    # The blinking "press A to continue" marker must never be spoken.
    assert clean_statement("Watch your step here. ▼") == "Watch your step here."


def test_run_of_punctuation_is_collapsed():
    assert clean_statement("Well........ maybe") == "Well... maybe"


def test_space_before_punctuation_is_closed_up():
    assert clean_statement("Really ?  I doubt it .") == "Really? I doubt it."


def test_fragments_are_not_speakable():
    assert not is_speakable("Hm")
    assert not is_speakable("!?!?")
    assert is_speakable("Look out behind you")


# ---------------------------------------------------------------- assembly
def test_lines_are_joined_in_reading_order_not_detection_order():
    # EasyOCR returns detections in detector order; the boxes decide the sentence.
    lines = [_line("that one bites.", y=40), _line("Careful —", y=0)]
    assert assemble_statement(lines) == "Careful — that one bites."


def test_hyphenated_line_break_is_rejoined_without_a_space():
    lines = [_line("The Rathal-", y=0), _line("os is circling.", y=40)]
    assert assemble_statement(lines) == "The Rathalos is circling."


def test_assembly_without_geometry_keeps_given_order():
    lines = [("Hold on,", 0.9, None), ("something's coming.", 0.9, None)]
    assert assemble_statement(lines) == "Hold on, something's coming."


def test_empty_assembly():
    assert assemble_statement([]) == ""


# ---------------------------------------------------------------- reader
def test_statement_waits_for_the_text_to_settle():
    r = StatementReader(stable_frames=2)
    assert r.update("Watch out for the tail", 1.0) is None   # first sighting
    assert r.update("Watch out for the tail", 2.0) == "Watch out for the tail"


def test_settled_statement_is_reported_once_only():
    r = StatementReader(stable_frames=2)
    r.update("Watch out for the tail", 1.0)
    r.update("Watch out for the tail", 2.0)
    assert r.update("Watch out for the tail", 3.0) is None
    assert r.update("Watch out for the tail", 4.0) is None


def test_typed_out_line_yields_only_the_new_tail():
    """Dialogue types out over several polls; the start must not be re-read."""
    r = StatementReader(stable_frames=2)
    r.update("Careful —", 1.0)
    assert r.update("Careful —", 2.0) == "Careful —"
    r.update("Careful — that one bites", 3.0)
    assert r.update("Careful — that one bites", 4.0) == "that one bites"


def test_ocr_jitter_in_the_already_read_part_is_tolerated():
    # "Careful" re-read as "CarefuI" must still count as the same prefix.
    r = StatementReader(stable_frames=1, min_chars=3)
    assert r.update("Careful there friend", 1.0) == "Careful there friend"
    assert r.update("CarefuI there friend, it bites", 2.0) == "it bites"


def test_unrelated_next_statement_is_read_in_full():
    r = StatementReader(stable_frames=1)
    assert r.update("Careful there", 1.0) == "Careful there"
    assert r.update("The village is this way", 2.0) == "The village is this way"


def test_blinking_box_does_not_repeat_the_line():
    """Box vanishes behind an animation and comes back with the same text."""
    r = StatementReader(stable_frames=1, repeat_window_s=25.0)
    assert r.update("The village is this way", 1.0) == "The village is this way"
    r.update("", 2.0)                                   # covered by an animation
    assert r.update("The village is this way", 3.0) is None


def test_the_same_line_is_read_again_once_the_window_has_passed():
    r = StatementReader(stable_frames=1, repeat_window_s=25.0)
    assert r.update("The village is this way", 1.0) == "The village is this way"
    r.update("", 2.0)
    assert r.update("The village is this way", 40.0) == "The village is this way"


def test_fragment_reads_are_never_spoken():
    r = StatementReader(stable_frames=1, min_chars=6)
    assert r.update("Hm", 1.0) is None


def test_fragment_that_grows_into_a_sentence_is_spoken_whole():
    r = StatementReader(stable_frames=1, min_chars=6)
    assert r.update("Hm", 1.0) is None
    assert r.update("Hm, that is odd", 2.0) == "Hm, that is odd"


def test_reset_forgets_everything():
    r = StatementReader(stable_frames=1)
    assert r.update("The village is this way", 1.0) == "The village is this way"
    r.reset()
    assert r.update("The village is this way", 2.0) == "The village is this way"
