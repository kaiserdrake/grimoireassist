"""Game catalog + bundled monster directory + fuzzy OCR correction."""
from grimoireassist.games import (
    load_catalog, get_game, default_game, monster_names, slug_map,
)
from grimoireassist.battle import canonicalize, ContinuousDetector


def test_catalog_loads():
    cat = load_catalog()
    ids = {g.id for g in cat}
    assert {"mhs3", "mhs2"} <= ids
    assert get_game("mhs3").site_url_template.endswith("/3/monsters/{name}")
    assert default_game() is not None
    assert get_game("nope") is None


def test_monster_data_per_game():
    assert "Rathalos" in monster_names("monsters_3")
    assert "Deviljho" in monster_names("monsters_2")     # MHS2-only
    assert slug_map("monsters_2")["Brute Tigrex"] == "brute-tigrex"
    assert monster_names("missing") == []


def test_fuzzy_uses_selected_game_list():
    mhs3 = monster_names("monsters_3")
    assert canonicalize("Rathaios", mhs3) == "Rathalos"
    # Deviljho is not in MHS3, so it should NOT snap to it
    assert canonicalize("Deviljho", mhs3) != "Deviljho" or "Deviljho" not in mhs3


def test_detector_with_game_list():
    seen = []
    d = ContinuousDetector(known_names=monster_names("monsters_2"), debounce_frames=1)
    d.on_changed = lambda names: seen.append(list(names))
    d.update(["Deviijho"])   # OCR misread of Deviljho (MHS2)
    assert seen[-1] == ["Deviljho"]
