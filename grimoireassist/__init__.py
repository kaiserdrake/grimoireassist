"""GrimoireAssist — capture-card battle assistant with virtual camera, OCR, and wiki overlays."""
from pathlib import Path
import sys

__version__ = "1.0.0"


def app_root() -> Path:
    """Directory that anchors runtime data (config.yaml, games/, logs/, snapshots/).

    Frozen (PyInstaller) build: the folder containing GrimoireAssist.exe, so the
    portable folder carries its own data regardless of the working directory.
    Source checkout: the repo root (parent of this package), matching run.bat.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
