"""Dialogue-region text handling: OCR lines in, whole spoken statements out.

The dialogue region sits over the game's subtitle / NPC chatter box. Unlike the
monster regions — where each read is matched against a known name list — this
region carries free-form prose, so the job here is the opposite one: glue the
per-line OCR fragments back into the sentence a human would read aloud, and work
out *when* that sentence is finished.

Two game-UI behaviours drive the design:

* Dialogue types out one character at a time, so successive polls see a growing
  prefix of the same line. `StatementReader` therefore waits for the text to stop
  changing, and when a settled statement merely extends the previous one it
  reports only the new tail — a long speech is narrated once, in order, instead
  of being re-read from the top every time another clause appears.
* Boxes blink, get covered by animations, and re-appear with the same text.
  A short recency window suppresses those repeats.

This module is pure logic (no Qt, no OCR engine), so it unit-tests directly.
"""
from __future__ import annotations

import difflib
import re
from collections import deque
from typing import Deque, List, Optional, Tuple

# Characters worth sending to a speech synthesiser. Game dialogue boxes are full
# of decoration the OCR happily reads as text — the blinking ▼ "press A to
# continue" arrow, button glyphs, box borders — and every one of them would be
# spoken as noise or split a sentence in two.
_KEEP = re.compile(r"[^0-9A-Za-zÀ-ɏ \t.,!?;:'\"()\[\]/&%$#@+=…‥·\-–—‘’“”]+")
_REPEATS = re.compile(r"([.,!?;:\-–—])\1{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?;:])")
_KEY = re.compile(r"[^a-z0-9 ]+")


def clean_statement(text: str) -> str:
    """Strip OCR/UI noise from a raw read and normalise its whitespace."""
    if not text:
        return ""
    out = _KEEP.sub(" ", text)
    out = _REPEATS.sub(r"\1\1\1", out)          # "......" -> "..."
    out = re.sub(r"\s+", " ", out).strip()
    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    # Trim box-border leftovers only. Dashes stay: a trailing em dash is how a
    # half-typed line reads, and a trailing hyphen is a word split across the
    # line break that assemble_statement still needs to see.
    return out.strip(" _|/\\")


def statement_key(text: str) -> str:
    """Comparison key: letters, digits and single spaces only.

    OCR jitters on case and punctuation between otherwise identical reads, so
    stability and repeat checks all run on this rather than the raw text."""
    return re.sub(r"\s+", " ", _KEY.sub("", (text or "").lower())).strip()


# Prose runs to a few words or closes with punctuation. UI furniture caught
# inside the region — button hints, map labels, tab names — is a word or two
# with no sentence shape, and reading it out is worse than saying nothing.
# Every label seen on a real map screen ("Related Tips", "Find Self", "Place
# Marker", "Undeveloped Area", "Residential District") is exactly two words,
# so the bar sits just above them rather than deep into plausible dialogue.
_PROSE_MIN_WORDS = 3


def is_speakable(text: str, min_chars: int = 6) -> bool:
    """Whether a statement looks like prose rather than a label or OCR fragment."""
    key = statement_key(text)
    if len(key) < max(1, min_chars):
        return False
    letters = sum(c.isalpha() for c in key)
    if letters < 0.5 * len(key.replace(" ", "")) or letters < 2:
        return False
    # "Look out!" is dialogue; "Related Tips" is a button hint. Length alone
    # cannot tell them apart, but sentence punctuation can.
    stripped = clean_statement(text)
    if stripped and stripped[-1] in _SENTENCE_END:
        return True
    return len(key.split()) >= _PROSE_MIN_WORDS


def _row_of(entry: tuple) -> float:
    box = entry[2]
    return box[1] + box[3] / 2.0        # vertical centre


def _height_of(entry: tuple) -> float:
    return entry[2][3] or 1.0


