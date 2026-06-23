"""Tests for the persistent MonsterTracker (retention timeout + Battle-End shrink)."""
from grimoireassist.battle import MonsterTracker, match_known

KNOWN = ["Rathalos", "Tigrex", "Pink Rathian"]


def make(persist=5.0, end_persist=1.0):
    seen = []
    t = MonsterTracker(known_names=KNOWN, persist_seconds=persist, end_persist_seconds=end_persist)
    t.on_changed = lambda names: seen.append(list(names))
    return t, seen


def step(t, names, now, end=False):
    t.observe(names, now)
    t.expire(now, end)
    t.emit_if_changed()


def test_detects_and_matches():
    t, seen = make()
    step(t, ["Rathaios"], 0.0)          # OCR misread -> snaps to Rathalos
    assert seen[-1] == ["Rathalos"]


def test_persists_through_short_gap():
    t, seen = make(persist=5.0)
    step(t, ["Rathalos"], 0.0)
    # name vanishes (attack animation) for 4s -> still shown
    for now in (1.0, 2.0, 3.0, 4.0):
        step(t, [], now)
    assert t.current() == ["Rathalos"]
    assert seen == [["Rathalos"]]       # never flickered off


def test_drops_after_persist():
    t, seen = make(persist=5.0)
    step(t, ["Tigrex"], 0.0)
    step(t, [], 6.0)                     # absent > 5s
    assert t.current() == []
    assert seen[-1] == []


def test_end_shrinks_retention():
    t, seen = make(persist=5.0, end_persist=1.0)
    step(t, ["Rathalos"], 0.0)
    # battle end showing -> retention is 1s, so a 2s gap clears it
    step(t, [], 2.0, end=True)
    assert t.current() == []


def test_touch_keeps_static_monster():
    t, _ = make(persist=5.0)
    t.observe(["Tigrex"], 0.0)
    # region static for a long time: touch() refreshes last_seen, never expires
    for now in range(1, 20):
        t.touch(float(now))
        t.expire(float(now), False)
    assert t.current() == ["Tigrex"]


def test_rejects_non_monster_text():
    assert match_known("Turn 1", KNOWN) is None
    assert match_known("xyzzy", KNOWN) is None
    assert match_known("Tigrex", KNOWN) == "Tigrex"


def test_multiple_monsters_order_preserved():
    t, seen = make()
    step(t, ["Tigrex", "Rathalos"], 0.0)
    assert seen[-1] == ["Tigrex", "Rathalos"]
