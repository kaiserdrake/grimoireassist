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
- Optional: an **internet connection** for the online narration voice — dialogue narration falls
  back to the built-in offline Windows voice without one.

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
   - **`dialogue`** *(optional)* — the NPC / subtitle text box. Its prose is read as whole
     sentences and narrated aloud. See [Dialogue narration](#dialogue-narration).
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
- **Speech** — see [Dialogue narration](#dialogue-narration).
  - **Speak dialogue** — turn the whole dialogue pipeline on. **Off by default**; while off no
    dialogue OCR runs at all.
  - **Mute narration** — silence the voice while still detecting, logging and displaying the text.
  - **Show dialogue log** — the running transcript strip above the monster cards (newest line
    first, chevron folds it away).
  - **Show test line box** — the **Test line** field inside that strip, for narrating typed text
    without a capture source. **On by default**; turn it off to keep the strip to the transcript
    alone (`ui.dialogue_test_box`).
  - **Voice source ▸** — *Automatic* (online, offline fallback), *Online neural voice*, or
    *Offline Windows voice*.
  - **Voice ▸** — the voices available from whichever source is active. Filled when you open the
    submenu, because listing the online voices is a network call.
  - **Voice speed ▸** — *Slower* … *Faster*.
  - **Test voice** — speaks one canned sentence. (Stopping mid-line lives on the dialogue
    strip's **■ Stop** button, next to the transcript it interrupts.)
- **Game**
  - **Add game…** — add a new game to the catalog (id, name, info-URL template, options).
  - **Switch game…** — reopen the game picker.
  - **Import monster data…** — fetch the monster list + info from your Grimoire notes (see below).
- **Browser** (the 🌐 drawer)
  - **Search engine ▸** — engine used when the address bar gets a query rather than a URL.
  - **Grimoire user…** — the Grimoire account whose **focus README** supplies the drawer's
    bookmarks. New tabs list every link under that note's `# Bookmarks` heading; ★ re-syncs.
    Stored as `grimoire.user` in `config.yaml`.
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

Like the input preview, it **rides above whichever view is showing** — tracking or Grimoire. Only
the review screen displaces it (that one owns the whole pane); it comes back on its own when you
close it.

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

## Dialogue narration

Reads the game's dialogue box aloud, so side-character chatter and NPC asides land without you
stopping to read them mid-fight. Unlike monster detection — which matches short names against a
known list — this region is treated as **prose**: the OCR lines are glued back into whole
sentences and only spoken once the sentence is finished.

**Setup:** press **F9**, draw the **`dialogue`** region over the text box, Save. Then
**☰ → Speech → Speak dialogue**. It is off by default, and while off no dialogue OCR runs at all,
so the feature costs nothing when unused.

### How a statement is decided

Dialogue types out one character at a time, so the same box reads differently on every poll.

- The text must stop changing for **`stable_frames`** polls (default **2**) before it counts as a
  finished statement — raise it if half-written sentences get spoken, lower it for snappier delivery.
- When a settled statement merely **extends** the previous one, only the new tail is spoken, so a
  long speech is narrated once, in order, instead of restarting from the top each time another
  clause appears.
- A box that blinks away and returns with the same line is not re-read within **`repeat_window_s`**
  (default **25s**).
- Decoration the OCR reads as text — the blinking ▼ "continue" arrow, button glyphs, box borders —
  is stripped before speaking, and reads too short or too garbled to be prose (**`min_chars`**) are
  dropped.

### Voices

| Source | Needs | Sounds like |
|---|---|---|
| **Online neural voice** (default) | Internet + `edge-tts` | Microsoft's "Online Natural" voices — `en-GB-RyanNeural`, `en-GB-SoniaNeural` and ~20 more |
| **Offline Windows voice** | Nothing | Whatever SAPI voices are installed (typically *Microsoft David* / *Zira*) |

Both lists are filtered to **US and UK English** — other locales read game dialogue with the
wrong vowels and pacing. If a machine has no US/UK voice installed at all, the offline list
falls back to every installed voice rather than going silent.

On the default **Automatic** setting the online voice is used whenever it works. If synthesis
fails — no network, endpoint unreachable — **the same statement is immediately spoken by the
offline voice rather than being lost**, the source is noted in the status bar and the dialogue
log, and the network is re-probed every `speech.edge_retry_s` (default 60s) so the good voice
returns on its own. Pin either source explicitly via **☰ → Speech → Voice source**.

> **The online voice sends text off your machine.** Each dialogue line is posted to a Microsoft
> speech endpoint to be synthesised. If that isn't acceptable, set the voice source to
> **Offline Windows voice** (or `speech.backend: sapi`) — everything then stays local.

Because synthesis is a network round-trip (~0.5–1.5s), the next queued statement is synthesised
while the current one is still playing, which hides most of that delay.

**A note on Narrator "natural" voices:** voices installed through *Settings → Accessibility →
Narrator → Add natural voices* (Sonia, Ryan and friends) are **not** usable by other applications
— they appear in neither the SAPI voice list nor the WinRT synthesiser, only inside Narrator.
That is why the good voices here come over the network instead. To improve the *offline* fallback,
install proper SAPI voices (e.g. the Microsoft Speech Platform runtime voices); any US or UK
English voice that shows up under Windows Speech settings appears in the **Voice** submenu.

### Trying it without a game running

Three ways to test narration with fixed input rather than a live capture card:

| Want to | Do this |
|---|---|
| Hear a **line again** | Click it in the transcript, then **↻ Replay** (or just double-click it). |
| Hear a **specific line** | **☰ → Speech → Show dialogue log**, type into **Test line** at the bottom of the strip, press Enter. Hide the box with **Show test line box** when you no longer want it. The text goes through the same cleaning a real read gets — paste in a line with its ▼ arrow and watch it drop out — then it is logged, displayed and spoken. Needs no capture source and no calibrated region. |
| Check the **voice** only | **☰ → Speech → Test voice** — speaks one canned sentence. |
| Exercise the **whole pipeline** (OCR included) | `python -m grimoireassist --video samples\clip.mp4` — runs against a recorded clip, so region calibration, statement settling and narration all work exactly as they would live. |

A line the reader considers too fragmentary to be prose is refused with the reason in the status
bar and left in the box so you can edit it, rather than being narrated as noise.

### The dialogue log

**☰ → Speech → Show dialogue log** docks a running transcript **above** the monster cards, in
the tracking view's own colours. The **newest line sits at the top**, so the line just spoken is
always against the top edge — glanceable mid-fight without chasing a scrollbar.

Each entry is banded against its neighbours so adjacent lines never run together, and carries a
`[HH:MM:SS]` timestamp set in a smaller, dimmer type than the prose it belongs to. The banding
follows the entry itself, so a line keeps its shade as newer statements push it down.

**Click any line to pick it** — it highlights — then **↻ Replay** speaks it again; double-clicking
a line replays it in one go. Clicking the picked line again unpicks it. Handy when a line was
drowned out by a fight, or you want to hear an NPC's hint a second time. Replay honours Mute: with
narration muted it says so in the status bar rather than silently doing nothing.

**Drag the grip along the bottom edge** to resize the box (56–600px); the height is written to
`ui.dialogue_height` on release, so it survives restarts.

Its header carries **■ Stop** (cut the line being spoken and drop the queue), **↻ Replay**, the
**🔊 / 🔇 Mute** toggle, **Clear**, and a **▲ / ▼ chevron** that folds the strip down to just that
header (the same gesture as the controller map overlay; remembered as `ui.dialogue_collapsed`).

Mute is deliberately *not* the same switch as *Speak dialogue*:

| | dialogue OCR | log + panel | audio |
|---|---|---|---|
| *Speak dialogue* off | no | no | no |
| *Speak dialogue* on, **muted** | yes | yes | no |
| *Speak dialogue* on, unmuted | yes | yes | yes |

Muting also cuts the line currently being spoken rather than letting it finish. Statements are
additionally written to the OCR debug log tagged `[dialogue · <source>]`, so you can see which
voice actually spoke each line.

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
  show_controller_map: false       # controller button map over the main pane (🎮)
  controller_map_left: playstation # playstation | switch | xbox | steamdeck
  controller_map_right: switch
  controller_map_width: 420        # overlay width in px; drag its corner grip
  controller_map_x: 0.5            # position as a 0–1 share of the free space,
  controller_map_y: 0.08           # so a resized window keeps it in proportion
  controller_map_collapsed: false  # folded down to the header pill
  dialogue_collapsed: false        # dialogue strip folded down to its header
  dialogue_test_box: true          # the type-a-line box inside the dialogue log
  dialogue_height: 132             # transcript height in px; drag its bottom grip
review:                            # rolling capture buffer behind the review screen
  enabled: true
  minutes: 30.0                    # length of the rolling window
  fps: 15.0                        # frames kept per second (the source fps is the cap)
  max_height: 720                  # downscale taller sources to this (0 = keep as captured)
  jpeg_quality: 75                 # 30–95; higher is bigger on disk
  max_disk_mb: 4096                # hard ceiling on buffer/ — whichever budget binds first wins
speech:                            # dialogue narration (see Dialogue narration)
  enabled: false                   # off = no dialogue OCR at all
  muted: false                     # detect + log, but stay silent
  backend: auto                    # auto (online, offline fallback) | edge | sapi
  voice_online: en-GB-RyanNeural   # edge-tts voice name
  voice_offline: ''                # SAPI voice, matched as a substring; '' = auto-pick
  rate: 0                          # -10 (slow) … 10 (fast)
  volume: 90                       # 0–100
  stable_frames: 2                 # identical reads before a statement counts as finished
  min_chars: 6                     # shorter reads are treated as OCR fragments
  repeat_window_s: 25.0            # don't re-read the same statement within this
  max_queue: 3                     # statements buffered when speech falls behind
  edge_retry_s: 60.0               # how long to stay offline before re-probing the network
grimoire:                          # source of the browser drawer's bookmarks
  base_url: https://grimoire.laeradsphere.com
  user: ''                         # ☰ → Browser → Grimoire user…; blank = no bookmarks
logging: { to_file: false }        # write the OCR debug log to logs/ (☰ → Log to file)
```

Bookmarks are fetched from `<base_url>/api/focus/readme/raw?user=<user>` and parsed out of the
returned markdown: every list item under the `# Bookmarks` heading becomes a link on the browser's
new-tab page.

```markdown
# Bookmarks

* [Monster Tier List](https://example.com/tiers)
* [Best Builds](https://example.com/builds)
```

**Per-game settings** live in `games/<id>/settings.json` (written by calibration / Add Game), not
in `config.yaml`:

```json
{
  "regions": {
    "monster_names": [ {"x":0,"y":0,"w":0,"h":0} ],
    "battle_end": {"x":0,"y":0,"w":0,"h":0},
    "dialogue": {"x":0,"y":0,"w":0,"h":0}
  },
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
  dialogue.py      dialogue-region prose: sentence assembly + when a statement is finished
  speech.py        text-to-speech: online neural + offline SAPI backends, queue, auto-fallback
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
cache/tts/              scratch MP3s for the online narration voice (deleted after playback)
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
cleanup, snapshot pinning, clip export + cancel), the review screen (playback clock at each
speed, scrubbing, save, close), dialogue statement assembly (reading order, typed-out lines,
blink suppression, noise stripping) and narration voice selection + the online→offline
fallback — no camera required. The review-screen tests drive Qt's **offscreen** platform, so
they need no display either; the speech tests use fake backends, so they make no sound and
need no network.
