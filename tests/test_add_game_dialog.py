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
