"""Battle state machine + OCR worker thread.

`BattleStateMachine` is pure logic (no Qt) and easily unit-testable.
`OcrWorker` is a QThread that polls the frame buffer, runs OCR on the configured
regions, feeds the machine, and emits Qt signals for the UI.
"""
from __future__ import annotations

import difflib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional

import cv2
import numpy as np

from .config import Config, Region
from .ocr import OcrEngine


class BattleState(Enum):
    IDLE = "idle"
    IN_BATTLE = "in_battle"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_any(haystack: str, needles: List[str]) -> bool:
    h = _norm(haystack)
    return any(n and n in h for n in needles)


def canonicalize(text: str, known_names: List[str], cutoff: float = 0.6) -> str:
    """Snap a noisy OCR read to a known monster name when close enough."""
    t = _norm(text)
    if not t or not known_names:
        return text.strip()
    match = difflib.get_close_matches(t, [_norm(n) for n in known_names],
                                      n=1, cutoff=cutoff)
    if match:
        for n in known_names:
            if _norm(n) == match[0]:
                return n
    return text.strip()


def match_known(text: str, known_names: List[str], cutoff: float = 0.6) -> Optional[str]:
    """Return the known monster name matching `text`, or None if it isn't close to any.

    Unlike `canonicalize`, this REJECTS text that doesn't resemble a real monster, so OCR
    noise during animations never becomes a fake monster. With no known list, accepts the
    raw text (best effort)."""
    t = _norm(text)
    if not t:
        return None
    if not known_names:
        return text.strip() or None
    match = difflib.get_close_matches(t, [_norm(n) for n in known_names], n=1, cutoff=cutoff)
    if match:
        for n in known_names:
            if _norm(n) == match[0]:
                return n
    return None


class MonsterTracker:
    """Persistent monster detection with a retention timeout.

    Each detected monster stays visible for `persist_seconds` after it was last seen, which
    bridges the gaps where attack animations hide the name. While the caller reports the
    Battle-End trigger (`end_detected`), retention drops to `end_persist_seconds` so the list
    clears promptly once the fight is over.
    """

    def __init__(self, known_names: Optional[List[str]] = None,
                 persist_seconds: float = 5.0, end_persist_seconds: float = 1.0) -> None:
        self.known_names = known_names or []
        self.persist_seconds = persist_seconds
        self.end_persist_seconds = end_persist_seconds
        self._seen: Dict[str, float] = {}     # name -> last_seen_time (insertion-ordered)
        self._last_found: List[str] = []      # matched names from the most recent OCR
        self._emitted: List[str] = []
        self.on_changed: Optional[Callable[[List[str]], None]] = None

    def observe(self, names: List[str], now: float) -> None:
        """A fresh OCR read: record matched monsters as seen 'now'."""
        found, seen_keys = [], set()
        for raw in names:
            n = match_known(raw, self.known_names)
            if n:
                key = _norm(n)
                if key not in seen_keys:
                    seen_keys.add(key)
                    found.append(n)
        self._last_found = found
        for n in found:
            self._seen[n] = now

    def touch(self, now: float) -> None:
        """Region unchanged since last OCR — the last-found monsters are still on screen."""
        for n in self._last_found:
            if n in self._seen:
                self._seen[n] = now

    def expire(self, now: float, end_detected: bool) -> None:
        persist = self.end_persist_seconds if end_detected else self.persist_seconds
        for n in [k for k, t in list(self._seen.items()) if now - t > persist]:
            del self._seen[n]

    def current(self) -> List[str]:
        return list(self._seen.keys())

    def emit_if_changed(self) -> None:
        cur = self.current()
        if [_norm(n) for n in cur] != [_norm(n) for n in self._emitted]:
            self._emitted = list(cur)
            if self.on_changed:
                self.on_changed(list(cur))


class ContinuousDetector:
    """Always-on monster detector (no battle start/end gating).

    Fed the list of names read from the OCR area each poll. Debounces the whole
    set so a momentary misread doesn't flicker the display, and reports the
    confirmed set whenever it changes (empty set = nothing in the area).
    """

    def __init__(self, known_names: Optional[List[str]] = None,
                 debounce_frames: int = 3) -> None:
        self.known_names = known_names or []
        self.debounce_frames = max(1, debounce_frames)
        self._history: Deque[tuple] = deque(maxlen=max(8, self.debounce_frames))
        self.confirmed: List[str] = []
        self.on_changed: Optional[Callable[[List[str]], None]] = None

    def update(self, names: List[str]) -> None:
        # canonicalize, drop blanks, dedupe (case-insensitive), keep order
        seen, cleaned = set(), []
        for raw in names:
            name = canonicalize(raw, self.known_names)
            key = _norm(name)
            if key and key not in seen:
                seen.add(key)
                cleaned.append(name)
        self._history.append(tuple(_norm(n) for n in cleaned))

        recent = list(self._history)[-self.debounce_frames:]
        if len(recent) < self.debounce_frames or len(set(recent)) != 1:
            return  # not stable yet
        if tuple(_norm(n) for n in self.confirmed) != recent[0]:
            self.confirmed = cleaned
            if self.on_changed:
                self.on_changed(list(cleaned))


