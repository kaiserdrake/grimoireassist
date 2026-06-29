"""EasyOCR-backed engine. Lazily constructs the (heavy) reader on first use."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import numpy as np

from .engine import OcrEngine, preprocess


def _merge_adjacent_boxes(boxes: list) -> list:
    """Group word/phrase boxes into lines, joining horizontally-adjacent ones.

    `boxes` is a list of dicts with text/conf/left/right/ycenter/height. Boxes
    on the same line (vertical centres within ~0.6 of text height) that are close
    horizontally (gap < ~1.5 of text height) are concatenated left-to-right into
    one string; the merged confidence is the mean of its parts. Returns
    [(text, confidence), ...]. A wide horizontal gap starts a new entry, so
    separated UI text or a different monster's name is never absorbed."""
    if not boxes:
        return []
    # Order top-to-bottom, then left-to-right.
    boxes = sorted(boxes, key=lambda b: (b["ycenter"], b["left"]))
    out = []
    cur_text: list = []
    cur_confs: list = []
    cur_y = cur_right = cur_h = None
    for b in boxes:
        h = b["height"] or 1.0
        same_line = (cur_y is not None
                     and abs(b["ycenter"] - cur_y) <= 0.6 * max(h, cur_h or h))
        adjacent = (cur_right is not None
                    and (b["left"] - cur_right) <= 1.5 * max(h, cur_h or h))
        if same_line and adjacent:
            cur_text.append(b["text"])
            cur_confs.append(b["conf"])
            cur_right = max(cur_right, b["right"])
            cur_y = (cur_y + b["ycenter"]) / 2.0
            cur_h = max(cur_h or h, h)
        else:
            if cur_text:
                out.append((" ".join(cur_text), sum(cur_confs) / len(cur_confs)))
            cur_text, cur_confs = [b["text"]], [b["conf"]]
            cur_y, cur_right, cur_h = b["ycenter"], b["right"], h
    if cur_text:
        out.append((" ".join(cur_text), sum(cur_confs) / len(cur_confs)))
    return out


class EasyOcrEngine(OcrEngine):
    def __init__(self, languages: List[str], gpu: bool = False) -> None:
        self.languages = languages or ["en"]
        self.gpu = gpu
        self._reader = None       # type: Optional[object]
        self.gpu_warning: Optional[str] = None
        self.ready = False
        # All EasyOCR / torch calls run on this single plain Python thread.
        # torch's MKL/OpenBLAS DLLs fail per-thread initialisation inside a
        # QThread (WinError 1114); a ThreadPoolExecutor worker has no such
        # Qt-imposed restrictions.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="easyocr")

    # ------------------------------------------------------------------ warmup
    def warmup(self) -> None:
        """Load the model on the pool thread so the QThread never touches torch."""
        self._pool.submit(self._ensure_reader).result()

    def _ensure_reader(self):
        if self._reader is not None:
            return self._reader
        import easyocr
        try:
            import torch
            torch.set_num_threads(2)
            torch.set_num_interop_threads(1)
        except Exception:
            pass
        self._reader = easyocr.Reader(self.languages, gpu=self.gpu, verbose=False)
        self.ready = True
        if self.gpu:
            try:
                import torch
                if not torch.cuda.is_available():
                    self.gpu_warning = "CUDA not available — OCR running on CPU"
            except Exception:
                pass
        return self._reader

    # ------------------------------------------------------------------ inference
    _CANVAS = 1280
    _MAG = 1.0

    def _submit(self, fn, *args, **kwargs):
        """Run fn on the pool thread and return its result (blocks caller)."""
        return self._pool.submit(fn, *args, **kwargs).result(timeout=15)

    def read_text(self, image: np.ndarray) -> str:
        if image is None or image.size == 0:
            return ""
        def _run():
            reader = self._ensure_reader()
            return reader.readtext(preprocess(image, binarize=False), detail=0,
                                   paragraph=True, canvas_size=self._CANVAS,
                                   mag_ratio=self._MAG)
        results = self._submit(_run)
        return " ".join(results).strip()

    def read_lines(self, image: np.ndarray) -> list:
        """Return [(text, confidence), ...] — one per detected line.

        EasyOCR returns one box per word/phrase. A single monster name can be
        split across boxes (e.g. "Ivory" + "Lagiacrus"); matched independently,
        the "Lagiacrus" fragment would resolve to the *separate* monster of that
        name. We merge boxes that sit on the same line AND are horizontally
        adjacent, so a split name is rejoined while spatially-separate UI text
        (or a genuinely different monster elsewhere on screen) stays distinct."""
        if image is None or image.size == 0:
            return []
        def _run():
            reader = self._ensure_reader()
            return reader.readtext(preprocess(image, binarize=False), detail=1,
                                   paragraph=False, canvas_size=self._CANVAS,
                                   mag_ratio=self._MAG)
        results = self._submit(_run)
        boxes = []
        for item in results:
            text = (item[1] or "").strip()
            if not text:
                continue
            conf = float(item[2]) if len(item) > 2 else 1.0
            xs = [p[0] for p in item[0]]
            ys = [p[1] for p in item[0]]
            boxes.append({
                "text": text, "conf": conf,
                "left": min(xs), "right": max(xs),
                "ycenter": (min(ys) + max(ys)) / 2.0,
                "height": max(ys) - min(ys),
            })
        return _merge_adjacent_boxes(boxes)
