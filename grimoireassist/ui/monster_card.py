"""Local monster info card — renders imported data without a web view."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

_CARD_BG   = "#1a1a24"
_SECT_BG   = "#2a2a36"
_TEXT_FG   = "#e8e8ec"
_DIM_FG    = "#6b6b75"
_KEY_FG    = "#9a9aa3"
_MAX_IMG_W = 64
_MAX_IMG_H = 64


def _px(path: str, base: Optional[Path]) -> Optional[QPixmap]:
    if not path or not base:
        return None
    full = base / path
    if not full.exists():
        return None
    px = QPixmap(str(full))
    return px if not px.isNull() else None


class MonsterCard(QWidget):
    """Card widget for one monster. Renders all imported sections instantly."""

    open_web = pyqtSignal(str)  # emits the monster name

    def __init__(self, name: str, sections: Optional[Dict[str, list]],
                 image_base: Optional[Path], parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self.setMinimumWidth(220)
        self.setStyleSheet(f"QWidget {{ background:{_CARD_BG}; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        self._build_header(root)
        if sections:
            self._build_sections(root, sections, image_base)
        else:
            lbl = QLabel("No local data — click Full page for details")
            lbl.setStyleSheet(f"color:{_DIM_FG}; font-size:11px;")
            root.addWidget(lbl)
        root.addStretch()

    def _build_header(self, lay: QVBoxLayout) -> None:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        lbl = QLabel(self._name)
        lbl.setStyleSheet(f"color:{_TEXT_FG}; font-size:15px; font-weight:700;")
        rl.addWidget(lbl, 1)
        btn = QPushButton("Full page →")
        btn.setStyleSheet(
            f"QPushButton {{ background:{_SECT_BG}; color:{_KEY_FG}; border:none;"
            " border-radius:4px; padding:3px 10px; font-size:11px; }"
            f"QPushButton:hover {{ color:{_TEXT_FG}; }}")
        btn.clicked.connect(lambda: self.open_web.emit(self._name))
        rl.addWidget(btn)
        lay.addWidget(row)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_SECT_BG};")
        lay.addWidget(sep)

    def _build_sections(self, lay: QVBoxLayout, sections: Dict[str, list],
                        image_base: Optional[Path]) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")
        inner = QWidget()
        inner.setStyleSheet("QWidget { background:transparent; }")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(6)

        # _root first (no label), then named sections
        order = (["_root"] if "_root" in sections else []) + [
            k for k in sections if not k.startswith("_")
        ]
        for key in order:
            rows = sections[key]
            if not rows:
                continue
            if key != "_root":
                sec_lbl = QLabel(key)
                sec_lbl.setStyleSheet(
                    f"background:{_SECT_BG}; color:{_TEXT_FG};"
                    " font-size:11px; font-weight:600; border-radius:3px;"
                    " padding:2px 6px;")
                il.addWidget(sec_lbl)
            for row in rows:
                il.addWidget(self._make_row(row, image_base))

        il.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

    def _make_row(self, row: list, image_base: Optional[Path]) -> QWidget:
        """One table row: list of cell dicts {text, imgs, img_paths}."""
        w = QWidget()
        w.setStyleSheet("QWidget { background:transparent; }")
        rl = QHBoxLayout(w)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(8)

        for i, cell in enumerate(row):
            text = cell.get("text", "")
            img_paths = cell.get("img_paths", [])
            is_key = (i == 0 and len(row) == 2)

            cell_w = QWidget()
            cell_w.setStyleSheet("QWidget { background:transparent; }")
            cl = QHBoxLayout(cell_w)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(4)

            # images first
            for img_path in img_paths:
                px = _px(img_path, image_base)
                if px:
                    img_lbl = QLabel()
                    img_lbl.setPixmap(
                        px.scaled(_MAX_IMG_W, _MAX_IMG_H,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
                    cl.addWidget(img_lbl)

            if text:
                style = (f"color:{_KEY_FG}; font-size:12px;" if is_key
                         else f"color:{_TEXT_FG}; font-size:12px;")
                txt_lbl = QLabel(text)
                txt_lbl.setStyleSheet(style)
                txt_lbl.setWordWrap(True)
                cl.addWidget(txt_lbl, 0 if img_paths else 1)

            rl.addWidget(cell_w, 1 if i > 0 else 0)

        return w


class MonsterCardGroup(QWidget):
    """Displays 1–N MonsterCard widgets side by side."""

    open_web = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("QWidget { background:#15151b; }")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(8)
        self._layout.addStretch()
        self._cards: List[MonsterCard] = []
        self._image_base: Optional[Path] = None

    def set_image_base(self, path: Optional[Path]) -> None:
        self._image_base = path

    def show_monsters(self, names: List[str],
                      imported: Dict[str, dict]) -> None:
        """Rebuild cards for the given names using imported data."""
        # remove old cards
        for card in self._cards:
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        for name in names:
            sections = imported.get(name)
            card = MonsterCard(name, sections, self._image_base)
            card.open_web.connect(self.open_web)
            # insert before the trailing stretch
            self._layout.insertWidget(self._layout.count() - 1, card)
            self._cards.append(card)

    def clear(self) -> None:
        self.show_monsters([], {})
