"""OCR engine interface + shared preprocessing."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import cv2
import numpy as np


def preprocess(crop: np.ndarray, upscale: float = 2.5) -> np.ndarray:
    """Make stylized game-UI text easier to read: grayscale, upscale, threshold."""
    if crop is None or crop.size == 0:
        return crop
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    if upscale and upscale != 1.0:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale,
                          interpolation=cv2.INTER_CUBIC)
    gray = cv2.bilateralFilter(gray, 5, 50, 50)
    thr = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 12
    )
    return thr


class OcrEngine(ABC):
    @abstractmethod
    def read_text(self, image: np.ndarray) -> str:
        """Return concatenated recognised text for an already-cropped region."""
        raise NotImplementedError

    def read_lines(self, image: np.ndarray) -> List[str]:
        """Return individual text detections (e.g. one per monster name).

        Default splits read_text on newlines; engines that can return separate
        detections should override this.
        """
        text = self.read_text(image)
        return [ln.strip() for ln in text.splitlines() if ln.strip()]


def build_engine(name: str, languages: List[str], gpu: bool = False) -> OcrEngine:
    name = (name or "easyocr").lower()
    if name == "easyocr":
        from .easyocr_engine import EasyOcrEngine
        return EasyOcrEngine(languages=languages, gpu=gpu)
    if name == "tesseract":
        from .tesseract_engine import TesseractEngine
        return TesseractEngine(languages=languages)
    raise ValueError(f"Unknown OCR engine: {name}")
