"""Configuration model: dataclasses + YAML load/save.

Global settings (camera, OCR engine, GPU) live at the top level. Per-game settings
(currently the OCR monster regions) live under `games.<id>`. The active game's
regions / site URL / monster list are populated at runtime from the selected game.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Union

import yaml


def _parse_gpu(value) -> Union[bool, str]:
    """ocr.gpu accepts true / false / "auto" (use the GPU iff CUDA is available)."""
    if isinstance(value, str) and value.strip().lower() == "auto":
        return "auto"
    return bool(value)


def _clamp01(value) -> float:
    """A 0..1 fraction from config, tolerant of junk."""
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def _pad_id(value, fallback: str) -> str:
    """Normalise a configured controller id to one the overlay knows about.
    `ui.controllers` is Qt-free, so this import stays headless-safe."""
    from .ui.controllers import valid_id
    return valid_id(value, fallback)


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
    engine: str = "auto"
    poll_fps: float = 3.0
    languages: List[str] = field(default_factory=lambda: ["en"])
    gpu: Union[bool, str] = "auto"   # true | false | "auto" (GPU iff CUDA is available)
    debounce_frames: int = 3
    continuous: bool = True  # vestigial; monster detection is always on
    monster_persist_s: float = 12.0      # keep a monster this long after it's last seen
    monster_persist_end_s: float = 1.0   # shorter retention while "Battle End" text shows
    min_confidence_level: str = "low"    # high|mid|low — minimum OCR confidence to track
    match_cutoff: float = 0.7            # fuzzy-match strictness (higher = stricter)
    # active per-game values (populated at runtime, not serialized here)
    regions_monster_names: List[Region] = field(default_factory=lambda: [Region()])
    regions_battle_end: Region = field(default_factory=Region)
    keywords_battle_end: List[str] = field(default_factory=lambda: list(_DEFAULT_END_KEYWORDS))

    def gpu_effective(self) -> bool:
        """Resolve the gpu setting to a concrete bool ("auto" → CUDA available?)."""
        if self.gpu == "auto":
            try:
                import torch
                return bool(torch.cuda.is_available())
            except Exception:
                return False
        return bool(self.gpu)


@dataclass
class GameSettings:
    """Per-game OCR layout: the monster name region(s), an optional Battle-End
    trigger region, and the keyword text that marks the battle end."""
    monster_names: List[Region] = field(default_factory=lambda: [Region()])
    battle_end: Region = field(default_factory=Region)
    end_keywords: List[str] = field(default_factory=lambda: list(_DEFAULT_END_KEYWORDS))
    monster_persist_s: Optional[float] = None       # overrides ocr.monster_persist_s when set
    monster_persist_end_s: Optional[float] = None   # overrides ocr.monster_persist_end_s when set


@dataclass
class UiConfig:
    always_on_top: bool = False
    auto_start_tracking: bool = False   # start OCR tracking as soon as the app opens
    snapshot_hotkey: str = "ctrl+alt+s"  # system-wide hotkey that saves a frame snapshot
    browser_split_ratio: float = 0.5    # main-pane share of the splitter when the browser is open
    search_engine: str = "google"       # default engine for non-address browser queries
    show_input_preview: bool = False    # live PiP of the raw capture frames over the tracking view
    preview_fps: float = 10.0           # PiP refresh rate; capped at 30 by the widget
    preview_width: int = 240            # PiP width in px (height follows the frame aspect);
                                        # set by dragging the preview's top-right grip
    idle_switch_s: int = 60             # seconds with no object detected before Auto Switch
                                        # falls back to the Grimoire view (min 1)
    show_controller_map: bool = False   # face-button reference over the main pane
    controller_map_left: str = "playstation"   # pad shown on the left (see ui.controllers)
    controller_map_right: str = "switch"       # pad shown on the right
    controller_map_width: int = 420     # overlay width in px; set by dragging its grip
    controller_map_x: float = 0.5       # position as a 0..1 share of the free space,
    controller_map_y: float = 0.08      # so a resized window keeps it in proportion
    controller_map_collapsed: bool = False  # folded down to the header pill


@dataclass
class ReviewConfig:
    """Rolling capture buffer behind the review screen (click the PiP).

    Frames are stored downscaled + JPEG-encoded in buffer/, so the disk cost is
    roughly `fps * minutes * 60 * frame_size`; at the defaults half an hour is
    around 1 GB. `max_disk_mb` is the hard ceiling — whichever of the two
    budgets binds first starts dropping the oldest frames.
    """
    enabled: bool = True
    minutes: float = 30.0          # length of the rolling window
    fps: float = 15.0              # frames kept per second (source fps is the cap)
    max_height: int = 720          # downscale tall sources to this (0 = keep as-is)
    jpeg_quality: int = 75         # 30..95
    max_disk_mb: int = 4096        # ceiling on the buffer directory


@dataclass
class GrimoireConfig:
    """Grimoire notes server.

    The browser drawer's bookmarks come from the focus README of `user`, fetched
    as raw markdown from `<base_url>/api/focus/readme/raw?user=<user>`. Links
    under that note's `# Bookmarks` heading become the new-tab bookmark list."""
    base_url: str = "https://grimoire.laeradsphere.com"
    user: str = ""

    def readme_raw_url(self) -> str:
        """Raw-markdown URL of the user's focus README, or "" if unconfigured."""
        import urllib.parse
        base = self.base_url.strip().rstrip("/")
        user = self.user.strip()
        if not base or not user:
            return ""
        return (f"{base}/api/focus/readme/raw"
                f"?user={urllib.parse.quote(user, safe='')}")


