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


def is_speakable(text: str, min_chars: int = 6) -> bool:
    """Whether a statement looks like prose rather than an OCR fragment."""
    key = statement_key(text)
    if len(key) < max(1, min_chars):
        return False
    letters = sum(c.isalpha() for c in key)
    return letters >= 0.5 * len(key.replace(" ", "")) and letters >= 2


def assemble_statement(lines: List[tuple]) -> str:
    """Join `[(text, confidence, box), ...]` into one statement.

    Lines are ordered the way they are read — top to bottom, then left to right —
    using the boxes when the engine supplies them (EasyOCR returns detections in
    detector order, which is not always reading order) and the given order when
    it doesn't. A line ending in a hyphen is treated as a word split across the
    line break and rejoined without a space.
    """
    entries: List[Tuple[int, tuple]] = list(enumerate(lines))
    if all(len(e) > 2 and e[2] is not None for _i, e in entries):
        entries.sort(key=lambda item: (item[1][2][1], item[1][2][0]))
    out = ""
    for _i, entry in entries:
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
        spoken = text
        if self._spoken:
            tail = _continuation(text, self._spoken)
            if tail is not None:
                spoken = tail

        if not is_speakable(spoken, self.min_chars):
            # Nothing was said, so don't advance the "already heard" mark — a
            # fragment that later grows into a sentence must be read in full.
            return None
        key = statement_key(text)
        self._spoken = text
        if self._seen_recently(key, now):
            return None
        self._recent.append((key, now))
        return spoken

    def _seen_recently(self, key: str, now: float) -> bool:
        return any(k == key and now - t < self.repeat_window_s
                   for k, t in self._recent)
