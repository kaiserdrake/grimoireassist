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
            self.continuous = cfg.ocr.continuous
            if self.continuous:
                # always-on monster detection; no battle start/end gating
                self.detector = ContinuousDetector(
                    known_names=cfg.monster_name_list,
                    debounce_frames=cfg.ocr.debounce_frames,
                )
                self.detector.on_changed = self.monsters_changed.emit
                self.machine = None
            else:
                self.detector = None
                self.machine = BattleStateMachine(
                    keywords_start=cfg.ocr.keywords_battle_start,
                    keywords_end=cfg.ocr.keywords_battle_end,
                    known_names=cfg.monster_name_list,
                    debounce_frames=cfg.ocr.debounce_frames,
                )
                self.machine.on_battle_started = self.battle_started.emit
                self.machine.on_battle_ended = self.battle_ended.emit
                self.machine.on_monsters_changed = self.monsters_changed.emit
                self.machine.on_monster_killed = self.monster_killed.emit

        def stop(self) -> None:
            self._running = False

        def _crop(self, frame: np.ndarray, region: Region) -> Optional[np.ndarray]:
            if not region.is_set():
                return None
            h, w = frame.shape[:2]
            if region.y + region.h > h or region.x + region.w > w:
                return None
            return frame[region.as_slice()]

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
                    if self.continuous:
                        # read each detected name in the monster area(s) separately
                        names: list = []
                        for region in self.cfg.ocr.regions_monster_names:
                            crop = self._crop(frame, region)
                            if crop is not None:
                                names.extend(self.engine.read_lines(crop))
                        self.debug_text.emit("", names)
                        self.detector.update(names)
                    else:
                        status_crop = self._crop(frame, self.cfg.ocr.regions_battle_status)
                        status_text = self.engine.read_text(status_crop) if status_crop is not None else ""
                        monster_texts = []
                        if self.machine.state is BattleState.IN_BATTLE:
                            for region in self.cfg.ocr.regions_monster_names:
                                crop = self._crop(frame, region)
                                monster_texts.append(self.engine.read_text(crop) if crop is not None else "")
                        self.debug_text.emit(status_text, monster_texts)
                        self.machine.update(status_text, monster_texts)
                except Exception as exc:
                    self.error.emit(f"OCR error: {exc}")
                dt = time.time() - t0
                if dt < interval:
                    time.sleep(interval - dt)

except ImportError:  # PyQt not available (e.g. headless unit tests)
    OcrWorker = None  # type: ignore
