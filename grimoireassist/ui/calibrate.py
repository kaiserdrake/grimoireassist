"""Region calibration dialog: draw / move / resize monster-name regions on a frozen frame."""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QVBoxLayout,
)

from ..config import Config, Region

_HANDLE = 9  # px hit radius for corner handles (widget space)


class _Canvas(QLabel):
    def __init__(self, frame_bgr: np.ndarray, parent=None) -> None:
        super().__init__(parent)
        h, w = frame_bgr.shape[:2]
        self.frame_w, self.frame_h = w, h
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        self._pixmap = QPixmap.fromImage(
            QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())
        self.regions: dict[str, Region] = {}
        self._active_key: Optional[str] = None

        # interaction state
        self._mode: Optional[str] = None   # 'draw' | 'move' | 'resize'
        self._corner: Optional[str] = None  # 'tl' | 'tr' | 'bl' | 'br'
        self._move_off: Tuple[int, int] = (0, 0)
        self._draw_start: Optional[QPoint] = None  # frame coords
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)

    def set_active(self, key: str) -> None:
        self._active_key = key
        self.update()

    def set_regions(self, regions: dict[str, Region]) -> None:
        self.regions = regions
        self.update()

    # -- coordinate mapping ---------------------------------------------
    def _display_rect(self) -> QRect:
        scaled = self._pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QRect(x, y, scaled.width(), scaled.height())

    def _to_frame(self, pt: QPoint) -> QPoint:
        dr = self._display_rect()
        fx = (pt.x() - dr.x()) / max(1, dr.width()) * self.frame_w
        fy = (pt.y() - dr.y()) / max(1, dr.height()) * self.frame_h
        return QPoint(int(np.clip(fx, 0, self.frame_w)),
                      int(np.clip(fy, 0, self.frame_h)))

    def _to_widget(self, region: Region) -> QRect:
        dr = self._display_rect()
        sx = dr.width() / max(1, self.frame_w)
        sy = dr.height() / max(1, self.frame_h)
        return QRect(int(dr.x() + region.x * sx), int(dr.y() + region.y * sy),
                     int(region.w * sx), int(region.h * sy))

    def _corners(self, r: QRect) -> dict:
        return {"tl": r.topLeft(), "tr": r.topRight(),
                "bl": r.bottomLeft(), "br": r.bottomRight()}

    def _hit_corner(self, pos: QPoint, region: Region) -> Optional[str]:
        wr = self._to_widget(region)
        for name, c in self._corners(wr).items():
            if abs(pos.x() - c.x()) <= _HANDLE and abs(pos.y() - c.y()) <= _HANDLE:
                return name
        return None

    # -- mouse -----------------------------------------------------------
    def mousePressEvent(self, e):
        if not self._active_key:
            return
        pos = e.position().toPoint()
        region = self.regions.get(self._active_key, Region())
        if region.is_set():
            corner = self._hit_corner(pos, region)
            if corner:
                self._mode, self._corner = "resize", corner
                return
            if self._to_widget(region).contains(pos):
                fp = self._to_frame(pos)
                self._mode = "move"
                self._move_off = (fp.x() - region.x, fp.y() - region.y)
                return
        # otherwise start drawing a fresh rectangle
        self._mode = "draw"
        self._draw_start = self._to_frame(pos)
        self.regions[self._active_key] = Region(self._draw_start.x(),
                                                self._draw_start.y(), 0, 0)
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        # cursor feedback when hovering a corner
        if self._mode is None and self._active_key:
            region = self.regions.get(self._active_key)
            if region and region.is_set() and self._hit_corner(pos, region):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if self._mode is None or not self._active_key:
            return
        fp = self._to_frame(pos)
        region = self.regions[self._active_key]

        if self._mode == "draw" and self._draw_start is not None:
            x0, y0 = self._draw_start.x(), self._draw_start.y()
            region.x, region.y = min(x0, fp.x()), min(y0, fp.y())
            region.w, region.h = abs(fp.x() - x0), abs(fp.y() - y0)
        elif self._mode == "move":
            region.x = int(np.clip(fp.x() - self._move_off[0], 0, self.frame_w - region.w))
            region.y = int(np.clip(fp.y() - self._move_off[1], 0, self.frame_h - region.h))
        elif self._mode == "resize":
            self._apply_resize(region, fp)
        self.update()

    def _apply_resize(self, region: Region, fp: QPoint) -> None:
        # fixed = opposite corner; move the dragged corner to fp
        left, top = region.x, region.y
        right, bottom = region.x + region.w, region.y + region.h
        if "l" in self._corner:
            left = fp.x()
        if "r" in self._corner:
            right = fp.x()
        if "t" in self._corner:
            top = fp.y()
        if "b" in self._corner:
            bottom = fp.y()
        x0, x1 = sorted((left, right))
        y0, y1 = sorted((top, bottom))
        region.x, region.y = int(x0), int(y0)
        region.w, region.h = max(4, int(x1 - x0)), max(4, int(y1 - y0))

    def mouseReleaseEvent(self, e):
        if self._mode == "draw":
            region = self.regions.get(self._active_key)
            if region and (region.w < 5 or region.h < 5):
                self.regions[self._active_key] = Region()  # discard tiny drags
        self._mode = self._corner = self._draw_start = None
        self.update()

    # -- paint -----------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0c0c10"))
        p.drawPixmap(self._display_rect(), self._pixmap)
        p.setFont(QFont("Segoe UI", 9))
        for key, region in self.regions.items():
            if not region.is_set():
                continue
            r = self._to_widget(region)
            active = key == self._active_key
            p.setPen(QColor("#ffd479") if active else QColor("#5a8cff"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.fillRect(QRect(r.x(), r.y() - 16, max(40, len(key) * 7), 16),
                       QColor(0, 0, 0, 160))
            p.drawText(r.x() + 3, r.y() - 4, key)
            if active:  # draw resize handles
                p.setBrush(QColor("#ffd479"))
                for c in self._corners(r).values():
                    p.drawRect(c.x() - 4, c.y() - 4, 8, 8)
        p.end()


class CalibrateDialog(QDialog):
    """Define one or more monster-name regions over a frozen frame (drag to draw,
    drag the body to move, drag a corner handle to resize)."""

    def __init__(self, cfg: Config, frame_bgr: np.ndarray, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrate monster regions")
        self.cfg = cfg
        self.resize(1000, 640)

        self.canvas = _Canvas(frame_bgr)
        regions: dict[str, Region] = {}
        for i, r in enumerate(cfg.ocr.regions_monster_names):
            regions[f"monster_{i+1}"] = Region(**vars(r))
        if not any(k.startswith("monster_") for k in regions):
            regions["monster_1"] = Region()
        # optional Battle-End trigger region (e.g. over the "Result" text)
        regions["battle_end"] = Region(**vars(cfg.ocr.regions_battle_end))
        self.canvas.set_regions(regions)

        self.region_picker = QComboBox()
        self.region_picker.addItems(list(regions.keys()))
        self.region_picker.currentTextChanged.connect(self.canvas.set_active)
        self.canvas.set_active(self.region_picker.currentText())

        self.slot_count = QSpinBox()
        self.slot_count.setMinimum(1)
        self.slot_count.setMaximum(8)
        self.slot_count.setValue(max(1, len(cfg.ocr.regions_monster_names)))
        self.slot_count.valueChanged.connect(self._resize_slots)

        save_btn = QPushButton("Save to config")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(QLabel("Region:"))
        top.addWidget(self.region_picker)
        top.addSpacing(16)
        top.addWidget(QLabel("Monster regions:"))
        top.addWidget(self.slot_count)
        top.addStretch(1)
        top.addWidget(QLabel("drag to draw / move / resize"))

        # Battle-End trigger: the region (drawn above as 'battle_end') + the text to match.
        end_row = QHBoxLayout()
        end_row.addWidget(QLabel("Battle-End text (comma-separated):"))
        self.end_text_edit = QLineEdit(", ".join(cfg.ocr.keywords_battle_end))
        self.end_text_edit.setPlaceholderText("e.g. Result, Victory, Defeat")
        end_row.addWidget(self.end_text_edit, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(cancel_btn)
        bottom.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(end_row)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(bottom)

    def _resize_slots(self, n: int) -> None:
        regions = dict(self.canvas.regions)
        for key in [k for k in regions if k.startswith("monster_")]:
            regions.pop(key)
        for i in range(n):
            regions[f"monster_{i+1}"] = Region()
        # keep existing geometry where we can
        existing = [v for k, v in sorted(self.canvas.regions.items())
                    if k.startswith("monster_")]
        for i, r in enumerate(existing[:n]):
            regions[f"monster_{i+1}"] = r
        self.canvas.set_regions(regions)
        self.region_picker.clear()
        self.region_picker.addItems([k for k in regions])

    def _save(self) -> None:
        regions = self.canvas.regions
        slots: List[Region] = []
        i = 1
        while f"monster_{i}" in regions:
            slots.append(regions[f"monster_{i}"])
            i += 1
        # update the active regions; the caller persists them under the current game
        self.cfg.ocr.regions_monster_names = slots or [Region()]
        self.cfg.ocr.regions_battle_end = regions.get("battle_end", Region())
        self.cfg.ocr.keywords_battle_end = [
            s.strip() for s in self.end_text_edit.text().split(",") if s.strip()
        ]
        self.accept()
