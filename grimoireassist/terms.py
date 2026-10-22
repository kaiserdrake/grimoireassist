"""Copyable terms: names, places and items picked out of OCR'd text.

The point is to save typing. A player reads "head to the Flooded Forest" or sees
"Mega Potion" in an inventory list and wants it in the Grimoire's search box or
a search engine; the transcript and the labels region already hold the text, so
this module finds the bits worth copying.

Detection is a hybrid, because no game ships a list of every place and item:

* **Known lists** — the imported monster names, plus an optional per-game
  ``terms.json`` of locations, items and characters. A hit carries its kind,
  and OCR jitter is snapped to the list's spelling.
* **Proper-noun runs** — game text capitalises its names ("Tower of Dawn",
  "Hinoa"), so a run of Capitalised words mid-sentence is taken as a term even
  when no list knows it. A sentence's opening word is capitalised for grammar,
  not because it is a name, so it only counts as part of a longer run.

Two entry points match the two kinds of text: `find_terms` for prose (dialogue)
and `label_terms` for the short labels of a map or menu screen, where every
label-shaped line is a term on its own.

Pure logic (no Qt, no OCR engine), so it unit-tests directly.
"""
from __future__ import annotations

import difflib
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .dialogue import clean_statement, statement_key

KINDS = ("monster", "location", "item", "character", "term")


@dataclass(frozen=True)
class Term:
    text: str
    kind: str = "term"          # one of KINDS
    source: str = "dialogue"    # "dialogue" | "map/menu" | "screen"


# Capitalised words that are grammar or UI, not names. Matched on the lowercased
# word. Kept to words that genuinely open sentences or label buttons — a word
# like "Dawn" or "Iron" must stay free to be part of a name.
_STOPWORDS = frozenset("""
i i'm i'll i've i'd you you're you'll you've we we're he she they it it's
the a an this that these those there here my your our his her their its me us
yes no oh ah eh hey hmm huh well ok okay please thanks thank sorry
what why how when where who which whatever
and but or so if then now just also still even not
let let's don't can't won't
press hold confirm cancel back select next skip close menu options continue
start exit return
""".split())

# Whole labels that are UI furniture rather than places or items. These are the
# button hints seen on real map screens (see dialogue._PROSE_MIN_WORDS).
_STOP_PHRASES = frozenset({
    "related tips", "find self", "place marker", "undeveloped area",
    "residential district", "map", "inventory", "items", "equipment", "status",
    "quest", "quests", "settings", "save", "load", "new", "all",
})

# Verbs that open quest instructions ("Ask Hinoa", "Head to Kamura Village").
# Capitalised only because they start the sentence, they would otherwise glue
# themselves onto the name that follows. Only dropped at a sentence's start —
# mid-sentence a capitalised "Return" may well be part of a name.
_OPENING_VERBS = frozenset("""
ask find go head talk speak visit take bring meet defeat hunt slay kill beat
return check see look travel follow use get give tell help come open search
collect gather deliver report bring buy sell equip craft explore reach enter
leave escort protect investigate examine wait try make keep stay run
""".split())

# Lowercase words allowed *inside* a run: "Tower of Dawn", "Sea of the Dead".
_CONNECTORS = frozenset({"of", "the", "de", "du", "la", "le", "von", "van",
                         "no", "and", "&"})

_WORD = re.compile(r"[0-9A-Za-zÀ-ɏ][0-9A-Za-zÀ-ɏ'’\-]*|&")
_SENTENCE_BREAK = ".!?…:;\"“”()[]"
# Quantities and counters trailing an inventory label: "Mega Potion x10", "Herb 3".
_QUANTITY = re.compile(r"\s*(?:[x×]\s*\d+|\d+\s*/\s*\d+|\d+)\s*$", re.IGNORECASE)

_SNAP_CUTOFF = 0.85


# ── known lists ────────────────────────────────────────────────────────────────

