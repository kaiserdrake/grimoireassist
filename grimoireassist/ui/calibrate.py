"""Region calibration dialog: draw / move / resize the OCR regions on a frozen frame."""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from ..config import Config, Region

_HANDLE = 9  # px hit radius for corner handles (widget space)

# Region kinds with several numbered slots; keys are "<kind>_<n>". The others
# (battle_end, dialogue) are a single slot keyed by the kind.
MONSTER = "monster"
LABELS = "labels"
MAX_MONSTERS = 3
MAX_LABELS = 4
_SINGLE_NAMES = {"battle_end": "Battle end", "dialogue": "Dialogue", "minimap": "Minimap"}
_MULTI_NAMES = {MONSTER: "Monster", LABELS: "Labels"}


def _kind(key: str) -> str:
    """'monster_2' → 'monster'; single kinds are their own key."""
    head, _, tail = key.rpartition("_")
    return head if head in _MULTI_NAMES and tail.isdigit() else key


def display_name(key: str) -> str:
    """'monster_2' → 'Monster 2', 'battle_end' → 'Battle end'."""
    kind = _kind(key)
    if kind in _MULTI_NAMES:
        return f"{_MULTI_NAMES[kind]} {key.rpartition('_')[2]}"
    return _SINGLE_NAMES.get(key, key)


class _Canvas(QLabel):
    changed = pyqtSignal()  # a draw / move / resize gesture finished

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
        gesture = self._mode is not None
        self._mode = self._corner = self._draw_start = None
        self.update()
        if gesture:
            self.changed.emit()

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
            name = display_name(key)
            tag_w = p.fontMetrics().horizontalAdvance(name) + 8
            p.fillRect(QRect(r.x(), r.y() - 16, max(40, tag_w), 16),
                       QColor(0, 0, 0, 160))
            p.drawText(r.x() + 3, r.y() - 4, name)
            if active:  # draw resize handles
                p.setBrush(QColor("#ffd479"))
                for c in self._corners(r).values():
                    p.drawRect(c.x() - 4, c.y() - 4, 8, 8)
        p.end()


class CalibrateDialog(QDialog):
    """Define the OCR regions over a frozen frame (drag to draw, drag the body to
    move, drag a corner handle to resize).

    The Region picker lists a fixed set of slots: `Monster 1-3` for names
    matched against the game's monster list, `Battle end` for the end-of-fight
    banner, `Dialogue` for the text box whose prose is read aloud, and
    `Labels 1-4` for map or menu areas whose labels are listed as copyable
    terms. Picking an unset slot and drawing on it adds that region; "Delete"
    clears the selected slot. Only drawn slots are saved."""

    def __init__(self, cfg: Config, frame_bgr: np.ndarray, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrate OCR regions")
        self.cfg = cfg
        self.resize(1000, 640)

        self.canvas = _Canvas(frame_bgr)
        self.canvas.changed.connect(self._refresh_picker)

        # Every slot, in picker order. A config with more regions than there
        # are slots keeps the first ones.
        mons = [Region(**vars(r)) for r in cfg.ocr.regions_monster_names
                if r.is_set()][:MAX_MONSTERS]
        labels = [Region(**vars(r)) for r in cfg.ocr.regions_labels
                  if r.is_set()][:MAX_LABELS]
        regions: dict[str, Region] = {}
        for i in range(MAX_MONSTERS):
            regions[f"{MONSTER}_{i + 1}"] = mons[i] if i < len(mons) else Region()
        regions["battle_end"] = Region(**vars(cfg.ocr.regions_battle_end))
        regions["dialogue"] = Region(**vars(cfg.ocr.regions_dialogue))
        for i in range(MAX_LABELS):
            regions[f"{LABELS}_{i + 1}"] = labels[i] if i < len(labels) else Region()
        regions["minimap"] = Region(**vars(cfg.ocr.regions_minimap))
        self.canvas.set_regions(regions)

        self.region_picker = QComboBox()
        self.region_picker.setMinimumWidth(170)
        for key in regions:
            self.region_picker.addItem(display_name(key), key)
        self.region_picker.currentIndexChanged.connect(self._on_pick)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setToolTip("Clear the selected region")
        self.delete_btn.clicked.connect(self._delete)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, activated=self._delete)

        save_btn = QPushButton("Save to config")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(QLabel("Region:"))
        top.addWidget(self.region_picker)
        top.addWidget(self.delete_btn)
        top.addStretch(1)
        top.addWidget(QLabel("pick a region, then drag to draw / move / resize"))

        # Battle-End trigger: the region (drawn above as 'Battle end') + the text to match.
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

        self._refresh_picker()

    # ---- the slot list ---------------------------------------------------
    def current_key(self) -> Optional[str]:
        return self.region_picker.currentData()

    def _refresh_picker(self) -> None:
        """Mark unset slots and sync the active slot and Delete button. The
        canvas swaps in a fresh Region on each draw, so read it from there."""
        for i in range(self.region_picker.count()):
            key = self.region_picker.itemData(i)
            region = self.canvas.regions.get(key, Region())
            self.region_picker.setItemText(
                i, display_name(key) + ("" if region.is_set() else " — not set"))
        self._on_pick()

    def _on_pick(self, *_args) -> None:
        key = self.current_key()
        if key:
            self.canvas.set_active(key)
        region = self.canvas.regions.get(key) if key else None
        self.delete_btn.setEnabled(region is not None and region.is_set())

    def _delete(self) -> None:
        key = self.current_key()
        if not key or not self.delete_btn.isEnabled():
            return
        self.canvas.regions[key] = Region()
        self.canvas.update()
        self._refresh_picker()

    def _save(self) -> None:
        r = self.canvas.regions
        mons = [v for k, v in r.items() if _kind(k) == MONSTER and v.is_set()]
        labels = [v for k, v in r.items() if _kind(k) == LABELS and v.is_set()]
        # update the active regions; the caller persists them under the current game
        self.cfg.ocr.regions_monster_names = mons or [Region()]
        self.cfg.ocr.regions_battle_end = r.get("battle_end", Region())
        self.cfg.ocr.regions_dialogue = r.get("dialogue", Region())
        self.cfg.ocr.regions_labels = labels
        self.cfg.ocr.regions_minimap = r.get("minimap", Region())
        self.cfg.ocr.keywords_battle_end = [
            s.strip() for s in self.end_text_edit.text().split(",") if s.strip()
        ]
        self.accept()
