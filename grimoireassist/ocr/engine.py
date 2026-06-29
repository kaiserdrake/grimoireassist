"""OCR engine interface + shared preprocessing."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import cv2
import numpy as np

# OpenCV lazily loads IPP and OpenCL DLLs on the first call from each thread.
# On Windows those DLLs fail to initialise inside a QThread (ERROR_DLL_INIT_FAILED
# / WinError 1114).  Disabling both acceleration paths here — at import time, in
# the main thread — prevents cv2 from ever attempting those lazy loads.
try:
    cv2.setUseOptimized(False)   # disables IPP / MKL paths
    cv2.ocl.setUseOpenCL(False)  # disables OpenCL
    # Prime cv2 with a trivial operation so all its DLLs are loaded now,
    # in the main thread, before any QThread calls cv2 for the first time.
    cv2.cvtColor(np.zeros((4, 4, 3), dtype=np.uint8), cv2.COLOR_BGR2GRAY)
except Exception:
    pass


# Keep OCR cheap WITHOUT hurting accuracy: cap the longest side (so a wide region
# isn't blown up to 4000px+), and only upscale genuinely small regions. We do NOT
# shrink text down to a tiny height — that costs detection accuracy and causes the
# OCR to intermittently miss a name that's clearly on screen.
_MAX_SIDE = 1280
_MIN_HEIGHT = 48


def preprocess(crop: np.ndarray, binarize: bool = True) -> np.ndarray:
    """Grayscale + size-normalise for OCR input.

    `binarize=True` (Tesseract) additionally applies an adaptive threshold —
    classic OCR reads clean black-on-white best. Neural engines (EasyOCR) are
    trained on natural grayscale/colour images and LOSE accuracy on binarised
    input: thresholding fragments stylised/outlined game fonts (e.g. white text
    with a black border), so they pass `binarize=False` and get the grayscale.
    """
    if crop is None or crop.size == 0:
        return crop
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    h, w = gray.shape[:2]
    scale = 1.0
    if max(w, h) > _MAX_SIDE:
        scale = _MAX_SIDE / max(w, h)      # shrink only oversized regions
    elif h < _MIN_HEIGHT:
        scale = _MIN_HEIGHT / h            # enlarge tiny text
    if abs(scale - 1.0) > 0.05:
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interp)
    if not binarize:
        return gray
    thr = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 12
    )
    return thr


# Confidence levels for detected text (EasyOCR confidence is 0..1).
CONFIDENCE_LEVELS = ("high", "mid", "low")
_LEVEL_FLOOR = {"high": 0.85, "mid": 0.60, "low": 0.0}


def conf_level(conf: float) -> str:
    """Bucket an OCR confidence into 'high' / 'mid' / 'low'."""
    if conf >= _LEVEL_FLOOR["high"]:
        return "high"
    if conf >= _LEVEL_FLOOR["mid"]:
        return "mid"
    return "low"


def level_floor(level: str) -> float:
    """Minimum confidence required for a given level (for filtering)."""
    return _LEVEL_FLOOR.get(level, 0.0)


class OcrEngine(ABC):
    ready: bool = True  # subclasses with lazy loading override this

    def warmup(self) -> None:
        """Pre-load the model (call once from a background thread at startup)."""

    @abstractmethod
    def read_text(self, image: np.ndarray) -> str:
        """Return concatenated recognised text for an already-cropped region."""
        raise NotImplementedError

    def read_lines(self, image: np.ndarray) -> List[tuple]:
        """Return [(text, confidence), ...] — one entry per detected line.

        Default splits read_text on newlines (confidence 1.0); engines that can
        return real per-detection confidence should override this.
        """
        text = self.read_text(image)
        return [(ln.strip(), 1.0) for ln in text.splitlines() if ln.strip()]


def build_engine(name: str, languages: List[str], gpu: bool = False) -> OcrEngine:
    """Construct the requested OCR engine, falling back automatically if unavailable.

    Priority when name == "auto" (or unrecognised):
      1. Tesseract  — instant startup, low CPU, good for clear game text
      2. EasyOCR   — higher accuracy but requires a 200 MB model download + PyTorch
    """
    name = (name or "auto").lower()

    if name in ("tesseract", "auto"):
        try:
            from .tesseract_engine import TesseractEngine
            return TesseractEngine(languages=languages)
        except Exception:
            if name == "tesseract":
                raise
            # fall through to EasyOCR

    if name in ("easyocr", "auto"):
        from .easyocr_engine import EasyOcrEngine
        return EasyOcrEngine(languages=languages, gpu=gpu)

    raise ValueError(f"Unknown OCR engine: {name!r}")
