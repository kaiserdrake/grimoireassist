"""Overlay model: the UI-facing state (battle status + detected monster names)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class OverlayModel:
    in_battle: bool = False
    status_text: str = "Idle"
    monsters: List[str] = field(default_factory=list)  # confirmed names

    def battle_started(self) -> None:
        self.in_battle = True
        self.status_text = "Battle started"
        self.monsters = []

    def battle_ended(self) -> None:
        self.in_battle = False
        self.status_text = "Idle"
        self.monsters = []

    def set_monsters(self, names: List[str]) -> None:
        self.monsters = list(names)

    def remove_monster(self, name: str) -> None:
        self.monsters = [m for m in self.monsters if m.lower() != name.lower()]
