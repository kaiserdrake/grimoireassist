"""Game catalog + bundled monster directories.

A "game" couples a display name, the monster info site URL template, and a bundled
monster list (names + exact site slugs). The list is used both to fix OCR
near-misses and to build the correct page URL.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class GameInfo:
    id: str
    name: str
    site_url_template: str
    monsters: str  # bundled data file id, e.g. "monsters_3"


def _read_data(filename: str) -> str:
    return (resources.files("grimoireassist.data") / filename).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_catalog() -> Tuple[GameInfo, ...]:
    try:
        data = json.loads(_read_data("games.json"))
        return tuple(GameInfo(d["id"], d["name"], d["site_url_template"], d["monsters"])
                     for d in data)
    except Exception:
        return tuple()


def get_game(game_id: str) -> Optional[GameInfo]:
    for g in load_catalog():
        if g.id == game_id:
            return g
    return None


def default_game() -> Optional[GameInfo]:
    cat = load_catalog()
    return cat[0] if cat else None


@lru_cache(maxsize=8)
def _load_monsters(monsters_file: str) -> Tuple[Tuple[str, str], ...]:
    """Return (name, slug) pairs for a bundled monster data file."""
    try:
        data = json.loads(_read_data(f"{monsters_file}.json"))
        return tuple((d["name"], d["slug"]) for d in data)
    except Exception:
        return tuple()


def monster_names(monsters_file: str) -> List[str]:
    return [name for name, _slug in _load_monsters(monsters_file)]


def slug_map(monsters_file: str) -> Dict[str, str]:
    return {name: slug for name, slug in _load_monsters(monsters_file)}


def icon_path() -> str:
    """Filesystem path to the app icon (.ico), or '' if unavailable."""
    try:
        return str(resources.files("grimoireassist.data") / "icon.ico")
    except Exception:
        return ""
