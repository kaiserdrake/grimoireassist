"""Minimap tab: a live, enlarged view of the calibrated minimap region.

Display only — no OCR, no processing beyond the crop. It polls the frame buffer
only while it is visible, and skips frames it has already drawn.
"""
from __future__ import annotations

from typing import Callable, Optional

import cv2
import numpy as np
from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from ..capture import FrameBuffer
from ..config import Region

_NO_REGION = ("No minimap region yet.\n"
              "Press F9 (Calibrate) and draw the Minimap region over the game's minimap.")
_NO_FRAME = "Waiting for frames…"


class MinimapView(QWidget):
    """Draws the minimap region of the latest frame, scaled up to fit."""

    def __init__(self, buffer: FrameBuffer, region: Callable[[], Region],
                 fps: float = 15.0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._buffer = buffer
        self._region = region            # read live, so recalibration applies at once
        self._pixmap: Optional[QPixmap] = None
        self._last_seq = -1
        self._last_region: Optional[tuple] = None
        self.setMinimumSize(120, 120)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._timer = QTimer(self)
        self._timer.setInterval(round(1000 / min(max(fps, 1.0), 30.0)))
        self._timer.timeout.connect(self._tick)

    # ---- frames ---------------------------------------------------------
    def _tick(self) -> None:
        region = self._region()
        key = (region.x, region.y, region.w, region.h)
        if not region.is_set():
            if self._pixmap is not None or self._last_region != key:
                self._pixmap, self._last_region = None, key
                self.update()
            return
        if self._buffer.current_seq() == self._last_seq and key == self._last_region:
            return  # nothing new to draw
        frame, seq = self._buffer.get()
        if frame is None:
            return
        self._last_seq, self._last_region = seq, key
        crop = self.crop(frame, region)
        self._pixmap = None if crop is None else self._to_pixmap(crop)
        self.update()

    @staticmethod
    def crop(frame: np.ndarray, region: Region) -> Optional[np.ndarray]:
        """The region of the frame, clipped to it; None when nothing is left."""
        h, w = frame.shape[:2]
        x0, y0 = max(0, region.x), max(0, region.y)
        x1, y1 = min(w, region.x + region.w), min(h, region.y + region.h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return frame[y0:y1, x0:x1]

    @staticmethod
    def _to_pixmap(crop: np.ndarray) -> QPixmap:
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        return QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w,
                                        QImage.Format.Format_RGB888).copy())

    # ---- painting -------------------------------------------------------
    def target_rect(self) -> QRectF:
        """Where the minimap is drawn: as large as fits, aspect kept, centred."""
        if self._pixmap is None:
            return QRectF()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        w, h = pw * scale, ph * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#15151b"))
        if self._pixmap is None:
            p.setPen(QColor("#5a5a63"))
            text = _NO_REGION if not self._region().is_set() else _NO_FRAME
            p.drawText(self.rect().adjusted(20, 20, -20, -20),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(self.target_rect(), self._pixmap, QRectF(self._pixmap.rect()))

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._last_seq = -1  # force a fresh frame on the first tick
        self._timer.start()
        self._tick()

    def hideEvent(self, ev) -> None:
        super().hideEvent(ev)
        self._timer.stop()
