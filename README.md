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
5. **Keeps a rolling 30-minute buffer** of the capture feed so you can replay, scrub and save the
   fight you just had — click the input preview to open the review screen.

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

OCR is much faster on an NVIDIA GPU. `ocr.gpu` defaults to **`auto`**: the GPU is used whenever
CUDA is available, with silent fallback to CPU otherwise. **☰ → Use GPU** overrides it either way
(and pins the choice in `config.yaml` as `true`/`false`).

- The **portable build** ships CUDA-enabled PyTorch — GPU works out of the box, nothing to do.
- A **source install** pulls CPU-only PyTorch by default; install the CUDA build once:

```bat
.venv\Scripts\activate
pip install --force-reinstall torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
```

Verify with `python -c "import torch; print(torch.cuda.is_available())"` → `True`. (Use `cu121`
for older drivers.) No NVIDIA GPU? Nothing to do — `auto` resolves to CPU.

## Portable build (no Python required)

The app can also be distributed as a **portable folder**: extract
`GrimoireAssist-v<version>-win64.7z` anywhere and run `GrimoireAssist.exe` — no Python, no
install, no admin rights. (Windows 11 Explorer extracts `.7z` natively; on Windows 10 install
[7-Zip](https://www.7-zip.org/).) All user data (`config.yaml`, `games/`, `logs/`, `snapshots/`)
is created **next to the exe**, so the folder is self-contained and can be moved or copied to a
USB stick. Web logins live in `%AppData%\GrimoireAssist` and survive updates. The build bundles
CUDA-enabled PyTorch, so it's a single archive for everyone (see the GPU section — `auto` falls
back to CPU on machines without an NVIDIA GPU); the trade-off is size (~1.5 GB download, ~4 GB
unpacked).

### Building a release

```bat
build.bat 1.0.2
```

The optional version argument stamps `__version__` into `grimoireassist/__init__.py` (the single
source of truth) before building — that's how you set the version. Run `build.bat` with no argument
to build at the current `__version__` unchanged. The bump is **not** auto-committed; commit it
yourself.

That's the whole build: it creates a clean `.venv-build` (first run only), installs CUDA torch +
`requirements.txt` + PyInstaller, runs PyInstaller with `GrimoireAssist.spec`, and packs the
result (7z/LZMA2 — a plain zip would exceed GitHub's 2 GiB release-asset limit; needs
[7-Zip](https://www.7-zip.org/) installed):

- `dist\GrimoireAssist\GrimoireAssist.exe` — smoke-test this (window opens, camera list
  populates, ☰ → Test OCR works, Grimoire page loads). Ideally test on a machine **without
  Python** and from a path with spaces.
- `dist\GrimoireAssist-v<version>-win64.7z` — the release asset.

### Releasing an update

One-time setup: authenticate the [GitHub CLI](https://cli.github.com/) with `gh auth login`.

1. Change the code, run `pytest`, commit and push.
2. `build.bat <version>` (e.g. `build.bat 1.0.2`) — stamps and builds. Commit the version bump
   and push.
3. Smoke-test the exe, then `release.bat`.

`release.bat` publishes the **newest** `dist\GrimoireAssist-v*-win64.7z` and derives the tag from
that archive's filename — it does not rebuild, so an already-built archive is released as-is. It
refuses to overwrite an existing release, tags the commit (`v<version>`), pushes the tag, and
publishes a GitHub release with the `.7z` attached (`gh release create --generate-notes`). Because
the version comes from the archive, always `build.bat <version>` before releasing so source and
artifact stay in step.

**User update flow:** extract the new archive *next to* the old folder (not over it), copy
`config.yaml` and the `games/` folder across (calibrated regions + imported monster data), delete
the old folder. Logins carry over automatically via `%AppData%`.

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
- **Review**
  - **Review capture… (Ctrl+R)** — open the review screen for the rolling buffer (same as clicking
    the input preview).
  - **Keep rolling capture buffer** — record the last 30 minutes to `buffer/`. **On by default**;
    turning it off discards what is buffered.
  - **Open saved clips folder** — open `recordings/`.
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
- **Window** — **Always on top** / **Fullscreen (F11)** / **Input preview** /
  **Controller button map** / **Controller map ▸** (see below).
- **Debug**
  - **Show OCR debug log** — show the in-app panel of raw/matched OCR text plus a manual
    *Test OCR* box for trying name matches.
  - **Log to file** — also write that debug log to `logs/ocr_<timestamp>.log`. **Off by default.**

## Controller button map

Switching between a PlayStation, Switch, Xbox or Steam Deck pad mid-session is confusing: the four
face buttons share one physical diamond but carry different labels, and Nintendo swaps A/B relative
to Xbox. The overlay shows two pads side by side — face buttons in their real positions, plus
bumpers and triggers — so a glance tells you what the button under your thumb is called on the
other pad. There are no mapping lines; the shared geometry does the work.

**Turn it on** with the **🎮** navbar button or ☰ → *Window* → *Controller button map* (the two
always agree). **Pick the pair** under ☰ → *Window* → *Controller map ▸ Left pad / Right pad*.

It shows **only on the tracking view** — it disappears on the Grimoire view and while the review
screen is open, and comes back on its own.

- **Drag it anywhere** to move it. The position is stored as a share of the free space, so it keeps
  its relative place when you resize the window and never ends up off-screen.
- **Drag the bottom-right grip** to resize; the layout scales with the width.
- **Click the chevron** in the header (or double-click the header) to fold it down to a compact
  pill, in place. Click again to restore the exact size.

Position, size, collapsed state and the chosen pair are all saved to `config.yaml` under `ui:`.

## Video review (rolling buffer)

The app continuously keeps the **last 30 minutes** of the capture feed, so a fight is always
replayable after the fact — nothing has to be armed beforehand.

**Open it** by clicking the **input preview** (the PiP; enable it via ☰ → *Window* → *Input
preview*), or with **Ctrl+R**. The review screen takes over the main pane; the live feed and OCR
keep running behind it, and the buffer keeps recording while you watch.

- **Play / pause** — the ▶ button, the **Space** bar, or clicking the picture.
- **Playback speed** — `0.5×`, `0.75×`, `1×`, `1.5×`, `2×`. Timing comes from each frame's own
  capture timestamp, so the speeds are exact and a stall in the source doesn't drift the clock.
- **Drag to a frame** — drag the timeline (or click anywhere on it to jump straight there).
  `←` / `→` step one frame, `Shift` + `←` / `→` jump a second, the mouse wheel steps over the
  picture, `Home` / `End` go to the oldest / newest frame. The readout shows position, the frame
  number, and the wall-clock time that frame was captured.
- **Save clip** — writes everything currently in the buffer to `recordings/review_<timestamp>.mp4`
  (`.avi`/MJPG if this machine's OpenCV can't open the mp4 encoder). It runs in the background with
  a progress readout and can be cancelled; the 📁 button opens the folder.
- **Close** — the ✕ button or **Esc**, which returns you to the live PiP.

The buffer status (`⏺ Review buffer: 12:34`) sits in the status bar, and the PiP tooltip shows how
much is buffered so far.

### Cost, and how to tune it

Raw frames are not an option — 30 minutes of 1080p30 would be ~340 GB — so kept frames are
downscaled and JPEG-encoded into rolling segment files under `buffer/`. Only the index lives in
memory (a few MB). At the defaults (720p, 15 fps, quality 75) half an hour is roughly **1 GB of
disk**; `max_disk_mb` is a hard ceiling, and whichever budget binds first drops the oldest frames.
`buffer/` is a cache — it is wiped on exit and on the next start, so only **saved clips** persist.

Encoding happens on its own thread and the capture thread never waits on the disk: if the disk
can't keep up, frames are dropped instead of stalling capture or the virtual camera. Turn the whole
thing off with ☰ → *Review* → *Keep rolling capture buffer* (or `review.enabled: false`), which
costs nothing but also means there is nothing to review.

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
| `cards_per_row` | max monster cards per row in the panel (columns drop automatically on narrow windows, down to one full-width card per row) | `4` |

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
  gpu: auto                        # auto (GPU iff CUDA is available) | true | false
  poll_fps: 3.0
  monster_persist_s: 12.0          # retention after a name was last seen
  monster_persist_end_s: 1.0       # retention while Battle-End text shows
  min_confidence_level: low        # low | mid | high
  match_cutoff: 0.7                # fuzzy-match strictness
ui:
  always_on_top: false
  auto_start_tracking: false       # start OCR tracking as soon as the app opens
  snapshot_hotkey: ctrl+alt+s      # system-wide snapshot hotkey (ctrl/alt/shift/win + key or F1–F24)
  idle_switch_s: 60                # seconds with no object detected before Auto Switch
                                   # falls back to the Grimoire view
  show_controller_map: false       # controller button map over the tracking view (🎮)
  controller_map_left: playstation # playstation | switch | xbox | steamdeck
  controller_map_right: switch
  controller_map_width: 420        # overlay width in px; drag its corner grip
  controller_map_x: 0.5            # position as a 0–1 share of the free space,
  controller_map_y: 0.08           # so a resized window keeps it in proportion
  controller_map_collapsed: false  # folded down to the header pill
review:                            # rolling capture buffer behind the review screen
  enabled: true
  minutes: 30.0                    # length of the rolling window
  fps: 15.0                        # frames kept per second (the source fps is the cap)
  max_height: 720                  # downscale taller sources to this (0 = keep as captured)
  jpeg_quality: 75                 # 30–95; higher is bigger on disk
  max_disk_mb: 4096                # hard ceiling on buffer/ — whichever budget binds first wins
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
| Ctrl+R     | Open the video review   | in-app |
| Ctrl+Alt+S | Save frame snapshot     | **system-wide** (StreamDeck / macro-key friendly; configurable via `ui.snapshot_hotkey`) |

If the snapshot combination is already taken by another app, GrimoireAssist falls back to an
in-app shortcut and says so in the status bar — pick a different `ui.snapshot_hotkey` in that case.

## How it works

A single **capture thread** owns the physical device and fans frames out to (a) the virtual-camera
sink (clean), (b) a one-slot buffer read by the OCR worker, and (c) the rolling review recorder,
which hands frames to its own encoder thread so the disk never stalls capture. The clean feed always
goes to the virtual camera; the window shows only the detection navbar and the embedded info page.

```
grimoireassist/
  __main__.py      entry / CLI / single-instance guard / dark theme / app icon / game-select
  config.py        YAML config (global) + per-game GameSettings (settings.json) + logging toggle
  capture.py       device owner + frame fan-out + named-device enumeration
  virtualcam.py    OBS Virtual Camera sink (clean feed)
  reviewbuffer.py  rolling 30-min DVR: JPEG segment files + pinned snapshots + clip export
  hotkey.py        system-wide hotkey (Win32 RegisterHotKey via Qt native event filter)
  games.py         game catalog (GameInfo) loader + import-data helpers + app icon
  ocr/             OcrEngine + EasyOCR/Tesseract impls (confidence) + preprocessing + level helpers
  battle.py        MonsterTracker (persistent + confidence) + match_known + OcrWorker (QThread)
  overlay.py       OverlayModel (UI state)
  data/            app icon (icon.ico)
  ui/              main window (☰ menu, pills, view switch), monster panel (web views),
                   monster cards, calibration dialog, game-select & add-game dialogs,
                   import wizard, input preview (PiP), review screen (playback + scrub),
                   controller button map (overlay + pad layout data)

# Runtime data (next to config.yaml, created/edited at use):
games/
  games.json            game catalog
  <id>/settings.json    per-game regions + keywords + persist overrides
  <id>/import/          imported monster data.json + images/
logs/                   OCR debug logs (only when ☰ → Log to file is on)
snapshots/              frame snapshots saved by the snapshot hotkey (☰ → Snapshot frame)
buffer/                 rolling review buffer (a cache — wiped on exit and on the next start)
recordings/             clips saved from the review screen (these are yours; nothing deletes them)
```

## Tests

```bat
.venv\Scripts\activate
pip install pytest
pytest
```

Covers the persistent tracker (retention, confidence stickiness, UI-text rejection), the URL
builder (path/search styles, multi-monster encoding, custom joiner), the game catalog/config,
config round-tripping, the rolling review buffer (fps decimation, time/size eviction, segment
cleanup, snapshot pinning, clip export + cancel) and the review screen (playback clock at each
speed, scrubbing, save, close) — no camera required. The review-screen tests drive Qt's
**offscreen** platform, so they need no display either.
