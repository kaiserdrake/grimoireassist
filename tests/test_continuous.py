"""Tests for the always-on ContinuousDetector (no battle gating)."""
from grimoireassist.battle import ContinuousDetector


def test_confirms_after_debounce():
    seen = []
    d = ContinuousDetector(debounce_frames=2)
    d.on_changed = lambda names: seen.append(list(names))
    d.update(["Goblin"])          # 1st read
    assert seen == []
    d.update(["Goblin"])          # 2nd identical -> confirm
    assert seen[-1] == ["Goblin"]


def test_clears_when_area_empty():
    seen = []
    d = ContinuousDetector(debounce_frames=2)
    d.on_changed = lambda names: seen.append(list(names))
    d.update(["Slime"]); d.update(["Slime"])
    assert seen[-1] == ["Slime"]
    d.update([]); d.update([])
    assert seen[-1] == []          # cleared


def test_multiple_names_dedup_and_order():
    seen = []
    d = ContinuousDetector(debounce_frames=1)
    d.on_changed = lambda names: seen.append(list(names))
    d.update(["Goblin", "Slime", "goblin"])  # dup (case-insensitive)
    assert seen[-1] == ["Goblin", "Slime"]


def test_fuzzy_correction():
    seen = []
    d = ContinuousDetector(known_names=["Goblin King"], debounce_frames=1)
    d.on_changed = lambda names: seen.append(list(names))
    d.update(["G0blin Klng"])
    assert seen[-1] == ["Goblin King"]


def test_no_event_when_unchanged():
    seen = []
    d = ContinuousDetector(debounce_frames=1)
    d.on_changed = lambda names: seen.append(list(names))
    d.update(["Orc"]); d.update(["Orc"]); d.update(["Orc"])
    assert seen == [["Orc"]]       # only one change event