class Lexicon:
    """Names the game is known to use, each with its kind."""

    def __init__(self, monsters: Iterable[str] = (), locations: Iterable[str] = (),
                 items: Iterable[str] = (), characters: Iterable[str] = ()) -> None:
        self._by_key: Dict[str, Tuple[str, str]] = {}
        # Later lists don't override earlier ones: a monster named like an item
        # is still the monster, which is what the Grimoire has a page for.
        for kind, names in (("monster", monsters), ("location", locations),
                            ("item", items), ("character", characters)):
            for name in names:
                name = (name or "").strip()
                key = statement_key(name)
                if key and key not in self._by_key:
                    self._by_key[key] = (name, kind)
        self._keys = list(self._by_key)
        # One alternation, longest first, so "Iron Sword+" wins over "Iron Sword".
        names = sorted((n for n, _k in self._by_key.values()), key=len, reverse=True)
        self._pattern = (re.compile(
            r"(?<![0-9A-Za-zÀ-ɏ])(?:"
            + "|".join(r"\s+".join(map(re.escape, n.split())) for n in names)
            + r")(?![0-9A-Za-zÀ-ɏ])", re.IGNORECASE) if names else None)

    @classmethod
    def load(cls, game_id: str, config_path) -> "Lexicon":
        from .games import load_terms, monster_names
        if not game_id or not config_path:
            return cls()
        extra = load_terms(game_id, config_path)
        return cls(monsters=monster_names(game_id, config_path),
                   locations=extra.get("locations", ()),
                   items=extra.get("items", ()),
                   characters=extra.get("characters", ()))

    def __len__(self) -> int:
        return len(self._keys)

    def resolve(self, text: str) -> Optional[Tuple[str, str]]:
        """(canonical name, kind) for `text`, exact or fuzzy, or None."""
        key = statement_key(text)
        if not key:
            return None
        hit = self._by_key.get(key)
        if hit:
            return hit
        match = difflib.get_close_matches(key, self._keys, n=1, cutoff=_SNAP_CUTOFF)
        # Same length guard as battle.match_known: a fragment that happens to
        # resemble the start of a long name is not that name.
        if match and len(key) >= 0.75 * len(match[0]):
            return self._by_key[match[0]]
        return None

    def spans(self, text: str) -> List[Tuple[int, int, str, str]]:
        """Every known name in `text`: [(start, end, canonical, kind)]."""
        if self._pattern is None:
            return []
        out = []
        for m in self._pattern.finditer(text):
            hit = self.resolve(m.group(0))
            if hit:
                out.append((m.start(), m.end(), hit[0], hit[1]))
        return out


def _term_for(text: str, lexicon: Optional[Lexicon], source: str) -> Term:
    hit = lexicon.resolve(text) if lexicon is not None else None
    if hit:
        return Term(hit[0], hit[1], source)
    return Term(text, "term", source)


# ── prose ──────────────────────────────────────────────────────────────────────

def _is_cap(word: str) -> bool:
    return word[0].isupper() or (word[0].isdigit() and any(c.isupper() for c in word))


def _starts_sentence(text: str, pos: int) -> bool:
    before = text[:pos].rstrip()
    return not before or before[-1] in _SENTENCE_BREAK


def _runs(text: str) -> List[List[re.Match]]:
    """Runs of capitalised words (with connectors inside) not split by punctuation."""
    runs: List[List[re.Match]] = []
    current: List[re.Match] = []
    for m in _WORD.finditer(text):
        word = m.group(0)
        if current:
            gap = text[current[-1].end():m.start()]
            if gap.strip():                 # punctuation between: a new phrase
                runs.append(current)
                current = []
        if _is_cap(word) or (current and word.lower() in _CONNECTORS):
            current.append(m)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _trim(run: List[re.Match], text: str) -> List[re.Match]:
    """Drop connectors and grammar words from either end of a run."""
    run = list(run)
    while run and (run[-1].group(0).lower() in _CONNECTORS
                   or run[-1].group(0).lower() in _STOPWORDS):
        run.pop()
    while run:
        first = run[0].group(0).lower()
        if first in _CONNECTORS or first in _STOPWORDS:
            run.pop(0)
            continue
        if first in _OPENING_VERBS and _starts_sentence(text, run[0].start()):
            run.pop(0)
            continue
        # A sentence's first word is capitalised for grammar. On its own it is
        # not a name ("Head to the village"); leading a longer run it usually is
        # ("Kamura Village is quiet") — unless the list knows better.
        if len(run) == 1 and _starts_sentence(text, run[0].start()):
            run.pop(0)
            continue
        break
    return run


def find_terms(text: str, lexicon: Optional[Lexicon] = None,
               source: str = "dialogue") -> List[Tuple[int, int, Term]]:
    """Terms in a line of prose: [(start, end, Term)], in order, never overlapping.

    Spans index into `text` as given, so a caller can wrap them in place."""
    if not text:
        return []
    found: List[Tuple[int, int, Term]] = []
    if lexicon is not None:
        for start, end, name, kind in lexicon.spans(text):
            found.append((start, end, Term(name, kind, source)))

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e, _t in found)

    for run in _runs(text):
        run = _trim(run, text)
        if not run:
            continue
        start, end = run[0].start(), run[-1].end()
        phrase = text[start:end]
        key = statement_key(phrase)
        if len(key.replace(" ", "")) < 2 or key in _STOP_PHRASES:
            continue
        if not free(start, end):
            continue
        found.append((start, end, _term_for(phrase, lexicon, source)))
    found.sort(key=lambda f: f[0])
    return found


