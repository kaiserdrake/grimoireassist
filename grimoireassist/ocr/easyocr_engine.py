"""EasyOCR-backed engine. Lazily constructs the (heavy) reader on first use."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .engine import OcrEngine, preprocess


class EasyOcrEngine(OcrEngine):
    def __init__(self, languages: List[str], gpu: bool = False) -> None:
        self.languages = languages or ["en"]
        self.gpu = gpu
        self._reader = None  # type: Optional[object]

    def _ensure_reader(self):
        if self._reader is None:
            import easyocr  # imported lazily; pulls in torch
            self._reader = easyocr.Reader(self.languages, gpu=self.gpu, verbose=False)
        return self._reader

    # Cap the detection-network input. EasyOCR's default canvas_size is 2560;
    # our regions are already small, so a smaller canvas cuts GPU/CPU work a lot.
    _CANVAS = 1280
    _MAG = 1.0

    def read_text(self, image: np.ndarray) -> str:
        if image is None or image.size == 0:
            return ""
        reader = self._ensure_reader()
        prepped = preprocess(image)
        results = reader.readtext(prepped, detail=0, paragraph=True,
                                  canvas_size=self._CANVAS, mag_ratio=self._MAG)
        return " ".join(results).strip()

    def read_lines(self, image: np.ndarray) -> list:
        """Return each detected text block separately (one per monster name)."""
        if image is None or image.size == 0:
            return []
        reader = self._ensure_reader()
        prepped = preprocess(image)
        results = reader.readtext(prepped, detail=0, paragraph=False,
                                  canvas_size=self._CANVAS, mag_ratio=self._MAG)
        return [r.strip() for r in results if r and r.strip()]
