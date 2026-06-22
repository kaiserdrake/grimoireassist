"""Unit tests for the pure battle state machine (no Qt/camera needed)."""
from grimoireassist.battle import BattleStateMachine, BattleState


def make_machine(**kw):
    return BattleStateMachine(
        keywords_start=["battle start", "encounter"],
        keywords_end=["victory", "defeat"],
        known_names=kw.get("known_names", []),
        debounce_frames=kw.get("debounce_frames", 2),
    )


def test_starts_on_keyword():
    events = []
    m = make_machine()
    m.on_battle_started = lambda: events.append("start")
    m.update("nothing here", [])
    assert m.state is BattleState.IDLE
    m.update("BATTLE START!", [])
    assert m.state is BattleState.IN_BATTLE
    assert events == ["start"]


def test_monsters_confirmed_after_debounce():
    seen = []
    m = make_machine(debounce_frames=2)
    m.on_monsters_changed = lambda names: seen.append(list(names))
    m.update("encounter", [])
    # need 2 identical reads before confirming
    m.update("", ["Goblin"])
    assert seen == []
    m.update("", ["Goblin"])
    assert seen[-1] == ["Goblin"]


def test_monster_killed_when_slot_blanks():
    killed = []
    m = make_machine(debounce_frames=2)
    m.on_monster_killed = lambda n: killed.append(n)
    m.update("encounter", [])
    m.update("", ["Slime"])
    m.update("", ["Slime"])
    assert m.confirmed_monsters() == ["Slime"]
    m.update("", [""])
    m.update("", [""])
    assert killed == ["Slime"]
    assert m.confirmed_monsters() == []


def test_battle_end_resets():
    ended = []
    m = make_machine()
    m.on_battle_ended = lambda: ended.append(True)
    m.update("encounter", [])
    m.update("", ["Dragon"])
    m.update("VICTORY", [])
    assert m.state is BattleState.IDLE
    assert ended == [True]
    assert m.confirmed_monsters() == []


def test_fuzzy_name_correction():
    m = make_machine(known_names=["Goblin", "Slime"], debounce_frames=2)
    m.update("encounter", [])
    m.update("", ["G0blln"])   # noisy OCR
    m.update("", ["G0blln"])
    assert m.confirmed_monsters() == ["Goblin"]
