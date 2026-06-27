"""Game catalog + monster name lookup + fuzzy OCR correction."""
import json
import pytest
from grimoireassist.games import load_catalog, get_game, default_game, monster_names
from grimoireassist.battle import canonicalize, ContinuousDetector


@pytest.fixture
def games_dir(tmp_path):
    """tmp_path with a minimal games/games.json and a fake config.yaml."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("", encoding="utf-8")
    gd = tmp_path / "games"
    gd.mkdir()
    (gd / "games.json").write_text(json.dumps([
        {"id": "mhs3", "name": "MH Stories 3", "site_url_template": "https://grimoire.laeradsphere.com/game/236/258/notes?fileId=14&st1={name}", "url_style": "search", "requires_login": True, "notes_url": "https://grimoire.laeradsphere.com/game/236/258/notes?fileId=14"},
        {"id": "mhs2", "name": "MH Stories 2", "site_url_template": "https://monsterbuddy.app/2/monsters/{name}", "url_style": "path"},
    ]), encoding="utf-8")
    return cfg


def test_catalog_loads(games_dir):
    cat = load_catalog(games_dir)
    ids = {g.id for g in cat}
    assert {"mhs3", "mhs2"} <= ids
    mhs3 = get_game("mhs3", games_dir)
    assert mhs3.url_style == "search"
    assert "grimoire.laeradsphere.com" in mhs3.site_url_template
    assert "{name}" in mhs3.site_url_template
    assert get_game("mhs2", games_dir).url_style == "path"
    assert default_game(games_dir) is not None
    assert get_game("nope", games_dir) is None


def test_monster_names_empty_without_import(games_dir):
    # No import data → empty list
    assert monster_names("mhs3", games_dir) == []


def test_monster_names_from_import(games_dir, tmp_path):
    # Create fake import data
    imp = tmp_path / "games" / "mhs3" / "import"
    imp.mkdir(parents=True)
    (imp / "data.json").write_text(json.dumps({
        "Rathalos": {"Details": {"_type": "keyvaluepair", "rows": []}},
        "Nargacuga": {},
        "_game_title": "MH Stories 3",
    }), encoding="utf-8")
    names = monster_names("mhs3", games_dir)
    assert "Rathalos" in names
    assert "Nargacuga" in names
    assert "_game_title" not in names


def test_fuzzy_correction():
    known = ["Rathalos", "Nargacuga", "Brachydios"]
    assert canonicalize("Rathaios", known) == "Rathalos"
    assert canonicalize("Brachydos", known) == "Brachydios"


def test_detector_with_list():
    seen = []
    d = ContinuousDetector(known_names=["Rathalos", "Deviljho"], debounce_frames=1)
    d.on_changed = lambda names: seen.append(list(names))
    d.update(["Rathaios"])
    assert seen[-1] == ["Rathalos"]
