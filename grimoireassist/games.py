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
    monsters: str            # bundled data file id, e.g. "monsters_3"
    url_style: str = "path"  # "path" = /monsters/<slug>; "search" = ?st1=<terms>, multi-monster
    multi_joiner: str = " || "   # separator for multiple monsters (search style)
    requires_login: bool = False  # monster view uses a persistent (logged-in) profile
    notes_url: str = ""           # secondary "notes" view (🔮 toggle); blank -> grimoire


def _read_data(filename: str) -> str:
    return (resources.files("grimoireassist.data") / filename).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_catalog() -> Tuple[GameInfo, ...]:
    try:
        data = json.loads(_read_data("games.json"))
        return tuple(GameInfo(
            d["id"], d["name"], d["site_url_template"], d["monsters"],
            d.get("url_style", "path"), d.get("multi_joiner", " || "),
            bool(d.get("requires_login", False)), d.get("notes_url", ""),
        ) for d in data)
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


def monster_imported_data(game_id: str, config_path=None) -> dict:
    """Return imported monster data for game_id, or {} if not yet imported.

    Data lives at <config_dir>/imported/<game_id>/data.json and is produced
    by the ImportWizard. Keys are monster names; special keys start with '_'.
    """
    if not config_path:
        return {}
    from pathlib import Path
    data_file = Path(config_path).parent / "games" / game_id / "data.json"
    if not data_file.exists():
        return {}
    try:
        return json.loads(data_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def icon_path() -> str:
    """Filesystem path to the app icon (.ico), or '' if unavailable."""
    try:
        return str(resources.files("grimoireassist.data") / "icon.ico")
    except Exception:
        return ""
