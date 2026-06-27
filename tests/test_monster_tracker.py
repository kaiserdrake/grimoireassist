"""Tests for the persistent MonsterTracker (retention timeout + confidence)."""
from grimoireassist.battle import MonsterTracker, match_known
from grimoireassist.ocr import conf_level

KNOWN = ["Rathalos", "Tigrex", "Pink Rathian"]


def make(persist=5.0, end_persist=1.0):
    seen = []
    t = MonsterTracker(persist_seconds=persist, end_persist_seconds=end_persist)
    t.on_changed = lambda dets: seen.append(list(dets))
    return t, seen


def step(t, dets, now, end=False):
    t.observe(dets, now)
    t.expire(now, end)
    t.emit_if_changed()


def names(dets):
    return [n for n, _ in dets]


def test_detects():
    t, seen = make()
    step(t, [("Rathalos", 0.95)], 0.0)
    assert names(seen[-1]) == ["Rathalos"]


def test_persists_through_short_gap():
    t, seen = make(persist=5.0)
    step(t, [("Rathalos", 0.9)], 0.0)
    for now in (1.0, 2.0, 3.0, 4.0):
        step(t, [], now)
    assert names(t.current()) == ["Rathalos"]
    assert len(seen) == 1                      # never flickered off


def test_drops_after_persist():
    t, seen = make(persist=5.0)
    step(t, [("Tigrex", 0.9)], 0.0)
    step(t, [], 6.0)
    assert t.current() == []


def test_end_shrinks_retention():
    t, _ = make(persist=5.0, end_persist=1.0)
    step(t, [("Rathalos", 0.9)], 0.0)
    step(t, [], 2.0, end=True)                 # end showing -> 1s retention
    assert t.current() == []


def test_touch_keeps_static_monster():
    t, _ = make(persist=5.0)
    t.observe([("Tigrex", 0.9)], 0.0)
    for now in range(1, 20):
        t.touch(float(now))
        t.expire(float(now), False)
    assert names(t.current()) == ["Tigrex"]


def test_confidence_carried_and_levelled():
    t, seen = make()
    step(t, [("Rathalos", 0.5)], 0.0)
    _name, conf = seen[-1][0]
    assert conf_level(conf) == "low"


def test_confidence_is_sticky_at_max():
    t, seen = make(persist=100.0)
    step(t, [("Rathalos", 0.50)], 0.0)         # low
    assert conf_level(seen[-1][0][1]) == "low"
    step(t, [("Rathalos", 0.95)], 0.1)         # rises to high -> re-emit
    assert conf_level(seen[-1][0][1]) == "high"
    step(t, [("Rathalos", 0.40)], 0.2)         # noisy low read -> stays high, no re-emit
    assert conf_level(t.current()[0][1]) == "high"
    assert len(seen) == 2                       # only the upward change emitted


def test_confidence_resets_after_clear():
    t, seen = make(persist=5.0)
    step(t, [("Tigrex", 0.95)], 0.0)           # high
    step(t, [], 6.0)                            # cleared (expired)
    step(t, [("Tigrex", 0.5)], 7.0)            # re-detected low -> starts fresh
    assert conf_level(t.current()[0][1]) == "low"


def test_match_known_filters_non_monsters():
    assert match_known("Rathaios", KNOWN) == "Rathalos"
    assert match_known("Turn 1", KNOWN) is None
    assert match_known("Tigrex", KNOWN) == "Tigrex"


def test_match_known_rejects_ui_text():
    """Pin-menu UI text must not be matched to monsters (real bug report)."""
    names = ["Rey Dau", "Espinas", "Ajarakan", "Rathalos", "Nargacuga", "Arkveld", "Magnamalo", "Lunagaron", "Malzeno", "Velkhana"]
    for ui in ("Set Yellow Pin", "Set Red Pin", "Set Blue Pin", "Set Green Pin",
               "Red", "Set Pin", "Yellow"):
        assert match_known(ui, names) is None, f"{ui!r} wrongly matched"
    # but genuine misreads of real names still resolve
    assert match_known("Rey Oau", names) == "Rey Dau"
    assert match_known("Esplnas", names) == "Espinas"
