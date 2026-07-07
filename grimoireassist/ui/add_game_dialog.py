"""Dialog for adding a new game to the catalog."""
from __future__ import annotations

import re

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from ..games import GameInfo, save_game, write_default_settings


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
        self._url.setPlaceholderText("e.g. https://example.com/monsters/{name}")
        form.addRow("Site URL template:", self._url)

        self._url_style = QComboBox()
        self._url_style.addItems(["path", "search"])
        form.addRow("URL style:", self._url_style)

        self._notes_url = QLineEdit()
        self._notes_url.setPlaceholderText("Optional — secondary notes/grimoire URL")
        form.addRow("Notes URL:", self._notes_url)

        self._bookmarks_url = QLineEdit()
        self._bookmarks_url.setPlaceholderText(
            "Optional — raw markdown URL with a '# Bookmarks' section")
        form.addRow("Bookmarks URL:", self._bookmarks_url)

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
            url_style=self._url_style.currentText(),
            notes_url=self._notes_url.text().strip(),
            requires_login=self._login.isChecked(),
            cards_per_row=self._cards_per_row.value(),
            bookmarks_url=self._bookmarks_url.text().strip(),
        )
        save_game(game, self._config_path)
        write_default_settings(game_id, self._config_path)
        self.result_game = game
        self.accept()

    def _show_error(self, msg: str) -> None:
        self._error.setText(msg)
        self._error.setVisible(True)
