# GrimoireAssist

A Windows desktop assistant that sits between a **capture-card game feed** and the rest of your
machine. It:

1. **Re-publishes** the capture feed as a **virtual camera** (OBS Virtual Camera) so other apps
   (OBS, Discord, browsers, etc.) can use the same source **in parallel** — the virtual feed is
   **clean** (no overlays).
2. **Runs OCR** on a fixed UI region to detect monster names in real time.
3. **Shows an embedded monster info page** (monsterbuddy.app) for each detected monster inside
   the app window — one button per monster, switches instantly.
4. **Integrates with [Grimoire](https://grimoire.laeradsphere.com/)** via a floating 🔮 button
   that opens the tracker inside the app. Auth cookies are persisted so you stay logged in
   between sessions. OCR results will open the relevant Grimoire endpoint automatically.

## Requirements

- Windows 10/11
- **Python 3.12** — see Installation below.
- **OBS Virtual Camera** driver — ships with [OBS Studio](https://obsproject.com/). Required
  for the virtual camera feature.

## Installation

### 1 — Install Python 3.12

Download the installer from <https://www.python.org/downloads/release/python-3120/> and run it.
Tick **"Add python.exe to PATH"** before clicking Install.

Verify in a new terminal:

```bat
python --version
```

### 2 — Launch (first run installs everything automatically)

```bat
cd path\to\grimoireassist
run.bat
```

`run.bat` creates the `.venv`, installs all dependencies, then starts the app. On subsequent
runs it skips straight to launching.

> **Note:** the first run downloads PyTorch and EasyOCR model weights — allow a few hundred
> MB of downloads.

### Useful flags

```bat
python -m grimoireassist --list-devices      REM print capture device indices
python -m grimoireassist --device 1          REM pick a device index
python -m grimoireassist --video samples\battle.mp4   REM test against a recorded clip
```

## GPU acceleration (recommended)

OCR (EasyOCR) is much faster on an NVIDIA GPU. The default `pip install` pulls the
**CPU-only** PyTorch, so you must reinstall the CUDA build once:

```bat
.venv\Scripts\activate
pip install --force-reinstall torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
```

Then set `ocr.gpu: true` in `config.yaml`. Verify with:

```bat
python -c "import torch; print(torch.cuda.is_available())"
```

It should print `True`. (cu124 works with current NVIDIA drivers; for older drivers use
`cu121`.) If you have no NVIDIA GPU, leave `ocr.gpu: false`.

## Display & detection mode

This window does **not** render the camera feed. It shows a button per detected monster and an
**embedded web page** (the monster info site, monsterbuddy.app by default) for the selected
monster. Rendering 1080p frames was the main CPU cost, so it's omitted; the **clean feed still
goes to the virtual camera** for other apps. Use **☰ → Always on top** to keep the panel
visible while you play.

**Continuous mode (default, `ocr.continuous: true`):** the app simply OCRs the `monster_names`
region every poll and shows whatever names it reads (each detected line is a separate entry),
clearing them when they leave the area. No battle start/end detection — only the `monster_names`
region matters; `battle_status` and the `keywords` are ignored.

Set `ocr.continuous: false` to use the battle state machine instead, which gates monster
detection between a battle-start and battle-end keyword (uses `battle_status` + `keywords`).

## Grimoire integration

Click the **🔮** button (bottom-right corner of the app) to toggle the
[Grimoire](https://grimoire.laeradsphere.com/) game tracker inside the app. Your login session
is stored on disk and restored automatically on next launch — you only need to log in once.

In future versions, OCR results will open the corresponding Grimoire endpoint directly (e.g. a
detected monster will jump to its Grimoire entry).

## Games

The app supports multiple games. On first launch a **game selection page** appears; pick the
game you're playing. Switch later via the **☰ menu → Switch game…**.

Each game is defined in `grimoireassist/data/games.json` (display name, monster info site URL
template, and a bundled monster list `monsters_<x>.json`). Built in: **MH Stories 3** and
**MH Stories 2** (monsterbuddy.app). To add another game, add a catalog entry plus its monster
list file.

Settings are **per game**: the OCR `monster_names` region is stored under `games.<id>` in
`config.yaml`, so each game keeps its own calibration. Global settings (camera, GPU, OCR engine)
are shared.

## The ☰ menu

Camera selection, calibration, always-on-top and game switching live behind the **burger (☰)**
button in the top-left:

- **Camera** — pick the capture device (OBS Virtual Camera is excluded to avoid feedback).
- **Calibrate regions… (F9)** — see below.
- **Always on top** — keep the panel above other windows.
- **Switch game…** — reopen the game picker.

## Calibration

Press **F9** (or ☰ → Calibrate) to define the **monster name region(s)** on a frozen frame:

- **Drag** on the frame to draw a region.
- **Drag the body** to move it; **drag a corner handle** to resize it.
- Use **Monster regions** to add more than one area if names appear in separate places.

Click **Save to config** — the region is saved for the current game and applied immediately
(no restart). Detected text is fuzzy-matched to that game's bundled monster list, so OCR
near-misses (e.g. "Rathaios" → "Rathalos") still resolve to the correct page and slug.

## Hotkeys

| Key | Action                  |
|-----|-------------------------|
| F9  | Open region calibration |
| F11 | Toggle fullscreen       |

## How it works

A single **capture thread** owns the physical device and fans frames out to (a) the virtual-camera
sink (clean) and (b) a one-slot buffer read by the OCR worker. The clean feed always goes to the
virtual camera; the window shows only detected monster pages.

```
grimoireassist/
  __main__.py      entry point / CLI / game-select on startup
  config.py        YAML config model (global + per-game)
  capture.py       device owner + frame fan-out
  virtualcam.py    OBS Virtual Camera sink (clean feed)
  games.py         game catalog + bundled monster directories
  ocr/             OcrEngine interface + EasyOCR/Tesseract impls + preprocessing
  battle.py        ContinuousDetector + BattleStateMachine + OcrWorker (QThread)
  overlay.py       OverlayModel (UI state)
  data/            games.json + monsters_<x>.json (bundled per-game data)
  ui/              main window (☰ menu), monster panel (web view + Grimoire toggle),
                   calibration dialog, game-select dialog
```

## Tests

```bat
.venv\Scripts\activate
pip install pytest
pytest
```

Covers the battle state machine transitions — no camera or GUI required.
