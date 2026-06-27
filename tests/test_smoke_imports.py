"""Smoke test: every module imports and the config round-trips. No camera/GUI shown."""
import numpy as np

from grimoireassist.config import Config, GameSettings, Region
from grimoireassist.capture import FrameBuffer
from grimoireassist.overlay import OverlayModel


def test_config_roundtrip(tmp_path):
    p = tmp_path / "config.yaml"
    cfg = Config()
    cfg.selected_game = "mhs3"
    cfg.set_regions_for("mhs3", GameSettings(
        monster_names=[Region(10, 20, 100, 40)],
        battle_end=Region(5, 5, 50, 20),
        end_keywords=["result"],
    ))
    cfg.save(p)
    again = Config.load(p)
    assert again.selected_game == "mhs3"
    gs = again.regions_for("mhs3")
    assert gs.monster_names[0].w == 100
    assert gs.battle_end.w == 50
    assert gs.end_keywords == ["result"]
    assert again.ocr.continuous is True


def test_config_migrates_single_game(tmp_path):
    """An old-style config (ocr.regions + site) is carried into the games map."""
    import json
    # Provide a catalog so the migration can resolve the game id
    gd = tmp_path / "games"
    gd.mkdir()
    (gd / "games.json").write_text(json.dumps([
        {"id": "mhs3", "name": "MH Stories 3",
         "site_url_template": "https://example.com/3/monsters/{name}"},
    ]), encoding="utf-8")
    p = tmp_path / "config.yaml"
    p.write_text(
        "ocr:\n  regions:\n    monster_names:\n    - {x: 5, y: 6, w: 70, h: 8}\n"
        "site:\n  url_template: https://example.com/3/monsters/{name}\n",
        encoding="utf-8",
    )
    cfg = Config.load(p)
    assert cfg.selected_game == "mhs3"
    assert cfg.regions_for("mhs3").monster_names[0].w == 70


def test_frame_buffer_roundtrip():
    buf = FrameBuffer()
    assert buf.get()[0] is None
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    buf.set(frame)
    got, seq = buf.get()
    assert got is not None and seq == 1


def test_overlay_clear_on_battle_end():
    m = OverlayModel()
    m.battle_started()
    m.set_monsters(["Goblin"])
    assert m.monsters == ["Goblin"] and m.in_battle
    m.remove_monster("Goblin")
    assert m.monsters == []
    m.set_monsters(["Slime"])
    m.battle_ended()
    assert m.monsters == [] and not m.in_battle


def test_region_slice():
    r = Region(2, 3, 5, 4)
    arr = np.arange(100).reshape(10, 10)
    assert arr[r.as_slice()].shape == (4, 5)
