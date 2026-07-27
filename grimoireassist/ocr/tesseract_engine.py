"""Optional Tesseract engine (requires the tesseract binary + pytesseract)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import List

import numpy as np

from .engine import OcrEngine, preprocess, preprocess_scale


class TesseractEngine(OcrEngine):
    ready = True  # no lazy model loading; Tesseract starts instantly

    def __init__(self, languages: List[str]) -> None:
        import pytesseract
        # Validate the binary works now so build_engine can fall back to
        # EasyOCR if tesseract isn't installed or its DLLs are broken.
        pytesseract.get_tesseract_version()
        self._pt = pytesseract
        self.lang = "+".join(languages or ["eng"])
        # pytesseract calls tesseract.exe via subprocess.Popen.  When called
        # from a QThread, Qt's Win32 thread attributes (window-station / desktop
        # handle) prevent the child-process DLLs from initialising, raising
        # WinError 1114.  Running every pytesseract call on a single plain
        # Python ThreadPoolExecutor worker avoids QThread entirely.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tesseract")

    def _run(self, fn, *args, **kwargs):
        """Submit a callable to the thread-pool worker and block for the result."""
        return self._pool.submit(fn, *args, **kwargs).result(timeout=10)

    def read_text(self, image: np.ndarray) -> str:
        if image is None or image.size == 0:
            return ""
        return self._run(
            lambda: self._pt.image_to_string(preprocess(image), lang=self.lang)
        ).strip()

    def read_lines(self, image: np.ndarray) -> list:
        """Return [(text, confidence, (x, y, w, h))] using Tesseract's
        per-word data; boxes are in crop coordinates."""
        if image is None or image.size == 0:
            return []
        data = self._run(
            lambda: self._pt.image_to_data(
                preprocess(image), lang=self.lang,
                output_type=self._pt.Output.DICT,
            )
        )
        # Aggregate words into lines keyed by (block, par, line) and average confidence.
        from collections import defaultdict
        lines: dict = defaultdict(
            lambda: {"words": [], "confs": [], "l": [], "t": [], "r": [], "b": []})
        for i, word in enumerate(data["text"]):
            word = (word or "").strip()
            conf = int(data["conf"][i])
            if not word or conf < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            ln = lines[key]
            ln["words"].append(word)
            ln["confs"].append(conf)
            ln["l"].append(data["left"][i])
            ln["t"].append(data["top"][i])
            ln["r"].append(data["left"][i] + data["width"][i])
            ln["b"].append(data["top"][i] + data["height"][i])
        # Word geometry is in preprocess()ed-image space; map back to the crop.
        ih, iw = image.shape[:2]
        s = preprocess_scale(iw, ih)
        out = []
        for line in lines.values():
            text = " ".join(line["words"])
            conf = sum(line["confs"]) / len(line["confs"]) / 100.0  # 0–1
            if text:
                x, y = min(line["l"]), min(line["t"])
                box = (round(x / s), round(y / s),
                       round((max(line["r"]) - x) / s),
                       round((max(line["b"]) - y) / s))
                out.append((text, conf, box))
        return out
