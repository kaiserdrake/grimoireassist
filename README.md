# GrimoireAssist

A Windows desktop assistant that sits between a **capture-card game feed** and the rest of your
machine. It:

1. **Re-publishes** the capture feed as a **virtual camera** (OBS Virtual Camera) so other apps
   (OBS, Discord, browsers, etc.) can use the same source **in parallel** — the virtual feed is
   **clean** (no overlays).
2. **Runs OCR** (EasyOCR, GPU-accelerated) on calibrated regions to detect monster names in real
   time, with confidence scoring and fuzzy matching against the game's monster list.
3. **Shows an embedded monster info page** for the detected monster(s) inside the app — the source
   is configurable per game (e.g. your **[Grimoire](https://grimoire.laeradsphere.com/)** notes for
   MH Stories 3, or monsterbuddy.app for MH Stories 2).
4. **Auto-switches** between the monster view and your Grimoire notes, or stays locked to either.

It's built with PyQt6 + QtWebEngine; the window itself never renders the camera feed (that was the
main CPU cost) — only the detection navbar and the embedded info page.

## Requirements

- Windows 10/11
- **Python 3.12**
- **OBS Virtual Camera** driver — ships with [OBS Studio](https://obsproject.com/). Required only
  for the virtual-camera output.
- Optional but recommended: an **NVIDIA GPU** (for fast OCR).

## Installation

1. **Install Python 3.12** from <https://www.python.org/downloads/> (tick *"Add python.exe to
   PATH"*).
2. **Launch** — first run creates the venv and installs everything:
   ```bat
   cd path\to\GrimoireAssist
   run.bat
   ```
   `run.bat` is the **single entry point**: it creates `.venv` and installs dependencies on first
   run (downloads PyTorch + EasyOCR weights, a few hundred MB), then on every run launches the app
   **windowless** (no lingering console) and exits. It rebuilds the environment automatically if
   `.venv` is ever missing.

### Desktop / taskbar shortcut

A `GrimoireAssist` desktop shortcut (game-controller icon) launches the app via `run.bat`. Right-
click it → **Pin to taskbar** for one-click launch. The app sets its own taskbar identity so the
window shows the same icon.

### GPU acceleration (recommended)

OCR is much faster on an NVIDIA GPU. The default install pulls **CPU-only** PyTorch; install the
CUDA build once:

```bat
.venv\Scripts\activate
pip install --force-reinstall torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
```

Then enable it via **☰ → Use GPU for OCR** (or `ocr.gpu: true` in `config.yaml`). Verify with
`python -c "import torch; print(torch.cuda.is_available())"` → `True`. (Use `cu121` for older
drivers.) No NVIDIA GPU? Leave GPU off — it runs on CPU.

### CLI flags

```bat
python -m grimoireassist --list-devices               REM list capture device indices
python -m grimoireassist --device 1                   REM force a device index
python -m grimoireassist --video samples\clip.mp4     REM test against a recorded clip
```

> Only one instance can use the capture device at a time, so a second launch is refused
> ("GrimoireAssist is already running").

## First launch & calibration

1. On first launch a **game-select page** appears — pick your game.
2. Select your capture card from **☰ → Camera** (OBS Virtual Camera is hidden to avoid feedback;
   friendly device names are shown). The **Source** pill turns green when frames arrive.
3. Press **F9** (or **☰ → Calibrate regions…**) and define, on a frozen frame:
   - **`monster_1..N`** — the area(s) where monster names appear. Drag to draw; drag the body to
     move; drag a corner to resize.
   - **`battle_end`** *(optional)* — the area where end text (e.g. "Result") appears, plus the
     **Battle-End text** field. When that text shows, monster retention drops so the list clears
     promptly after a fight.
   Click **Save** — regions are stored per game and applied live (no restart).
4. **Import monster data** via **☰ → Import monster data…** — this populates the monster list used
   for fuzzy matching (without it there's nothing to match detections against) and the in-app
   monster cards. See [Importing monster data](#importing-monster-data).

The window opens at ~60% of your screen (centred), not maximized.

## The navbar

Left → right: **☰ menu**, the **status pills**, the **monster pills**, and the **view switch**.

- **Status pills** (gray background): **Source** (green `Source Active` / red `No Source` /
  `Connecting…`) and **Tracking** (green `Tracking` / `Idle`).
- **Monster pills** — one per detected monster, **color-coded by OCR confidence**: green = high,
  orange = mid, yellow = low (hover shows the %). Confidence is **sticky at the best level reached**
  until the monster clears, so it never flickers down. Click a pill to focus that monster's page.
- **View switch** — `Auto Switch` (default) vs `Grimoire`:
  - **Auto Switch** — shows the detected monsters while in a fight; 1 minute after the last
    monster disappears it switches to your full Grimoire notes. The app opens on this view.
  - **Grimoire** — locks to the notes view; no auto-switching.

## The ☰ menu

- **Camera**
  - **Select source… ▸** — pick the capture device (live device list).
  - **Retry camera** — re-open the current device (e.g. after another app released it).
  - **Calibrate regions… (F9)** — see above.
  - **Snapshot frame (Ctrl+Alt+S)** — save the current capture frame to `snapshots/` as a
    timestamped PNG. The hotkey is **system-wide** (works while the app is unfocused), so it can
    be bound to a StreamDeck button or macro key that sends the combination. Change it via
    `ui.snapshot_hotkey` in `config.yaml`.
- **OCR**
  - **Use GPU** — toggle GPU/CPU OCR live (model reloads on the first read after switching).
  - **Track confidence ▸** — minimum confidence required to track a monster: *Low and up (all)*,
    *Mid and up*, or *High only*. Lower-confidence detections are filtered out.
  - **Auto-start tracking on launch** — start OCR tracking automatically when the app opens
    instead of waiting for the **▶ Start** button. **Off by default.**
- **Game**
  - **Add game…** — add a new game to the catalog (id, name, info-URL template, options).
  - **Switch game…** — reopen the game picker.
  - **Import monster data…** — fetch the monster list + info from your Grimoire notes (see below).
- **Window** — **Always on top** / **Fullscreen (F11)**.
- **Debug**
  - **Show OCR debug log** — show the in-app panel of raw/matched OCR text plus a manual
    *Test OCR* box for trying name matches.
  - **Log to file** — also write that debug log to `logs/ocr_<timestamp>.log`. **Off by default.**

## Detection behaviour

Monster detection is **persistent** so attack animations (which briefly hide the name) don't make
the display flicker:

- A detected monster stays for **`monster_persist_s`** seconds (default **12**) after its name was
  last read, then clears.
- While the **Battle-End** text shows, retention drops to **`monster_persist_end_s`** (default
  **1s**).
- OCR text is fuzzy-matched to the game's bundled monster list; UI text and noise that don't
  resemble a real monster are rejected (tunable via `ocr.match_cutoff`, default 0.7 — higher is
  stricter). This stops menu text like "Set Red Pin" from matching a monster.
- Idle/unchanged frames cost ~0 GPU (change-detection + a heartbeat re-read).

## Monster info sources (per game)

The game catalog lives at `games/games.json` (next to `config.yaml`, user-editable). Each entry
defines where monster info comes from:

| Field | Meaning | Default |
|-------|---------|---------|
| `id` | short game id (used for the `games/<id>/` folder) | — |
| `name` | display name in the picker | — |
| `site_url_template` | info URL; `{name}` is substituted | — |
| `url_style` | `"path"` (slug in path, one monster) or `"search"` (query term, multi-monster) | `"path"` |
| `multi_joiner` | separator for multiple monsters (search style) | `" \|\| "` |
| `requires_login` | monster view uses the persistent logged-in profile | `false` |
| `notes_url` | the secondary view shown in `Grimoire` mode (blank → grimoire) | `""` |
| `cards_per_row` | monster cards shown per row in the panel | `4` |

Built-in games:

- **MH Stories 3** → your Grimoire notes (`url_style: search`, `requires_login: true`). Multiple
  detected monsters are shown at once via `&st1=monA || monB` (sorted, so detection order doesn't
  reload the page). The slug is the lowercase monster name.
- **MH Stories 2** → monsterbuddy.app (`url_style: path`, single monster).

**Adding a game:** use **☰ → Add game…** (writes the catalog entry and a default
`games/<id>/settings.json`), then **calibrate regions** and **import monster data**. A standard wiki
(path style, no login) is plug-and-play; a login-gated, multi-monster site can set `requires_login`,
`multi_joiner`, and `notes_url`.

## Importing monster data

The **monster list used for fuzzy matching comes from imported data** — until you import, there is
nothing to match against. **☰ → Import monster data…** fetches your monster notes straight from
Grimoire's raw-markdown API and saves them locally to `games/<id>/import/` (`data.json` + downloaded
`images/`), which also feeds the in-app monster cards.

- The API URL is auto-derived from the game's `notes_url` (`?fileId=NN`); a field is shown so you
  can paste it manually if needed. You must be logged into Grimoire first (open the notes view).
- The importer parses `## Monster`, `### Section`, and `**Key:** value` markdown (the `#`-level is
  what matters, not the heading text). Anything inside fenced ```` ``` ```` code blocks is ignored.
- A section can carry a `:::meta … :::` directive block:
  - `type:` — `key-value-pair` (default), `table-col-row`, or `table-row-col`.
  - `header: false` — render the section's rows without showing its heading.

## Grimoire view, login & camera

The `Grimoire` view (and MHS3's monster view) load your Grimoire notes. Your **login is persisted**
on disk, so you sign in once. The embedded view is granted **camera/mic permission**, so Grimoire's
**Insert Image from Camera** works — including the camera dropdown when multiple cameras exist (pick
a camera other than the one the app is capturing).

## Configuration files

**`config.yaml`** — global settings (next to the package):

```yaml
selected_game: mhs3
capture: { device_index, width, height, fps }
virtual_camera: { enabled }
ocr:
  engine: auto                     # auto | easyocr | tesseract
  gpu: true
  poll_fps: 3.0
  monster_persist_s: 12.0          # retention after a name was last seen
  monster_persist_end_s: 1.0       # retention while Battle-End text shows
  min_confidence_level: low        # low | mid | high
  match_cutoff: 0.7                # fuzzy-match strictness
ui:
  always_on_top: false
  auto_start_tracking: false       # start OCR tracking as soon as the app opens
  snapshot_hotkey: ctrl+alt+s      # system-wide snapshot hotkey (ctrl/alt/shift/win + key or F1–F24)
logging: { to_file: false }        # write the OCR debug log to logs/ (☰ → Log to file)
```

**Per-game settings** live in `games/<id>/settings.json` (written by calibration / Add Game), not
in `config.yaml`:

```json
{
  "regions": { "monster_names": [ {"x":0,"y":0,"w":0,"h":0} ], "battle_end": {"x":0,"y":0,"w":0,"h":0} },
  "keywords": { "battle_end": ["result", "victory", "defeat"] },
  "monster_persist_s": 12.0,
  "monster_persist_end_s": 1.0
}
```

The two `monster_persist_*` values are optional per-game overrides of the global `ocr` defaults.

## Hotkeys

| Key        | Action                  | Scope |
|------------|-------------------------|-------|
| F9         | Open region calibration | in-app |
| F11        | Toggle fullscreen       | in-app |
| Ctrl+Alt+S | Save frame snapshot     | **system-wide** (StreamDeck / macro-key friendly; configurable via `ui.snapshot_hotkey`) |

If the snapshot combination is already taken by another app, GrimoireAssist falls back to an
in-app shortcut and says so in the status bar — pick a different `ui.snapshot_hotkey` in that case.

## How it works

A single **capture thread** owns the physical device and fans frames out to (a) the virtual-camera
sink (clean) and (b) a one-slot buffer read by the OCR worker. The clean feed always goes to the
virtual camera; the window shows only the detection navbar and the embedded info page.

```
grimoireassist/
  __main__.py      entry / CLI / single-instance guard / dark theme / app icon / game-select
  config.py        YAML config (global) + per-game GameSettings (settings.json) + logging toggle
  capture.py       device owner + frame fan-out + named-device enumeration
  virtualcam.py    OBS Virtual Camera sink (clean feed)
  hotkey.py        system-wide hotkey (Win32 RegisterHotKey via Qt native event filter)
  games.py         game catalog (GameInfo) loader + import-data helpers + app icon
  ocr/             OcrEngine + EasyOCR/Tesseract impls (confidence) + preprocessing + level helpers
  battle.py        MonsterTracker (persistent + confidence) + match_known + OcrWorker (QThread)
  overlay.py       OverlayModel (UI state)
  data/            app icon (icon.ico)
  ui/              main window (☰ menu, pills, view switch), monster panel (web views),
                   monster cards, calibration dialog, game-select & add-game dialogs,
                   import wizard

# Runtime data (next to config.yaml, created/edited at use):
games/
  games.json            game catalog
  <id>/settings.json    per-game regions + keywords + persist overrides
  <id>/import/          imported monster data.json + images/
logs/                   OCR debug logs (only when ☰ → Log to file is on)
snapshots/              frame snapshots saved by the snapshot hotkey (☰ → Snapshot frame)
```

## Tests

```bat
.venv\Scripts\activate
pip install pytest
pytest
```

Covers the persistent tracker (retention, confidence stickiness, UI-text rejection), the URL
builder (path/search styles, multi-monster encoding, custom joiner), the game catalog/config, and
config round-tripping — no camera or GUI required.