# ── labels (map / menu screens) ────────────────────────────────────────────────

_LABEL_MAX_WORDS = 5


def label_text(text: str) -> str:
    """The label in a raw OCR line, or "" when the line isn't label-shaped.

    A label is a short capitalised name with no sentence shape: "Iron Sword",
    "Flooded Forest", "Mega Potion x10" (the counter is dropped)."""
    text = _QUANTITY.sub("", clean_statement(text)).strip(" -–—:·")
    if not text:
        return ""
    words = text.split()
    if not 1 <= len(words) <= _LABEL_MAX_WORDS:
        return ""
    if text[-1] in ".!?…,":
        return ""
    key = statement_key(text)
    letters = sum(c.isalpha() for c in key)
    if letters < 3 or letters < 0.6 * len(key.replace(" ", "")):
        return ""
    if key in _STOP_PHRASES or all(w.lower() in _STOPWORDS for w in words):
        return ""
    if not _is_cap(words[0]):
        return ""                       # lowercase scraps are OCR noise, not labels
    return text


def _title_shaped(label: str) -> bool:
    """Every word capitalised (connectors aside), as names are: "Tower of Dawn"
    is, "Talk to the elder" — an unpunctuated hint — is not."""
    return all(_is_cap(w) or w.lower() in _CONNECTORS for w in label.split())


def label_terms(lines: Sequence, lexicon: Optional[Lexicon] = None,
                source: str = "map/menu") -> List[Term]:
    """Terms on a map or menu screen, one per label-shaped line.

    `lines` is the OCR engine's `[(text, confidence, box), ...]` (or plain
    strings). A line that reads as prose instead — a tooltip, an item
    description — is searched like dialogue rather than taken whole."""
    out: List[Term] = []
    seen = set()

    def add(term: Term) -> None:
        key = statement_key(term.text)
        if key and key not in seen:
            seen.add(key)
            out.append(term)

    for line in lines:
        raw = line[0] if isinstance(line, (tuple, list)) else str(line)
        if statement_key(raw) in _STOP_PHRASES:
            continue        # a button hint: not a label, and no name inside it
        label = label_text(raw)
        if label and _title_shaped(label):
            add(_term_for(label, lexicon, source))
            continue
        for _s, _e, term in find_terms(clean_statement(raw), lexicon, source):
            add(term)
    return out


# ── the running list shown as chips ────────────────────────────────────────────

class TermLog:
    """Recently seen terms, newest first, one entry per distinct term.

    Seeing a term again moves it back to the front. A term first guessed from
    capitalisation keeps the better kind if a list later recognises it.

    With a `ttl` (seconds), each term lasts that long after it was last seen:
    `expire()` drops the ones whose time is up, and seeing a term again starts
    its clock over. `clock` is injectable for tests."""

    def __init__(self, maxlen: int = 40, ttl: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.maxlen = max(1, maxlen)
        self.ttl = ttl if ttl and ttl > 0 else None
        self._clock = clock
        self._items: "OrderedDict[str, Term]" = OrderedDict()
        self._seen: Dict[str, float] = {}

    def add(self, term: Term) -> None:
        key = statement_key(term.text)
        if not key:
            return
        old = self._items.pop(key, None)
        if old is not None and term.kind == "term" and old.kind != "term":
            term = Term(term.text, old.kind, term.source)
        self._items[key] = term
        self._items.move_to_end(key, last=False)
        self._seen[key] = self._clock()
        while len(self._items) > self.maxlen:
            dropped, _ = self._items.popitem(last=True)
            self._seen.pop(dropped, None)

    def expire(self) -> bool:
        """Drop terms not seen for `ttl` seconds. True if any went."""
        if self.ttl is None or not self._items:
            return False
        cutoff = self._clock() - self.ttl
        stale = [k for k in self._items if self._seen.get(k, cutoff) <= cutoff]
        for key in stale:
            del self._items[key]
            self._seen.pop(key, None)
        return bool(stale)

    def extend(self, terms: Iterable[Term]) -> None:
        # Added in reverse so the first of a batch ends up first in the log.
        for term in reversed(list(terms)):
            self.add(term)

    def terms(self) -> List[Term]:
        return list(self._items.values())

    def clear(self) -> None:
        self._items.clear()
        self._seen.clear()

    def __len__(self) -> int:
        return len(self._items)
