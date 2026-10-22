"""Add-game dialog: the URL style is stored by value, and the hint under it
spells out what the template + style will open."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from PyQt6.QtWidgets import QApplication

from grimoireassist.ui.add_game_dialog import AddGameDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app, tmp_path):
    dlg = AddGameDialog(tmp_path / "config.yaml")
    yield dlg
    dlg.close()


def _select(dlg, style):
    dlg._url_style.setCurrentIndex(dlg._url_style.findData(style))


def test_styles_are_stored_as_their_short_values(dialog):
    values = [dialog._url_style.itemData(i) for i in range(dialog._url_style.count())]
    assert values == ["path", "search"]


def test_path_preview_uses_one_slug(dialog):
    _select(dialog, "path")
    dialog._url.setText("https://monsterbuddy.app/2/monsters/{name}")
    assert dialog._url_hint.text().endswith("https://monsterbuddy.app/2/monsters/anjanath")


def test_search_preview_joins_every_name(dialog):
    _select(dialog, "search")
    dialog._url.setText("https://example.com/notes?q={name}")
    assert dialog._url_hint.text().endswith(
        "https://example.com/notes?q=anjanath || great jagras")


def test_placeholder_follows_the_style(dialog):
    _select(dialog, "path")
    path_hint = dialog._url.placeholderText()
    _select(dialog, "search")
    assert dialog._url.placeholderText() != path_hint
    assert "{name}" in dialog._url.placeholderText()


def test_a_template_without_name_is_called_out(dialog):
    dialog._url.setText("https://grimoire.laeradsphere.com/api/note-files/21/raw")
    assert "same page" in dialog._url_hint.text()


def test_stray_braces_are_called_out(dialog):
    dialog._url.setText("https://example.com/{name}/{oops}")
    assert "Only {name}" in dialog._url_hint.text()


def test_edit_mode_fills_fields_and_saves_in_place(app, tmp_path):
    import json
    from grimoireassist.games import GameInfo, load_catalog, save_game
    cfg = tmp_path / "config.yaml"
    save_game(GameInfo("first", "First", "https://a/{name}"), cfg)
    original = GameInfo("mhs2", "MH Stories 2", "https://b/{name}",
                        multi_joiner=" | ", cards_per_row=4)
    save_game(original, cfg)
    save_game(GameInfo("last", "Last", "https://c/{name}"), cfg)
    gf = tmp_path / "games" / "games.json"
    data = json.loads(gf.read_text(encoding="utf-8"))
    data[1]["custom"] = "kept"
    gf.write_text(json.dumps(data), encoding="utf-8")

    dlg = AddGameDialog(cfg, game=original)
    assert dlg.windowTitle() == "Edit game"
    assert dlg._id.text() == "mhs2" and dlg._id.isReadOnly()
    assert dlg._name.text() == "MH Stories 2"
    dlg._name.setText("MH Stories 2: Wings of Ruin")
    dlg._cards_per_row.setValue(3)
    dlg._on_accept()

    assert dlg.result_game.name == "MH Stories 2: Wings of Ruin"
    assert dlg.result_game.multi_joiner == " | "
    assert [g.id for g in load_catalog(cfg)] == ["first", "mhs2", "last"]
    saved = json.loads(gf.read_text(encoding="utf-8"))[1]
    assert saved["cards_per_row"] == 3 and saved["custom"] == "kept"
    dlg.close()