@dataclass
class LoggingConfig:
    to_file: bool = False  # write the OCR debug log to logs/<ts>.log


def _load_game_settings(path: "Path") -> "Optional[GameSettings]":
    """Read a games/<id>/settings.json file into a GameSettings object."""
    try:
        import json as _json
        raw = _json.loads(path.read_text(encoding="utf-8"))
        regions = raw.get("regions", {})
        keywords = raw.get("keywords", {})
        mons = regions.get("monster_names") or [{}]
        end_kw = [str(k) for k in (keywords.get("battle_end") or [])]
        raw_p = raw.get("monster_persist_s")
        raw_pe = raw.get("monster_persist_end_s")
        return GameSettings(
            monster_names=[_region(r) for r in mons],
            battle_end=_region(regions.get("battle_end")),
            end_keywords=end_kw or list(_DEFAULT_END_KEYWORDS),
            monster_persist_s=float(raw_p) if raw_p is not None else None,
            monster_persist_end_s=float(raw_pe) if raw_pe is not None else None,
        )
    except Exception:
        return None


def _save_game_settings(path: "Path", gs: "GameSettings") -> None:
    """Write a GameSettings object to games/<id>/settings.json."""
    import json as _json
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "regions": {
            "monster_names": [asdict(r) for r in gs.monster_names],
            "battle_end": asdict(gs.battle_end),
        },
        "keywords": {
            "battle_end": gs.end_keywords,
        },
    }
    if gs.monster_persist_s is not None:
        data["monster_persist_s"] = gs.monster_persist_s
    if gs.monster_persist_end_s is not None:
        data["monster_persist_end_s"] = gs.monster_persist_end_s
    path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    review: ReviewConfig = field(default_factory=ReviewConfig)
    grimoire: GrimoireConfig = field(default_factory=GrimoireConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    selected_game: Optional[str] = None
    games: Dict[str, GameSettings] = field(default_factory=dict)  # per-game settings
    _path: Optional[Path] = None

    # ---- per-game settings --------------------------------------------
    def regions_for(self, game_id: str) -> GameSettings:
        return self.games.get(game_id) or GameSettings()

    def set_regions_for(self, game_id: str, settings: GameSettings) -> None:
        self.games[game_id] = settings
        # Persist immediately to games/<id>/settings.json
        if self._path:
            _save_game_settings(
                Path(self._path).parent / "games" / game_id / "settings.json",
                settings)

    def effective_monster_persist_s(self) -> float:
        gs = self.games.get(self.selected_game or "")
        if gs and gs.monster_persist_s is not None:
            return gs.monster_persist_s
        return self.ocr.monster_persist_s

    def effective_monster_persist_end_s(self) -> float:
        gs = self.games.get(self.selected_game or "")
        if gs and gs.monster_persist_end_s is not None:
            return gs.monster_persist_end_s
        return self.ocr.monster_persist_end_s

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
        cls._migrate_single_game(cfg, raw)   # needs _path to find the catalog
        # Migrate any game settings still in config.yaml → settings.json
        for gid, gs in list(cfg.games.items()):
            sj = path.parent / "games" / gid / "settings.json"
            if not sj.exists():
                _save_game_settings(sj, gs)

        # Load (or reload) per-game settings from games/<id>/settings.json.
        # settings.json is authoritative; config.yaml entries are only a fallback
        # for installations that haven't migrated yet.
        games_dir = path.parent / "games"
        if games_dir.is_dir():
            for game_dir in games_dir.iterdir():
                if not game_dir.is_dir():
                    continue
                sj = game_dir / "settings.json"
                if sj.exists():
                    gs = _load_game_settings(sj)
                    if gs is not None:
                        cfg.games[game_dir.name] = gs
        return cfg

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        cap = raw.get("capture", {})
        vc = raw.get("virtual_camera", {})
        ocr = raw.get("ocr", {})
        ui = raw.get("ui", {})
        rev = raw.get("review", {})
        grim = raw.get("grimoire", {}) or {}
        log = raw.get("logging", {})

        # per-game settings
        games: Dict[str, GameSettings] = {}
        for gid, g in (raw.get("games") or {}).items():
            regions = (g or {}).get("regions", {})
            keywords = (g or {}).get("keywords", {})
            mons = regions.get("monster_names") or [{}]
            end_kw = [str(k) for k in (keywords.get("battle_end") or [])]
            raw_persist = (g or {}).get("monster_persist_s")
            raw_persist_end = (g or {}).get("monster_persist_end_s")
            games[gid] = GameSettings(
                monster_names=[_region(r) for r in mons],
                battle_end=_region(regions.get("battle_end")),
                end_keywords=end_kw or list(_DEFAULT_END_KEYWORDS),
                monster_persist_s=float(raw_persist) if raw_persist is not None else None,
                monster_persist_end_s=float(raw_persist_end) if raw_persist_end is not None else None,
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
                gpu=_parse_gpu(ocr.get("gpu", "auto")),
                debounce_frames=int(ocr.get("debounce_frames", 3)),
                continuous=bool(ocr.get("continuous", True)),
                monster_persist_s=float(ocr.get("monster_persist_s", 12.0)),
                monster_persist_end_s=float(ocr.get("monster_persist_end_s", 1.0)),
                min_confidence_level=str(ocr.get("min_confidence_level", "low")),
                match_cutoff=float(ocr.get("match_cutoff", 0.7)),
            ),
            ui=UiConfig(
                always_on_top=bool(ui.get("always_on_top", False)),
                auto_start_tracking=bool(ui.get("auto_start_tracking", False)),
                snapshot_hotkey=str(ui.get("snapshot_hotkey", "ctrl+alt+s")),
                browser_split_ratio=float(ui.get("browser_split_ratio", 0.5)),
                search_engine=str(ui.get("search_engine", "google")),
                show_input_preview=bool(ui.get("show_input_preview", False)),
                preview_fps=float(ui.get("preview_fps", 10.0)),
                preview_width=int(ui.get("preview_width", 240)),
                idle_switch_s=max(1, int(ui.get("idle_switch_s", 60))),
                show_controller_map=bool(ui.get("show_controller_map", False)),
                controller_map_left=_pad_id(
                    ui.get("controller_map_left"), "playstation"),
                controller_map_right=_pad_id(
                    ui.get("controller_map_right"), "switch"),
                controller_map_width=max(260, int(ui.get("controller_map_width", 420))),
                controller_map_x=_clamp01(ui.get("controller_map_x", 0.5)),
                controller_map_y=_clamp01(ui.get("controller_map_y", 0.08)),
                controller_map_collapsed=bool(
                    ui.get("controller_map_collapsed", False)),
            ),
            review=ReviewConfig(
                enabled=bool(rev.get("enabled", True)),
                minutes=max(0.5, float(rev.get("minutes", 30.0))),
                fps=min(max(float(rev.get("fps", 15.0)), 1.0), 60.0),
                max_height=max(0, int(rev.get("max_height", 720))),
                jpeg_quality=min(max(int(rev.get("jpeg_quality", 75)), 30), 95),
                max_disk_mb=max(64, int(rev.get("max_disk_mb", 4096))),
            ),
            grimoire=GrimoireConfig(
                base_url=str(grim.get(
                    "base_url", "https://grimoire.laeradsphere.com")).strip(),
                user=str(grim.get("user", "") or "").strip(),
            ),
            logging=LoggingConfig(to_file=bool(log.get("to_file", False))),
            selected_game=selected_game,
            games=games,
        )
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

        catalog = load_catalog(cfg._path)
        gid = None
        for g in catalog:
            if num(g.site_url_template) and num(g.site_url_template) == num(site_url):
                gid = g.id
                break
        if gid is None and catalog:
            gid = catalog[0].id
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
                # video_file is deliberately NOT saved: it's a testing override
                # (--video / hand-edited yaml) and must never stick to a normal
                # capture-card launch.
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
                "min_confidence_level": self.ocr.min_confidence_level,
                "match_cutoff": self.ocr.match_cutoff,
            },
            "ui": {
                "always_on_top": self.ui.always_on_top,
                "auto_start_tracking": self.ui.auto_start_tracking,
                "snapshot_hotkey": self.ui.snapshot_hotkey,
                "browser_split_ratio": self.ui.browser_split_ratio,
                "search_engine": self.ui.search_engine,
                "show_input_preview": self.ui.show_input_preview,
                "preview_fps": self.ui.preview_fps,
                "preview_width": self.ui.preview_width,
                "idle_switch_s": self.ui.idle_switch_s,
                "show_controller_map": self.ui.show_controller_map,
                "controller_map_left": self.ui.controller_map_left,
                "controller_map_right": self.ui.controller_map_right,
                "controller_map_width": self.ui.controller_map_width,
                "controller_map_x": self.ui.controller_map_x,
                "controller_map_y": self.ui.controller_map_y,
                "controller_map_collapsed": self.ui.controller_map_collapsed,
            },
            "review": {
                "enabled": self.review.enabled,
                "minutes": self.review.minutes,
                "fps": self.review.fps,
                "max_height": self.review.max_height,
                "jpeg_quality": self.review.jpeg_quality,
                "max_disk_mb": self.review.max_disk_mb,
            },
            "grimoire": {
                "base_url": self.grimoire.base_url,
                "user": self.grimoire.user,
            },
            "logging": {"to_file": self.logging.to_file},
            # Per-game settings are stored in games/<id>/settings.json, not here.
        }

    def save(self, path: str | Path | None = None) -> None:
        target = Path(path or self._path or "config.yaml")
        target.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        self._path = target
        # Flush any in-memory game settings to their settings.json files
        for gid, gs in self.games.items():
            _save_game_settings(target.parent / "games" / gid / "settings.json", gs)