def in_reading_order(entries: List[tuple]) -> List[tuple]:
    """Sort detections top-to-bottom, then left-to-right *within each line*.

    Sorting on the raw y is not enough. OCR routinely splits one visual line
    into several boxes whose tops differ by a pixel or two, and a plain
    (y, x) sort then interleaves them across the line — turning "If something
    strange ever got | into the water-" into "into the water- If something
    strange ever got", which is what the listener actually hears.

    Boxes whose vertical centres sit within 60% of their height are treated as
    the same line (the tolerance `_merge_adjacent_boxes` already uses to join
    words) and ordered by x inside it.
    """
    ordered = sorted(entries, key=_row_of)
    rows: List[List[tuple]] = []
    current: List[tuple] = []
    for entry in ordered:
        if current:
            gap = abs(_row_of(entry) - _row_of(current[0]))
            if gap > 0.6 * max(_height_of(entry), _height_of(current[0])):
                rows.append(current)
                current = []
        current.append(entry)
    if current:
        rows.append(current)
    out: List[tuple] = []
    for row in rows:
        out.extend(sorted(row, key=lambda e: e[2][0]))
    return out


# A header sits noticeably further from the first line of prose than the body
# lines sit from each other. Measured on real captures: header->body gaps of
# 12-14px against body gaps of 0-2px, with a line height around 20 — so a
# fraction of the line's own height separates the two cases with room to spare,
# and scales with resolution rather than assuming pixels.
_HEADER_GAP_RATIO = 0.35
# Horizontal gap, as a multiple of text height, that means "separate element"
# rather than "word space". Real intra-line gaps run 0-2x and the observed
# UI-bar case was 19x, so this sits well clear of both: far enough above real
# prose that a line is never torn in half, far enough below the clutter to
# still catch it.
_BLOCK_GAP_RATIO = 8.0
# Lines of one paragraph share a left margin — the body lines of a real box all
# began within a pixel or two of each other. Scattered UI elements do not: a map
# label at x=232 above a button hint at x=59 is 6x the text height apart. The
# bar is generous so an indented first line still counts as the same paragraph.
_BLOCK_MARGIN_RATIO = 3.0
_HEADER_MAX_WORDS = 6
_SENTENCE_END = ".!?…"


# Colour, measured as chromaticity so brightness drops out. Raw RGB distance
# does not work: on a real capture the "Male BLADE" header sat 23.0 from the
# body while body lines sat up to 20.2 apart from each other — no separation at
# all, because anti-aliasing and video compression move brightness far more than
# hue. In chromaticity the same header sits 0.028 from the body against 0.002 of
# body-to-body noise, and the yellow "Heart-to-Heart Info" header 0.088.
_HEADER_CHROMA = 0.012


def chromaticity(colour) -> Optional[Tuple[float, float]]:
    """(r, b) shares of an (R, G, B) triple — its colour with brightness removed."""
    if colour is None:
        return None
    r, g, b = (float(c) for c in colour[:3])
    total = r + g + b
    if total <= 0:
        return None
    return (r / total, b / total)


def colour_differs(colour, reference) -> bool:
    """Whether two glyph colours are different hues rather than two brightnesses."""
    a, b = chromaticity(colour), chromaticity(reference)
    if a is None or b is None:
        return False
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 >= _HEADER_CHROMA


