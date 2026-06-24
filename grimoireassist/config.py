"""Configuration model: dataclasses + YAML load/save.

Global settings (camera, OCR engine, GPU) live at the top level. Per-game settings
(currently the OCR monster regions) live under `games.<id>`. The active game's
regions / site URL / monster list are populated at runtime from the selected game.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class Region:
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    def is_set(self) -> bool:
        return self.w > 0 and self.h > 0

    def as_slice(self):
        """Return numpy slice tuple (rows, cols) for cropping a frame."""
        return (slice(self.y, self.y + self.h), slice(self.x, self.x + self.w))


@dataclass
class CaptureConfig:
    device_index: int = 0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_file: Optional[str] = None


@dataclass
class VirtualCameraConfig:
    enabled: bool = True


_DEFAULT_END_KEYWORDS = ["result", "victory", "defeat"]


@dataclass
class OcrConfig:
    engine: str = "easyocr"
    poll_fps: float = 3.0
    languages: List[str] = field(default_factory=lambda: ["en"])
    gpu: bool = False
    debounce_frames: int = 3
    continuous: bool = True  # vestigial; monster detection is always on
    monster_persist_s: float = 12.0      # keep a monster this long after it's last seen
    monster_persist_end_s: float = 1.0   # shorter retention while "Battle End" text shows
    # active per-game values (populated at runtime, not serialized here)
    regions_monster_names: List[Region] = field(default_factory=lambda: [Region()])
    regions_battle_end: Region = field(default_factory=Region)
    keywords_battle_end: List[str] = field(default_factory=lambda: list(_DEFAULT_END_KEYWORDS))


@dataclass
class GameSettings:
    """Per-game OCR layout: the monster name region(s), an optional Battle-End
    trigger region, and the keyword text that marks the battle end."""
    monster_names: List[Region] = field(default_factory=lambda: [Region()])
    battle_end: Region = field(default_factory=Region)
    end_keywords: List[str] = field(default_factory=lambda: list(_DEFAULT_END_KEYWORDS))


@dataclass
class UiConfig:
    always_on_top: bool = False


def _region(d: dict | None) -> Region:
    d = d or {}
    return Region(int(d.get("x", 0)), int(d.get("y", 0)),
                  int(d.get("w", 0)), int(d.get("h", 0)))


@dataclass
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    virtual_camera: VirtualCameraConfig = field(default_factory=VirtualCameraConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    monster_name_list: List[str] = field(default_factory=list)  # active list
    ui: UiConfig = field(default_factory=UiConfig)
    selected_game: Optional[str] = None
    games: Dict[str, GameSettings] = field(default_factory=dict)  # per-game settings
    _path: Optional[Path] = None

    # ---- per-game settings --------------------------------------------
    def regions_for(self, game_id: str) -> GameSettings:
        return self.games.get(game_id) or GameSettings()

    def set_regions_for(self, game_id: str, settings: GameSettings) -> None:
        self.games[game_id] = settings

    # ---- loading -------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            cfg = cls()
            cfg._path = path
            return cfg
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = cls.from_dict(raw)
        cfg._path = path
        return cfg

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        cap = raw.get("capture", {})
        vc = raw.get("virtual_camera", {})
        ocr = raw.get("ocr", {})
        ui = raw.get("ui", {})

        # per-game settings
        games: Dict[str, GameSettings] = {}
        for gid, g in (raw.get("games") or {}).items():
            regions = (g or {}).get("regions", {})
            keywords = (g or {}).get("keywords", {})
            mons = regions.get("monster_names") or [{}]
            end_kw = [str(k) for k in (keywords.get("battle_end") or [])]
            games[gid] = GameSettings(
                monster_names=[_region(r) for r in mons],
                battle_end=_region(regions.get("battle_end")),
                end_keywords=end_kw or list(_DEFAULT_END_KEYWORDS),
            )

        selected_game = raw.get("selected_game")

        cfg = cls(
            capture=CaptureConfig(
                device_index=int(cap.get("device_index", 0)),
                width=int(cap.get("width", 1920)),
                height=int(cap.get("height", 1080)),
                fps=int(cap.get("fps", 30)),
                video_file=cap.get("video_file"),
            ),
            virtual_camera=VirtualCameraConfig(enabled=bool(vc.get("enabled", True))),
            ocr=OcrConfig(
                engine=str(ocr.get("engine", "easyocr")),
                poll_fps=float(ocr.get("poll_fps", 3.0)),
                languages=list(ocr.get("languages", ["en"])),
                gpu=bool(ocr.get("gpu", False)),
                debounce_frames=int(ocr.get("debounce_frames", 3)),
                continuous=bool(ocr.get("continuous", True)),
                monster_persist_s=float(ocr.get("monster_persist_s", 12.0)),
                monster_persist_end_s=float(ocr.get("monster_persist_end_s", 1.0)),
            ),
            ui=UiConfig(always_on_top=bool(ui.get("always_on_top", False))),
            selected_game=selected_game,
            games=games,
        )
        cls._migrate_single_game(cfg, raw)
        return cfg

    @staticmethod
    def _migrate_single_game(cfg: "Config", raw: dict) -> None:
        """Carry a pre-multi-game config (single `ocr.regions` + `site`) into the
        new per-game shape so the user keeps their calibrated region."""
        if cfg.games:
            return
        old_regions = (raw.get("ocr", {}).get("regions", {}) or {}).get("monster_names")
        if not old_regions:
            return
        site_url = (raw.get("site", raw.get("wiki", {})) or {}).get("url_template", "")
        from .games import load_catalog
        import re

        def num(u: str) -> str:
            m = re.search(r"/(\d+)/monsters", u or "")
            return m.group(1) if m else ""

        gid = None
        for g in load_catalog():
            if num(g.site_url_template) and num(g.site_url_template) == num(site_url):
                gid = g.id
                break
        if gid is None and load_catalog():
            gid = load_catalog()[0].id
        if gid:
            cfg.games[gid] = GameSettings(monster_names=[_region(r) for r in old_regions])
            if not cfg.selected_game:
                cfg.selected_game = gid

    # ---- saving --------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "selected_game": self.selected_game,
            "capture": {
                "device_index": self.capture.device_index,
                "width": self.capture.width,
                "height": self.capture.height,
                "fps": self.capture.fps,
                **({"video_file": self.capture.video_file} if self.capture.video_file else {}),
            },
            "virtual_camera": {"enabled": self.virtual_camera.enabled},
            "ocr": {
                "engine": self.ocr.engine,
                "poll_fps": self.ocr.poll_fps,
                "languages": self.ocr.languages,
                "gpu": self.ocr.gpu,
                "debounce_frames": self.ocr.debounce_frames,
                "continuous": self.ocr.continuous,
                "monster_persist_s": self.ocr.monster_persist_s,
                "monster_persist_end_s": self.ocr.monster_persist_end_s,
            },
            "ui": {"always_on_top": self.ui.always_on_top},
            "games": {
                gid: {
                    "regions": {
                        "monster_names": [asdict(r) for r in gs.monster_names],
                        "battle_end": asdict(gs.battle_end),
                    },
                    "keywords": {"battle_end": gs.end_keywords},
                }
                for gid, gs in self.games.items()
            },
        }

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path or self._path or "config.yaml")
        target.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        self._path = target
