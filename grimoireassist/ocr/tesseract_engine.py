"""Optional Tesseract engine (requires the tesseract binary + pytesseract)."""
from __future__ import annotations

from typing import List

import numpy as np

from .engine import OcrEngine, preprocess


class TesseractEngine(OcrEngine):
    def __init__(self, languages: List[str]) -> None:
        import pytesseract  # noqa: F401  (validate availability early)
        self._pt = pytesseract
        self.lang = "+".join(languages or ["eng"])

    def read_text(self, image: np.ndarray) -> str:
        if image is None or image.size == 0:
            return ""
        prepped = preprocess(image)
        return self._pt.image_to_string(prepped, lang=self.lang).strip()