def _median_colour(colours: List) -> Optional[tuple]:
    usable = [c for c in colours if c is not None]
    if not usable:
        return None
    return tuple(sorted(c[i] for c in usable)[len(usable) // 2] for i in range(3))


def looks_like_header(entry: tuple, following: tuple,
                      colour=None, body_colour=None) -> bool:
    """Whether `entry` is a box header / speaker label rather than prose.

    Two guards are mandatory, because getting this wrong in the permissive
    direction silently swallows a line of actual dialogue: the line must be
    short, and it must not end like a sentence. A wrapped sentence ("The supply
    drop landed" / "somewhere north of the barracks") passes both, so on their
    own they are not enough.

    Past that, *either* of two independent observations marks it as a header:

    * it is set apart vertically from the line below it, or
    * it is drawn in a different colour from the prose.

    Either alone is enough because a game may separate its header by only one of
    the two — a label at the body's own line spacing but in the box's accent
    colour is still a label, and requiring both would miss it.
    """
    text = clean_statement(entry[0])
    if not text or len(text.split()) > _HEADER_MAX_WORDS:
        return False
    if text[-1] in _SENTENCE_END:
        return False

    box, next_box = entry[2], following[2]
    set_apart = False
    if box is not None and next_box is not None:
        height = box[3] or 1
        gap = next_box[1] - (box[1] + box[3])
        set_apart = gap > _HEADER_GAP_RATIO * height
    return set_apart or colour_differs(colour, body_colour)


def split_header(lines: List[tuple],
                 colours: Optional[List] = None) -> Tuple[str, List[tuple]]:
    """Separate a leading header from the prose under it.

    Returns `(header_text, remaining_lines)`; the header is "" when the first
    line is just the start of the dialogue. `lines` must already be in reading
    order (see `in_reading_order`). `colours`, when given, is one (R, G, B) per
    line — the colour its glyphs are drawn in — and lets a header be recognised
    by its colour as well as by its spacing.
    """
    if len(lines) < 2:
        return "", list(lines)
    colour = body_colour = None
    if colours and len(colours) == len(lines):
        colour = colours[0]
        body_colour = _median_colour(colours[1:])
    if not looks_like_header(lines[0], lines[1], colour, body_colour):
        return "", list(lines)
    return clean_statement(lines[0][0]), list(lines[1:])


def assemble_statement(lines: List[tuple]) -> str:
    """Join `[(text, confidence, box), ...]` into one statement.

    Lines are ordered the way they are read — top to bottom, then left to right
    within each line — using the boxes when the engine supplies them (EasyOCR
    returns detections in detector order, which is not reading order) and the
    given order when it doesn't. A line ending in a hyphen is treated as a word
    split across the line break and rejoined without a space.
    """
    entries: List[tuple] = list(lines)
    placed = entries and all(len(e) > 2 and e[2] is not None for e in entries)
    if placed:
        entries = in_reading_order(entries)
    blocks = _blocks(entries) if placed else [entries]
    # One paragraph is one block. Several means unrelated things share the
    # region, so speak the substantial one rather than gluing them together.
    return max((_join(b) for b in blocks), key=len, default="")


def _join(entries: List[tuple]) -> str:
    out = ""
    for entry in entries:
        part = clean_statement(entry[0])
        if not part:
            continue
        if not out:
            out = part
        elif out.endswith("-"):
            out = out[:-1] + part
        else:
            out = f"{out} {part}"
    return clean_statement(out)


def _blocks(entries: List[tuple]) -> List[List[tuple]]:
    """Split reading-ordered entries where a row contains separate elements.

    Words inside a line of prose sit fractions of a character-height apart —
    the merge in the OCR engine joins anything under 1.5x. A gap many times
    that is not a word space: it is the other side of the screen. Measured on a
    map screen whose button-hint bar clipped into the region, "Related Tips"
    and "Find Self" sat 344px apart at a text height of 18 (19x), and joining
    them produced the statement "Related Tips Find Self".
    """
    out: List[List[tuple]] = []
    current: List[tuple] = []
    for entry in entries:
        if current:
            previous = current[-1]
            height = max(_height_of(entry), _height_of(previous))
            same_row = abs(_row_of(entry) - _row_of(previous)) <= 0.6 * height
            if same_row:
                # Side by side: a word space, or the other side of the screen?
                gap = entry[2][0] - (previous[2][0] + previous[2][2])
                split = gap > _BLOCK_GAP_RATIO * _height_of(previous)
            else:
                # A new row continues the paragraph only if it starts at the
                # same margin. Two unrelated labels stacked in the region are
                # not a two-line sentence.
                margin = abs(entry[2][0] - current[0][2][0])
                split = margin > _BLOCK_MARGIN_RATIO * height
            if split:
                out.append(current)
                current = []
        current.append(entry)
    if current:
        out.append(current)
    return out


def _continuation(text: str, previous: str) -> Optional[str]:
    """The tail of `text` that follows `previous`, or None if it isn't a
    continuation of it.

    Compared word by word so a typed-out line ("Careful —" -> "Careful — that
    one bites") yields just the words that appeared since. The head comparison
    is fuzzy because OCR re-reads the already-visible part slightly differently
    from one frame to the next."""
    new_words, old_words = text.split(), previous.split()
    if not old_words or len(new_words) <= len(old_words):
        return None
    head = statement_key(" ".join(new_words[:len(old_words)]))
    prev_key = statement_key(previous)
    if head != prev_key:
        if not head or not prev_key:
            return None
        if difflib.SequenceMatcher(None, head, prev_key).ratio() < 0.9:
            return None
    return " ".join(new_words[len(old_words):]).strip()


class StatementReader:
    """Debounces dialogue-region reads into finished statements.

    Feed every poll's text to `update`; it returns the statement to speak, or
    None while the text is still settling, already spoken, or too fragmentary to
    be real. Callers that skip OCR on an unchanged region should feed the
    previous text again — a settled box repeating itself is exactly what tells
    the reader the statement is finished.
    """

    def __init__(self, stable_frames: int = 2, min_chars: int = 6,
                 repeat_window_s: float = 25.0) -> None:
        self.stable_frames = max(1, stable_frames)
        self.min_chars = min_chars
        self.repeat_window_s = repeat_window_s
        self._pending = ""      # last text seen (cleaned)
        self._stable = 0        # consecutive polls showing it
        self._spoken = ""       # last statement handed out for the current box
        self._recent: Deque[Tuple[str, float]] = deque(maxlen=16)

    def reset(self) -> None:
        """Forget everything — used when the region or the feature is switched off."""
        self._pending = ""
        self._stable = 0
        self._spoken = ""
        self._recent.clear()

    def update(self, text: str, now: float) -> Optional[str]:
        text = clean_statement(text)
        if not text:
            # Box empty: the next statement is a new one, even if it reads the
            # same as the last (repeat suppression still covers a quick flicker).
            self._pending = ""
            self._stable = 0
            self._spoken = ""
            return None

        if text != self._pending:
            self._pending, self._stable = text, 1
        else:
            self._stable += 1
        if self._stable != self.stable_frames:
            return None  # still settling, or already reported on the exact hit
        return self._emit(text, now)

    def _emit(self, text: str, now: float) -> Optional[str]:
        # Judge the statement as a whole, never the fragment about to be read
        # out. A continuation inherits the legitimacy of the line it completes:
        # "it bites" is not a button hint when the line it finishes is
        # "Careful with that one — it bites", and testing the tail on its own
        # would drop the end of every sentence that grew by a word or two.
        if not is_speakable(text, self.min_chars):
            # Nothing was said, so don't advance the "already heard" mark — a
            # fragment that later grows into a sentence must be read in full.
            return None
        spoken = text
        if self._spoken:
            tail = _continuation(text, self._spoken)
            if tail is not None:
                spoken = tail
        if not statement_key(spoken):
            return None            # the tail was punctuation only
        key = statement_key(text)
        self._spoken = text
        if self._seen_recently(key, now):
            return None
        self._recent.append((key, now))
        return spoken

    # A re-read of the same box rarely comes back character-identical: one word
    # lands differently ("in front of a house" / "in front of house") and an
    # exact-match check then treats it as a new statement and reads the whole
    # thing again. Anything this similar to something just spoken is the same
    # line, not a new one.
    _REPEAT_RATIO = 0.9

    def _seen_recently(self, key: str, now: float) -> bool:
        for seen, at in self._recent:
            if now - at >= self.repeat_window_s:
                continue
            if seen == key:
                return True
            if difflib.SequenceMatcher(None, seen, key).ratio() >= self._REPEAT_RATIO:
                return True
        return False