@dataclass
class _Slot:
    """Debounce buffer for one monster-name region."""
    history: Deque[str] = field(default_factory=lambda: deque(maxlen=8))
    confirmed: Optional[str] = None


class BattleStateMachine:
    """Drives transitions from successive OCR reads.

    Callers invoke `update(status_text, monster_texts)` once per poll. The
    machine fires the registered callbacks on transitions.
    """

    def __init__(
        self,
        keywords_start: List[str],
        keywords_end: List[str],
        known_names: Optional[List[str]] = None,
        debounce_frames: int = 3,
    ) -> None:
        self.keywords_start = [_norm(k) for k in keywords_start]
        self.keywords_end = [_norm(k) for k in keywords_end]
        self.known_names = known_names or []
        self.debounce_frames = max(1, debounce_frames)
        self.state = BattleState.IDLE
        self._slots: List[_Slot] = []

        # callbacks (set by the worker / UI)
        self.on_battle_started: Optional[Callable[[], None]] = None
        self.on_battle_ended: Optional[Callable[[], None]] = None
        self.on_monsters_changed: Optional[Callable[[List[str]], None]] = None
        self.on_monster_killed: Optional[Callable[[str], None]] = None

    # -- helpers ---------------------------------------------------------
    def _canonical(self, text: str) -> str:
        return canonicalize(text, self.known_names)

    def _ensure_slots(self, n: int) -> None:
        while len(self._slots) < n:
            self._slots.append(_Slot())
        if len(self._slots) > n:
            del self._slots[n:]

    def confirmed_monsters(self) -> List[str]:
        seen, out = set(), []
        for s in self._slots:
            if s.confirmed:
                key = _norm(s.confirmed)
                if key not in seen:
                    seen.add(key)
                    out.append(s.confirmed)
        return out

    # -- main entry ------------------------------------------------------
    def update(self, status_text: str, monster_texts: List[str]) -> None:
        if self.state is BattleState.IDLE:
            if _contains_any(status_text, self.keywords_start):
                self._start()
            else:
                return  # ignore monster reads outside battle

        # IN_BATTLE
        if _contains_any(status_text, self.keywords_end):
            self._end()
            return

        self._update_monsters(monster_texts)

    def _start(self) -> None:
        self.state = BattleState.IN_BATTLE
        self._slots = []
        if self.on_battle_started:
            self.on_battle_started()

    def _end(self) -> None:
        self.state = BattleState.IDLE
        self._slots = []
        if self.on_battle_ended:
            self.on_battle_ended()

    def _update_monsters(self, monster_texts: List[str]) -> None:
        self._ensure_slots(len(monster_texts))
        changed = False
        for slot, raw in zip(self._slots, monster_texts):
            name = self._canonical(raw)
            slot.history.append(_norm(name))
            # confirm when the same non-empty read dominates the recent window
            recent = list(slot.history)[-self.debounce_frames:]
            if len(recent) >= self.debounce_frames and len(set(recent)) == 1:
                value = recent[0]
                prev = slot.confirmed
                if value and value != _norm(prev or ""):
                    slot.confirmed = name
                    changed = True
                elif not value and prev:
                    # slot went blank -> monster killed/left
                    slot.confirmed = None
                    changed = True
                    if self.on_monster_killed:
                        self.on_monster_killed(prev)
        if changed and self.on_monsters_changed:
            self.on_monsters_changed(self.confirmed_monsters())


