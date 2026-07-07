"""Game catalog + per-game import helpers.

The catalog lives at  <config_dir>/games/games.json  (user-editable).
Per-game imported data lives at  <config_dir>/games/<id>/import/data.json.
Per-game default settings are written to  <config_dir>/games/<id>/settings.json
when a game is first added via the Add Game dialog.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from importlib import resources


@dataclass(frozen=True)
class GameInfo:
    id: str
    name: str
    site_url_template: str
    url_style: str = "path"
    multi_joiner: str = " || "
    requires_login: bool = False
    notes_url: str = ""
    cards_per_row: int = 4
    bookmarks_url: str = ""   # raw-markdown note with a "# Bookmarks" section


# ── Catalog (games/games.json) ─────────────────────────────────────────────────

def _games_file(config_path) -> Optional[Path]:
    if not config_path:
        return None
    return Path(config_path).parent / "games" / "games.json"


def load_catalog(config_path=None) -> Tuple[GameInfo, ...]:
    gf = _games_file(config_path)
    if not gf or not gf.exists():
        return tuple()
    try:
        data = json.loads(gf.read_text(encoding="utf-8"))
        return tuple(GameInfo(
            id=d["id"],
            name=d["name"],
            site_url_template=d.get("site_url_template", ""),
            url_style=d.get("url_style", "path"),
            multi_joiner=d.get("multi_joiner", " || "),
            requires_login=bool(d.get("requires_login", False)),
            notes_url=d.get("notes_url", ""),
            cards_per_row=int(d.get("cards_per_row", 4)),
            bookmarks_url=d.get("bookmarks_url", ""),
        ) for d in data)
    except Exception:
        return tuple()


def get_game(game_id: str, config_path=None) -> Optional[GameInfo]:
    for g in load_catalog(config_path):
        if g.id == game_id:
            return g
    return None


def default_game(config_path=None) -> Optional[GameInfo]:
    cat = load_catalog(config_path)
    return cat[0] if cat else None


def save_game(game: GameInfo, config_path) -> None:
    """Append or update a game entry in games/games.json."""
    gf = _games_file(config_path)
    if not gf:
        return
    gf.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if gf.exists():
        try:
            existing = json.loads(gf.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    entry = {
        "id": game.id,
        "name": game.name,
        "site_url_template": game.site_url_template,
        "url_style": game.url_style,
        "multi_joiner": game.multi_joiner,
        "requires_login": game.requires_login,
        "notes_url": game.notes_url,
        "cards_per_row": game.cards_per_row,
        "bookmarks_url": game.bookmarks_url,
    }
    # Replace existing entry with same id, or append
    updated = [e for e in existing if e.get("id") != game.id]
    updated.append(entry)
    gf.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Monster names (import data only) ──────────────────────────────────────────

def monster_names(game_id: str, config_path=None) -> List[str]:
    """Return monster names from imported data. Empty list if not yet imported."""
    imported = monster_imported_data(game_id, config_path)
    return [k for k in imported if not k.startswith("_")]


def slug_map(game_id: str = "") -> Dict[str, str]:
    """Slug map — currently not populated (URL slugs are derived at runtime)."""
    return {}


# ── Import data ────────────────────────────────────────────────────────────────

def import_dir(game_id: str, config_path) -> Path:
    """Return the import directory for a game (data.json + images/ live here)."""
    return Path(config_path).parent / "games" / game_id / "import"


def monster_imported_data(game_id: str, config_path=None) -> dict:
    """Return imported monster data for game_id, or {} if not yet imported.

    Automatically migrates old per-monster image layout to shared images/icons/.
    """
    if not config_path:
        return {}
    imp = import_dir(game_id, config_path)
    data_file = imp / "data.json"
    if not data_file.exists():
        return {}
    try:
        data = json.loads(data_file.read_text(encoding="utf-8"))
        _migrate_icons(data, imp, data_file)
        return data
    except Exception:
        return {}


# ── Game settings file ─────────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "regions": {
        "monster_names": [{"x": 0, "y": 0, "w": 0, "h": 0}],
        "battle_end": {"x": 0, "y": 0, "w": 0, "h": 0},
    },
    "keywords": {
        "battle_end": ["result", "victory", "defeat"],
    },
    "monster_persist_s": 12.0,
    "monster_persist_end_s": 1.0,
}


def write_default_settings(game_id: str, config_path) -> None:
    """Write a default settings.json for a newly added game."""
    path = Path(config_path).parent / "games" / game_id / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            json.dumps(_DEFAULT_SETTINGS, ensure_ascii=False, indent=2),
            encoding="utf-8")


# ── Icon ───────────────────────────────────────────────────────────────────────

def icon_path() -> str:
    """Filesystem path to the app icon (.ico), or '' if unavailable."""
    try:
        return str(resources.files("grimoireassist.data") / "icon.ico")
    except Exception:
        return ""


# ── Icon migration (internal) ──────────────────────────────────────────────────

def _migrate_icons(data: dict, imp_dir: Path, data_file: Path) -> None:
    """One-time migration: copy per-monster icon files to images/icons/ and
    update img_paths in place. Deletes old per-monster image directories."""
    import shutil

    needs = any(
        len(p.replace("\\", "/").split("/")) >= 3
        and p.replace("\\", "/").split("/")[1] != "icons"
        for monster, sections in data.items()
        if not monster.startswith("_") and isinstance(sections, dict)
        for sec_val in sections.values()
        for row in (sec_val.get("rows", sec_val) if isinstance(sec_val, dict) else sec_val)
        if isinstance(row, list)
        for cell in row
        if isinstance(cell, dict)
        for p in cell.get("img_paths", [])
    )
    if not needs:
        return

    icons_dir = imp_dir / "images" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    url_to_new: dict = {}

    for monster, sections in data.items():
        if monster.startswith("_") or not isinstance(sections, dict):
            continue
        for sec_val in sections.values():
            rows = sec_val.get("rows", sec_val) if isinstance(sec_val, dict) else sec_val
            if not isinstance(rows, list):
                continue
            for row in rows:
                for cell in row:
                    if not isinstance(cell, dict):
                        continue
                    for sub in cell.get("sub_items", []):
                        sub["img_paths"] = [
                            _icon_new_path(u, op, url_to_new, imp_dir, icons_dir)
                            for op, u in zip(sub.get("img_paths", []), sub.get("imgs", []))
                        ]
                    cell["img_paths"] = [
                        _icon_new_path(u, op, url_to_new, imp_dir, icons_dir)
                        for op, u in zip(cell.get("img_paths", []), cell.get("imgs", []))
                    ]

    data_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    images_dir = imp_dir / "images"
    if images_dir.exists():
        for d in images_dir.iterdir():
            if d.is_dir() and d.name != "icons":
                shutil.rmtree(d, ignore_errors=True)


def _icon_new_path(url: str, old_rel: str, url_to_new: dict,
                   imp_dir: Path, icons_dir: Path) -> str:
    if url not in url_to_new:
        fname = url.rsplit("/", 1)[-1].split("?")[0] or "icon.png"
        new_rel = f"images/icons/{fname}"
        url_to_new[url] = new_rel
        dest = imp_dir / new_rel
        if not dest.exists():
            src = imp_dir / old_rel
            if src.exists():
                import shutil
                shutil.copy2(src, dest)
    return url_to_new[url]
