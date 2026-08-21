"""Unit tests for dialogue-region statement assembly (no OCR engine needed)."""
from grimoireassist.dialogue import (
    StatementReader, assemble_statement, clean_statement, is_speakable,
    split_header,
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
    r.update("Careful with that one —", 1.0)
    assert r.update("Careful with that one —", 2.0) == "Careful with that one —"
    r.update("Careful with that one — it bites when cornered", 3.0)
    assert r.update("Careful with that one — it bites when cornered",
                    4.0) == "it bites when cornered"


def test_ocr_jitter_in_the_already_read_part_is_tolerated():
    # "Careful" re-read as "CarefuI" must still count as the same prefix.
    r = StatementReader(stable_frames=1, min_chars=3)
    assert r.update("Careful there friend", 1.0) == "Careful there friend"
    assert r.update("CarefuI there friend, it bites", 2.0) == "it bites"


def test_unrelated_next_statement_is_read_in_full():
    r = StatementReader(stable_frames=1)
    assert r.update("Careful there friend", 1.0) == "Careful there friend"
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


# --------------------------------------------------------------- reading order
def test_a_line_split_into_two_boxes_keeps_its_word_order():
    """OCR splits one visual line into boxes whose tops differ by a pixel or
    two. Sorting on raw y interleaves them and the listener hears the halves
    back to front."""
    lines = [
        ("If something strange ever got", 0.9, (12, 40, 250, 22)),
        ("into the water-", 0.9, (266, 38, 120, 22)),   # same line, 2px higher
        ("purification plant, it could be trouble.", 0.9, (12, 67, 400, 22)),
    ]
    assert assemble_statement(lines) == (
        "If something strange ever got into the waterpurification plant, "
        "it could be trouble.")


def test_boxes_are_still_ordered_top_to_bottom():
    lines = [
        ("second line", 0.9, (10, 60, 100, 20)),
        ("first line", 0.9, (10, 20, 100, 20)),
    ]
    assert assemble_statement(lines) == "first line second line"


def test_a_genuinely_lower_box_is_not_folded_into_the_line_above():
    """The row tolerance must not swallow a real next line."""
    lines = [
        ("right half", 0.9, (200, 20, 100, 20)),
        ("left half", 0.9, (10, 20, 100, 20)),
        ("next line", 0.9, (10, 60, 100, 20)),
    ]
    assert assemble_statement(lines) == "left half right half next line"


def test_detections_without_geometry_keep_the_order_given():
    lines = [("first", 0.9, None), ("second", 0.9, None)]
    assert assemble_statement(lines) == "first second"


# ------------------------------------------------ repeats despite OCR jitter
def test_a_re_read_with_one_word_different_is_not_spoken_again():
    """Taken from a real capture: the same box read twice gave 'in front of a
    house' and 'in front of house'. An exact-match repeat check calls that a new
    statement and reads the whole line a second time."""
    reader = StatementReader(stable_frames=1, repeat_window_s=25.0)
    first = ("Heart-to-Heart Info At sunset, I heard dogs barking at Irina "
             "in front of a house near the cathedral.")
    again = ("Heart-to-Heart Info At sunset, I heard dogs barking at Irina "
             "in front of house near the cathedral.")
    assert reader.update(first, 0.0) == first
    reader.update("", 1.0)                     # box blinks away and returns
    assert reader.update(again, 2.0) is None


def test_a_genuinely_different_line_still_gets_spoken():
    """The fuzzy check must not swallow the next line of a conversation."""
    reader = StatementReader(stable_frames=1, repeat_window_s=25.0)
    first = "At sunset, I heard dogs barking at Irina near the cathedral."
    second = "The supply drop landed somewhere north of the barracks."
    assert reader.update(first, 0.0) == first
    reader.update("", 1.0)
    assert reader.update(second, 2.0) == second


def test_the_same_line_is_allowed_again_after_the_window():
    reader = StatementReader(stable_frames=1, repeat_window_s=10.0)
    line = "At sunset, I heard dogs barking at Irina near the cathedral."
    assert reader.update(line, 0.0) == line
    reader.update("", 1.0)
    assert reader.update(line, 30.0) == line


# ------------------------------------------------------- headers vs dialogue
def _hline(text, y, h=20, x=29, w=250):
    """A detection at a real screen position (the header tests care about y)."""
    return (text, 0.9, (x, y, w, h))


def test_a_box_header_is_separated_from_the_prose():
    """Geometry taken from a real capture: header at y=107, body from y=139."""
    lines = [_hline("Heart-to-Heart Info", 107, x=39, w=160),
             _hline("At sunset, I heard dogs barking at Irina", 139),
             _hline("in front of a house near the cathedral.", 161, h=16)]
    header, body = split_header(lines)
    assert header == "Heart-to-Heart Info"
    assert "Heart-to-Heart" not in assemble_statement(body)


def test_a_speaker_label_is_treated_the_same_way():
    lines = [_hline("Male BLADE", 107, x=41, w=106),
             _hline("If you meet a Curator who calls herself", 141, h=18),
             _hline("the Murderess, watch out.", 159)]
    header, body = split_header(lines)
    assert header == "Male BLADE"
    assert assemble_statement(body).startswith("If you meet")


def test_a_wrapped_sentence_is_not_mistaken_for_a_header():
    """Short and unpunctuated, but at the body's own line spacing — mislabelling
    it would silently drop the first half of the line."""
    lines = [_hline("The supply drop landed", 139, w=200),
             _hline("somewhere north of the barracks.", 161, h=18)]
    header, body = split_header(lines)
    assert header == ""
    assert assemble_statement(body) == (
        "The supply drop landed somewhere north of the barracks.")


def test_a_long_first_line_is_never_a_header():
    lines = [_hline("If something strange ever got into the water treatment plant", 107),
             _hline("it could be serious trouble.", 139)]
    assert split_header(lines)[0] == ""


def test_a_first_line_ending_like_a_sentence_is_never_a_header():
    lines = [_hline("Look out!", 107, w=90),
             _hline("That one bites when cornered.", 139)]
    assert split_header(lines)[0] == ""


def test_a_lone_line_has_no_header():
    assert split_header([_hline("Just the one line here.", 139)])[0] == ""


def test_lines_without_geometry_are_left_alone():
    lines = [("Male BLADE", 0.9, None), ("If you meet a Curator", 0.9, None)]
    assert split_header(lines)[0] == ""


# ------------------------------------------- headers found by colour instead
YELLOW = (191, 190, 129)     # the "Heart-to-Heart Info" accent, measured
CYAN = (180, 208, 211)       # the "Male BLADE" accent, measured
WHITE = (190, 195, 190)      # body prose


def test_colour_alone_identifies_a_header_at_body_line_spacing():
    """The false negative the geometry rule alone would produce: a label drawn
    in the box's accent colour but sitting at the body's own line spacing."""
    lines = [_hline("Male BLADE", 139, w=106),
             _hline("If you meet a Curator who calls herself", 161, h=18),
             _hline("the Murderess, watch out.", 179)]
    assert split_header(lines)[0] == ""                      # spacing says no
    header, body = split_header(lines, [CYAN, WHITE, WHITE])
    assert header == "Male BLADE"                            # colour says yes
    assert assemble_statement(body).startswith("If you meet")


def test_a_yellow_header_is_identified_by_colour_too():
    lines = [_hline("Heart-to-Heart Info", 139, w=160),
             _hline("At sunset, I heard dogs barking at Irina", 161),
             _hline("in front of a house near the cathedral.", 179)]
    assert split_header(lines, [YELLOW, WHITE, WHITE])[0] == "Heart-to-Heart Info"


def test_prose_in_the_body_colour_is_never_taken_for_a_header():
    lines = [_hline("The supply drop landed", 139, w=200),
             _hline("somewhere north of the barracks.", 161, h=18)]
    assert split_header(lines, [WHITE, WHITE])[0] == ""


def test_a_brightness_difference_is_not_a_colour_difference():
    """Anti-aliasing and video compression move brightness, not hue — reading a
    dimmer first line as a header would swallow dialogue."""
    dim, bright = (120, 124, 121), (210, 216, 212)
    lines = [_hline("The supply drop landed", 139, w=200),
             _hline("somewhere north of the barracks.", 161, h=18)]
    assert split_header(lines, [dim, bright])[0] == ""


def test_missing_colours_fall_back_to_spacing_alone():
    lines = [_hline("Male BLADE", 107, w=106),
             _hline("If you meet a Curator.", 141, h=18)]
    assert split_header(lines, [None, None])[0] == "Male BLADE"


def test_a_wrong_length_colour_list_is_ignored_rather_than_misaligned():
    lines = [_hline("Male BLADE", 107, w=106),
             _hline("If you meet a Curator.", 141, h=18)]
    assert split_header(lines, [CYAN])[0] == "Male BLADE"   # spacing still works


# ------------------------------------------- UI clutter caught in the region
def test_two_labels_far_apart_are_not_joined_into_one_statement():
    """From a real map screen: the button-hint bar clipped into the dialogue
    region, and "Related Tips" (ending x=147) and "Find Self" (starting x=491)
    were glued into the statement "Related Tips Find Self" and spoken."""
    lines = [("Related Tips", 0.93, (59, 241, 88, 18)),
             ("Find Self", 0.93, (491, 239, 63, 20))]
    assert assemble_statement(lines) != "Related Tips Find Self"
    assert assemble_statement(lines) == "Related Tips"       # the larger block


def test_neither_of_those_labels_is_speakable():
    for label in ("Related Tips", "Find Self", "Place Marker",
                  "Undeveloped Area", "Residential District", "Map Shortcuts"):
        assert not is_speakable(label, 6), label


def test_a_normal_line_of_prose_is_still_speakable():
    for line in ("At sunset, I heard dogs barking at Irina near the cathedral.",
                 "That woman is just terrible with animals",
                 "The village is this way"):
        assert is_speakable(line, 6), line


def test_a_short_line_that_ends_like_a_sentence_is_still_dialogue():
    """Length alone cannot separate "Look out!" from "Related Tips"."""
    assert is_speakable("Look out!", 6)
    assert is_speakable("Careful.", 6)


def test_words_of_one_line_are_not_torn_apart_by_the_block_split():
    """Real intra-line gaps are a fraction of the text height; the split must
    sit far enough above them to never cut a sentence in half."""
    lines = [("If something strange ever got", 0.9, (7, 139, 250, 20)),
             ("into the water-", 0.9, (261, 139, 120, 20))]
    assert assemble_statement(lines) == (
        "If something strange ever gotinto the water-".replace("got", "got "))


def test_a_continuation_is_spoken_even_when_the_tail_is_short():
    """The prose guard judges the whole statement; a two-word tail that
    completes a real sentence must still be read."""
    r = StatementReader(stable_frames=1)
    first = "Careful with that one, it looks hungry"
    assert r.update(first, 1.0) == first
    assert r.update(first + " and angry", 2.0) == "and angry"


def test_stacked_labels_at_different_margins_are_separate_elements():
    """The map screen again: a map label above a button hint. They are on
    different rows, so the horizontal rule never sees them — what separates
    them is that a paragraph shares a left margin and these do not."""
    lines = [("Undeveloped Area", 0.89, (232, 202, 202, 28)),
             ("Related Tips", 0.93, (59, 241, 88, 18))]
    assert assemble_statement(lines) != "Undeveloped Area Related Tips"
    assert not is_speakable(assemble_statement(lines), 6)


def test_lines_of_one_paragraph_share_a_margin_and_stay_together():
    """Body lines of the real capture all began within a pixel or two."""
    lines = [("At sunset, I heard dogs barking at Irina", 0.9, (29, 139, 262, 20)),
             ("in front of a house near the cathedral.", 0.9, (27, 161, 254, 16)),
             ("That woman is just terrible with animals", 0.9, (27, 179, 268, 18))]
    assert assemble_statement(lines) == (
        "At sunset, I heard dogs barking at Irina in front of a house near "
        "the cathedral. That woman is just terrible with animals")


def test_a_slightly_indented_line_still_belongs_to_the_paragraph():
    lines = [("Careful with that one, it looks", 0.9, (39, 139, 240, 20)),
             ("hungry and rather annoyed.", 0.9, (27, 161, 220, 20))]
    assert assemble_statement(lines).startswith("Careful with that one")
    assert "hungry and rather annoyed." in assemble_statement(lines)
