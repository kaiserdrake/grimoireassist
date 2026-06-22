# GrimoireAssist

A Windows desktop assistant that sits between a **capture-card game feed** and the rest of your
machine. It:

1. **Re-publishes** the capture feed as a **virtual camera** (OBS Virtual Camera) so other apps
   (OBS, Discord, browsers, etc.) can use the same source **in parallel** — the virtual feed is
   **clean** (no overlays).
2. **Displays** the live feed in its own window with overlays.
3. **Runs OCR** on fixed UI regions to drive a battle state machine:
   *battle started → which monsters are present → battle ended*.
4. **Fetches monster info** by scraping a wiki HTML page and shows it in a **toggleable overlay**,
   clearing it when a monster dies or the battle ends.

## Requirements

- Windows 10/11
- **OBS Virtual Camera** driver installed (ships with OBS Studio). Required for feature #1.
- Python 3.12 (the launcher creates a venv for you).

## Quick start

```bat
run.bat
```

First run creates `.venv`, installs dependencies (this downloads PyTorch + EasyOCR model
weights — a few hundred MB), then launches the app.

To run manually:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m grimoireassist
```

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

| Key        | Action                       |
|------------|------------------------------|
| F9         | Open region calibration       |

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
  ui/              main window (☰ menu), monster panel (web view),
                   calibration dialog, game-select dialog
```

## Tests

```bat
.venv\Scripts\activate
pip install pytest
pytest
```

Covers the battle state machine transitions and the wiki scraper (against an HTML fixture) — no
camera or GUI required.
