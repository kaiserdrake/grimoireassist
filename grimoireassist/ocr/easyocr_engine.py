"""EasyOCR-backed engine. Lazily constructs the (heavy) reader on first use."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import numpy as np

from .engine import OcrEngine, preprocess


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
            return reader.readtext(preprocess(image), detail=0, paragraph=True,
                                   canvas_size=self._CANVAS, mag_ratio=self._MAG)
        results = self._submit(_run)
        return " ".join(results).strip()

    def read_lines(self, image: np.ndarray) -> list:
        """Return [(text, confidence), ...] — one per detected block."""
        if image is None or image.size == 0:
            return []
        def _run():
            reader = self._ensure_reader()
            return reader.readtext(preprocess(image), detail=1, paragraph=False,
                                   canvas_size=self._CANVAS, mag_ratio=self._MAG)
        results = self._submit(_run)
        out = []
        for item in results:
            text = (item[1] or "").strip()
            conf = float(item[2]) if len(item) > 2 else 1.0
            if text:
                out.append((text, conf))
        return out