# --------------------------------------------------------------------------
# Qt worker
# --------------------------------------------------------------------------
try:
    from PyQt6.QtCore import QThread, pyqtSignal

    class OcrWorker(QThread):
        battle_started = pyqtSignal()
        battle_ended = pyqtSignal()
        monsters_changed = pyqtSignal(list)
        monster_killed = pyqtSignal(str)
        debug_text = pyqtSignal(str, list)  # status_text, monster_texts
        error = pyqtSignal(str)

        def __init__(self, cfg: Config, frame_buffer, engine: OcrEngine, parent=None):
            super().__init__(parent)
            self.cfg = cfg
            self.buffer = frame_buffer
            self.engine = engine
            self._running = True
            self._last_sig = None   # last OCR'd monster fingerprint (change detection)
            self._last_ocr_t = 0.0  # time of last monster OCR (heartbeat)
            self._end_sig = None    # last Battle-End region fingerprint
            self._end_ocr_t = 0.0
            self._end_cached = False
            self.tracker = MonsterTracker(
                known_names=cfg.monster_name_list,
                persist_seconds=cfg.ocr.monster_persist_s,
                end_persist_seconds=cfg.ocr.monster_persist_end_s,
            )
            self.tracker.on_changed = self.monsters_changed.emit

        # Skip OCR while the monster region is visually unchanged; only a real
        # change (text appearing/changing) triggers an inference, so idle screens
        # cost ~0 GPU. A heartbeat still re-OCRs a static scene periodically, so a
        # single bad read caught mid-transition can't stick while frozen.
        _CHANGE_THRESHOLD = 2.0
        _HEARTBEAT_S = 1.0

        def stop(self) -> None:
            self._running = False

        def _crop(self, frame: np.ndarray, region: Region) -> Optional[np.ndarray]:
            if not region.is_set():
                return None
            h, w = frame.shape[:2]
            if region.y + region.h > h or region.x + region.w > w:
                return None
            return frame[region.as_slice()]

        def _region_signature(self, frame: np.ndarray) -> Optional[np.ndarray]:
            """Tiny grayscale fingerprint of the monster region(s) for change detection."""
            parts = []
            for region in self.cfg.ocr.regions_monster_names:
                crop = self._crop(frame, region)
                if crop is None:
                    continue
                g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
                parts.append(cv2.resize(g, (64, 16), interpolation=cv2.INTER_AREA).ravel())
            if not parts:
                return None
            return np.concatenate(parts).astype(np.int16)

        def _region_changed(self, sig: Optional[np.ndarray]) -> bool:
            if sig is None:
                return False
            last = self._last_sig
            if last is not None and last.shape == sig.shape:
                if float(np.abs(sig - last).mean()) < self._CHANGE_THRESHOLD:
                    return False
            return True

        def _detect_end(self, frame: np.ndarray, now: float) -> bool:
            """Whether the Battle-End region currently shows an end keyword.

            Change-detected + cached so a static end banner costs ~0 GPU."""
            region = self.cfg.ocr.regions_battle_end
            if not region.is_set():
                return False
            crop = self._crop(frame, region)
            if crop is None:
                return False
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
            sig = cv2.resize(g, (48, 16), interpolation=cv2.INTER_AREA).astype(np.int16)
            changed = (self._end_sig is None or self._end_sig.shape != sig.shape
                       or float(np.abs(sig - self._end_sig).mean()) >= self._CHANGE_THRESHOLD)
            if changed or (now - self._end_ocr_t) >= self._HEARTBEAT_S:
                text = self.engine.read_text(crop)
                needles = [_norm(k) for k in self.cfg.ocr.keywords_battle_end]
                self._end_cached = _contains_any(text, needles)
                self._end_sig = sig
                self._end_ocr_t = now
            return self._end_cached

        def run(self) -> None:
            interval = 1.0 / max(0.5, self.cfg.ocr.poll_fps)
            last_seq = -1
            while self._running:
                t0 = time.time()
                frame, seq = self.buffer.get()
                if frame is None or seq == last_seq:
                    time.sleep(interval)
                    continue
                last_seq = seq
                try:
                    # Monster region: OCR only when it changed or the heartbeat is due;
                    # otherwise the same monsters are still on screen (touch keeps them).
                    sig = self._region_signature(frame)
                    changed = self._region_changed(sig)
                    due = (t0 - self._last_ocr_t) >= self._HEARTBEAT_S
                    if changed or due:
                        names: list = []
                        for region in self.cfg.ocr.regions_monster_names:
                            crop = self._crop(frame, region)
                            if crop is not None:
                                names.extend(self.engine.read_lines(crop))
                        self._last_sig = sig
                        self._last_ocr_t = t0
                        self.tracker.observe(names, t0)
                        self.debug_text.emit("", names)
                    else:
                        self.tracker.touch(t0)
                    # Retention shrinks while the Battle-End banner is showing.
                    end_detected = self._detect_end(frame, t0)
                    self.tracker.expire(t0, end_detected)
                    self.tracker.emit_if_changed()
                except Exception as exc:
                    self.error.emit(f"OCR error: {exc}")
                dt = time.time() - t0
                if dt < interval:
                    time.sleep(interval - dt)

except ImportError:  # PyQt not available (e.g. headless unit tests)
    OcrWorker = None  # type: ignore
