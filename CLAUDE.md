# CLAUDE.md

Operational notes for Claude Code working in this repo. GrimoireAssist is a
Windows PyQt6 capture-card battle assistant (virtual camera + OCR + web
overlays). Python package lives in `grimoireassist/`; tests in `tests/`
(`pytest`).

## Version mechanics (build & release)

**Single source of truth:** `grimoireassist/__init__.py` → `__version__ = "X.Y.Z"`.
Everything downstream (the exe, the archive name, the git tag, the GitHub
release) derives from this one string. Format is plain semver `X.Y.Z`.

### `build.bat [X.Y.Z]`

- **With a version argument** (`build.bat 1.0.2`): validates it is `X.Y.Z`,
  then **stamps** `__version__` into `grimoireassist/__init__.py` before
  building. This is how the build script "controls" the version.
- **With no argument** (`build.bat`): builds at the current `__version__`,
  unchanged.
- It does **not** auto-commit the version bump — commit it yourself.
- Output: `dist\GrimoireAssist\GrimoireAssist.exe` (one-folder) and
  `dist\GrimoireAssist-vX.Y.Z-win64.7z` (the release asset). The archive name
  reflects whatever `__version__` is at build time.
- Uses a dedicated clean `.venv-build`; first run installs CUDA torch + deps
  and takes several minutes. Requires 7-Zip installed.

### `release.bat`

- Releases the **newest existing** `dist\GrimoireAssist-v*-win64.7z` and
  derives the version/tag **from that filename** — it does not re-read
  `__version__` and does not rebuild. So a build already in `dist\` is released
  as-is.
- Tags `vX.Y.Z`, pushes the tag, and runs `gh release create` with the `.7z`
  attached (`--generate-notes`). Refuses to overwrite an existing release.
- Assumes the version-bump commit is already committed and pushed.
- Requires the GitHub CLI (`gh`) installed and authenticated (`gh auth login`).

### Standard flow to build + release version X.Y.Z

1. `build.bat X.Y.Z`  — stamps `__version__` and builds the archive.
2. `git add -A && git commit` the version bump (and any code), then
   `git push origin main`.
3. Smoke-test `dist\GrimoireAssist\GrimoireAssist.exe`.
4. `release.bat` — tags, pushes the tag, publishes the GitHub release.

### Gotchas

- **Build before release.** Because `release.bat` takes the version from the
  newest archive, a hand-edited `__version__` with no rebuild means the release
  uses the *older* archive's version. Running `build.bat X.Y.Z` keeps source
  and artifact in step.
- **`gh` is required for release** and is not always installed on the build
  machine; if `gh` is missing, everything up to the archive can be prepared but
  the publish must wait until `gh` is installed and authenticated.
- These are Windows `.bat` scripts — invoke through `cmd /c`. When launching
  from a wrapper whose working directory may differ, call them by **absolute
  path** (`cmd /c "C:\...\build.bat"`); the scripts `cd /d "%~dp0"` themselves.
