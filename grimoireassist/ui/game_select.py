"""Startup / switch game selection dialog."""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from ..games import GameInfo


class GameSelectDialog(QDialog):
    def __init__(self, catalog: List[GameInfo], current: Optional[str] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select game")
        self.setMinimumWidth(360)
        self.selected: Optional[str] = None
        self.setStyleSheet(
            "QDialog { background:#15151b; }"
            "QLabel { color:#e8e8ec; }"
            "QPushButton { background:#2a2a36; border:none; border-radius:8px;"
            " padding:14px; color:#e8e8ec; font-size:15px; text-align:left; }"
            "QPushButton:hover { background:#39394a; }"
            "QPushButton:default { background:#ffd479; color:#1a1a1a; font-weight:600; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("Which game are you playing?")
        title.setStyleSheet("font-size:17px; font-weight:600;")
        layout.addWidget(title)

        for g in catalog:
            btn = QPushButton(g.name)
            btn.clicked.connect(lambda _checked, gid=g.id: self._choose(gid))
            if g.id == current:
                btn.setDefault(True)
            layout.addWidget(btn)

        if not catalog:
            layout.addWidget(QLabel("No games available (missing data/games.json)."))

    def _choose(self, game_id: str) -> None:
        self.selected = game_id
        self.accept()
