"""OCR engine interface + shared preprocessing."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import cv2
import numpy as np


# Keep OCR cheap WITHOUT hurting accuracy: cap the longest side (so a wide region
# isn't blown up to 4000px+), and only upscale genuinely small regions. We do NOT
# shrink text down to a tiny height — that costs detection accuracy and causes the
# OCR to intermittently miss a name that's clearly on screen.
_MAX_SIDE = 1280
_MIN_HEIGHT = 48


def preprocess(crop: np.ndarray) -> np.ndarray:
    """Grayscale + size-normalise + threshold for readable, low-cost OCR input."""
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
    name = (name or "easyocr").lower()
    if name == "easyocr":
        from .easyocr_engine import EasyOcrEngine
        return EasyOcrEngine(languages=languages, gpu=gpu)
    if name == "tesseract":
        from .tesseract_engine import TesseractEngine
        return TesseractEngine(languages=languages)
    raise ValueError(f"Unknown OCR engine: {name}")
