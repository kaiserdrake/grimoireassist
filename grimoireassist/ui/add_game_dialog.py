"""Dialog for adding a new game to the catalog."""
from __future__ import annotations

import re
from urllib.parse import unquote

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from ..games import GameInfo, save_game, write_default_settings
from .monster_panel import build_monster_url

# (stored value, what the dropdown shows)
_URL_STYLES = (
    ("path", "Page per monster (path)"),
    ("search", "Search (search)"),
)
_URL_PLACEHOLDERS = {
    "path": "e.g. https://monsterbuddy.app/2/monsters/{name}",
    "search": "e.g. https://example.com/notes?search={name}",
}
_URL_STYLE_HINTS = {
    "path": "{name} becomes one monster's slug: Great Jagras → great-jagras.",
    "search": "{name} becomes every monster on screen: anjanath || great jagras.",
}
_SAMPLE_NAMES = ["Great Jagras", "Anjanath"]


class AddGameDialog(QDialog):
    def __init__(self, config_path, parent=None) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self.result_game: GameInfo | None = None

        self.setWindowTitle("Add game")
        self.setMinimumWidth(480)
        self.setStyleSheet(
            "QDialog { background:#15151b; }"
            "QLabel { color:#e8e8ec; }"
            "QLineEdit, QComboBox { background:#2a2a36; color:#e8e8ec;"
            "  border:1px solid #3a3a50; border-radius:4px; padding:4px 8px; }"
            "QCheckBox { color:#e8e8ec; }"
            "QPushButton { background:#2a2a36; color:#e8e8ec; border:none;"
            "  border-radius:6px; padding:6px 16px; }"
            "QPushButton:hover { background:#3a3a50; }"
            "QPushButton[text='Add'] { background:#5b3fa6; color:#fff; font-weight:600; }"
        )

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(16, 16, 16, 16)

        form = QFormLayout()
        form.setSpacing(8)

        self._id = QLineEdit()
        self._id.setPlaceholderText("e.g. mhs3  (used as folder name, no spaces)")
        form.addRow("Game ID:", self._id)

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. MH Stories 3: Twisted Reflection")
        form.addRow("Display name:", self._name)

        self._url = QLineEdit()
        self._url.setToolTip(
            "The page opened for the monsters on screen. Put {name} where the\n"
            "monster goes; the URL style below decides what replaces it.\n"
            "With no {name}, the same page opens for every monster.\n\n"
            "Examples:\n"
            "  https://monsterbuddy.app/2/monsters/{name}\n"
            "  https://grimoire.laeradsphere.com/game/236/258/notes?fileId=14&st1={name}")
        self._url.textChanged.connect(self._update_url_hint)
        form.addRow("Site URL template:", self._url)

        self._url_style = QComboBox()
        for style, label in _URL_STYLES:
            self._url_style.addItem(label, style)
        self._url_style.setToolTip(
            "How {name} is filled in:\n\n"
            "Page per monster (path) — one monster, as a URL slug:\n"
            "  \"Great Jagras\" → great-jagras. If several are on screen, the\n"
            "  first alphabetically is used. For wikis with a page per monster.\n\n"
            "Search (search) — every monster on screen, lowercase, joined with\n"
            "  \" || \": \"anjanath || rathalos\". For a notes page that filters\n"
            "  by a search term, like the Grimoire's st1= parameter.")
        self._url_style.currentIndexChanged.connect(self._update_url_hint)
        form.addRow("URL style:", self._url_style)

        # What the two fields above produce, worked out live from sample names.
        self._url_hint = QLabel()
        self._url_hint.setStyleSheet("color:#8a8a99; font-size:11px;")
        self._url_hint.setWordWrap(True)
        form.addRow("", self._url_hint)

        self._notes_url = QLineEdit()
        self._notes_url.setPlaceholderText("Optional — secondary notes/grimoire URL")
        form.addRow("Notes URL:", self._notes_url)

        self._login = QCheckBox("Requires login (use persistent browser session)")
        form.addRow("", self._login)

        self._cards_per_row = QSpinBox()
        self._cards_per_row.setMinimum(1)
        self._cards_per_row.setMaximum(8)
        self._cards_per_row.setValue(4)
        self._cards_per_row.setStyleSheet(
            "QSpinBox { background:#2a2a36; color:#e8e8ec;"
            "  border:1px solid #3a3a50; border-radius:4px; padding:4px 8px; }")
        form.addRow("Cards per row:", self._cards_per_row)

        lay.addLayout(form)

        self._error = QLabel()
        self._error.setStyleSheet("color:#ff6b6b; font-size:11px;")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        lay.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        add_btn = buttons.addButton("Add", QDialogButtonBox.ButtonRole.AcceptRole)
        add_btn.setObjectName("add")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._update_url_hint()

    def _update_url_hint(self) -> None:
        """Placeholder and preview line follow the chosen style, so the two
        fields explain each other."""
        style = self._url_style.currentData()
        self._url.setPlaceholderText(_URL_PLACEHOLDERS[style])
        template = self._url.text().strip()
        if not template:
            self._url_hint.setText(_URL_STYLE_HINTS[style])
            return
        if "{name}" not in template:
            self._url_hint.setText(
                "No {name} in the template: the same page opens for every monster.")
            return
        try:
            url = build_monster_url(template, _SAMPLE_NAMES, url_style=style)
        except (IndexError, KeyError, ValueError):
            self._url_hint.setText(
                "Only {name} may appear in braces — other {…} breaks the template.")
            return
        # Shown decoded: the real URL is percent-encoded, which hides the point.
        self._url_hint.setText(
            f"With {' + '.join(_SAMPLE_NAMES)} on screen this opens:\n{unquote(url)}")

    def _on_accept(self) -> None:
        game_id = self._id.text().strip().lower()
        name = self._name.text().strip()
        url = self._url.text().strip()

        if not game_id:
            self._show_error("Game ID is required.")
            return
        if not re.fullmatch(r"[a-z0-9_-]+", game_id):
            self._show_error("Game ID may only contain letters, digits, hyphens and underscores.")
            return
        if not name:
            self._show_error("Display name is required.")
            return
        if not url:
            self._show_error("Site URL template is required.")
            return

        game = GameInfo(
            id=game_id,
            name=name,
            site_url_template=url,
            url_style=self._url_style.currentData(),
            notes_url=self._notes_url.text().strip(),
            requires_login=self._login.isChecked(),
            cards_per_row=self._cards_per_row.value(),
        )
        save_game(game, self._config_path)
        write_default_settings(game_id, self._config_path)
        self.result_game = game
        self.accept()

    def _show_error(self, msg: str) -> None:
        self._error.setText(msg)
        self._error.setVisible(True)
