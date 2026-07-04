"""Tests for the global-hotkey text parser and the new ui config fields."""
from grimoireassist.config import Config
from grimoireassist.hotkey import parse_hotkey


def test_parse_letter_with_modifiers():
    # MOD_ALT=1, MOD_CTRL=2 → 3; 'S' = 0x53
    assert parse_hotkey("ctrl+alt+s") == (3, ord("S"))


def test_parse_function_key():
    assert parse_hotkey("f8") == (0, 0x77)          # VK_F8
    assert parse_hotkey("ctrl+shift+f12") == (6, 0x7B)


def test_parse_named_key_and_win_modifier():
    assert parse_hotkey("win+printscreen") == (8, 0x2C)


def test_parse_is_case_and_space_insensitive():
    assert parse_hotkey("Ctrl + Alt + S") == parse_hotkey("ctrl+alt+s")


def test_parse_rejects_invalid():
    assert parse_hotkey("bogus+key") is None
    assert parse_hotkey("ctrl+") is None      # modifier only, no key
    assert parse_hotkey("") is None


def test_ui_config_roundtrip(tmp_path):
    cfg = Config()
    cfg.ui.auto_start_tracking = True
    cfg.ui.snapshot_hotkey = "ctrl+alt+f12"
    p = tmp_path / "config.yaml"
    cfg.save(p)
    loaded = Config.load(p)
    assert loaded.ui.auto_start_tracking is True
    assert loaded.ui.snapshot_hotkey == "ctrl+alt+f12"


def test_ui_config_defaults():
    cfg = Config.from_dict({"ui": {"always_on_top": True}})
    assert cfg.ui.auto_start_tracking is False
    assert cfg.ui.snapshot_hotkey == "ctrl+alt+s"
