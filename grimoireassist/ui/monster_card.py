"""Local monster info card — renders imported data without a web view."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

# ── Palette ────────────────────────────────────────────────────────────────────
# The "tabletop" (group bg) is kept a notch lighter than the deepest shadow and the
# card surface is lifted above it, so the drop shadow has contrast to read against
# on the dark theme — giving cards a raised, physical feel.
_GROUP_BG    = "#1a1a22"
_CARD_BG     = "#2a2a3a"
_CARD_BDR    = "#4a4a68"
_CARD_RADIUS = 10
_NAME_BG     = "#252538"
_SECT_BG     = "#2a2a3e"
_DIV_COL     = "#35354e"
_TEXT_FG     = "#e8e8ec"
_KEY_FG      = "#9a9aa3"
_DIM_FG      = "#6b6b75"

# ── Sizes (70 % of original fullscreen scale) ─────────────────────────────────
_ICON_SIZE   = 34
_PORTRAIT_H  = 150
_COLS        = 4
_CARD_MIN_H  = 420   # cards never shrink below this; blank space fills bottom
_CARD_MIN_W  = 340   # narrowest a card column may get before a column is dropped

# Font sizes (px)
_FS_NAME  = 18
_FS_SECT  = 14
_FS_BODY  = 17
_FS_TABLE = 20   # table cells read from a distance more than prose rows
_FS_DIM   = 21


# ── Helpers ───────────────────────────────────────────────────────────────────

def _px(path: str, base: Optional[Path]) -> Optional[QPixmap]:
    if not path or not base:
        return None
    full = base / path
    if not full.exists():
        return None
    px = QPixmap(str(full))
    return px if not px.isNull() else None


def _val_label(text: str, font_px: int = _FS_BODY,
               color: str = _TEXT_FG) -> QLabel:
    """QLabel for value cells.

    Wraps `text` in an outer span that sets the default colour and font-size,
    then lets any inner <span style="color:X"> from the imported data cascade
    over the default.  Using the HTML root span avoids Qt stylesheet vs
    rich-text colour-override conflicts.
    """
    lbl = QLabel()
    lbl.setTextFormat(Qt.TextFormat.RichText)
    # The outer span supplies defaults; inner spans (colour arrows) override it.
    lbl.setText(
        f'<span style="color:{color}; font-size:{font_px}px;">{text}</span>')
    lbl.setStyleSheet("background:transparent; border:none;")
    lbl.setWordWrap(True)
    return lbl


def _key_label(text: str, font_px: int = _FS_BODY) -> QLabel:
    """Plain (non-HTML) label for key / header cells."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"font-size:{font_px}px; color:{_KEY_FG};"
        " background:transparent; border:none;")
    lbl.setWordWrap(True)
    return lbl


# ── Card surface ───────────────────────────────────────────────────────────────

class _CardFrame(QWidget):
    """Draws the rounded-rectangle card background + border via QPainter."""

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(),
                            _CARD_RADIUS, _CARD_RADIUS)
        p.fillPath(path, QColor(_CARD_BG))
        p.setPen(QColor(_CARD_BDR))
        p.drawPath(path)


# ── Monster card ───────────────────────────────────────────────────────────────

class MonsterCard(QWidget):
    open_web = pyqtSignal(str)

    def __init__(self, name: str, sections: Optional[Dict],
                 image_base: Optional[Path], parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(200)
        self.setMinimumHeight(_CARD_MIN_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        # Generous margins leave room for the drop shadow to fall outside the card
        # surface so it reads like a physical card resting on the table.
        outer.setContentsMargins(14, 10, 14, 22)
        outer.setSpacing(0)

        frame = _CardFrame(self)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 220))
        shadow.setOffset(0, 9)
        frame.setGraphicsEffect(shadow)
        outer.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 10)
        lay.setSpacing(0)

        # ── Name banner ────────────────────────────────────────────────────
        banner = QWidget(frame)
        banner.setObjectName("banner")
        banner.setStyleSheet(
            f"QWidget#banner {{ background:{_NAME_BG};"
            f" border-radius:{_CARD_RADIUS}px;"
            " border-bottom-left-radius:0; border-bottom-right-radius:0; }}")
        bl = QVBoxLayout(banner)
        bl.setContentsMargins(12, 10, 12, 10)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            f"font-size:{_FS_NAME}px; font-weight:700; color:{_TEXT_FG};"
            " background:transparent; border:none;")
        name_lbl.setWordWrap(True)
        bl.addWidget(name_lbl)
        lay.addWidget(banner)

        # ── Portrait ───────────────────────────────────────────────────────
        if sections:
            img_path = sections.get("_image_path")
            if img_path:
                px = _px(img_path, image_base)
                if px:
                    portrait = QLabel(frame)
                    portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    portrait.setStyleSheet("background:transparent; border:none;")
                    portrait.setPixmap(
                        px.scaled(600, _PORTRAIT_H,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
                    lay.addWidget(portrait)

        # ── Divider ────────────────────────────────────────────────────────
        div = QFrame(frame)
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(
            f"border:none; border-top:1px solid {_DIV_COL}; color:{_DIV_COL};")
        lay.addWidget(div)

        # ── Body ───────────────────────────────────────────────────────────
        body = QWidget(frame)
        body.setStyleSheet("background:transparent;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(12, 8, 12, 0)
        body_lay.setSpacing(6)

        if sections:
            self._build_sections(body_lay, sections, image_base)
        else:
            body_lay.addWidget(_val_label("No local data", _FS_DIM, _DIM_FG))

        body_lay.addStretch()
        lay.addWidget(body)

    # ── Section rendering ──────────────────────────────────────────────────────

    def _build_sections(self, lay: QVBoxLayout, sections: Dict,
                        image_base: Optional[Path]) -> None:
        order = (["_root"] if "_root" in sections else []) + [
            k for k in sections if not k.startswith("_")
        ]
        first = True
        for key in order:
            sec_val = sections[key]
            if isinstance(sec_val, dict):
                sec_type = sec_val.get("_type")
                rows = sec_val.get("rows", [])
            else:
                sec_type = None
                rows = sec_val
            if not rows:
                continue
            if not first:
                lay.addSpacing(8)
            first = False
            show_header = sec_val.get("_header", True) if isinstance(sec_val, dict) else True
            if key != "_root" and show_header:
                lbl = QLabel(key)
                lbl.setStyleSheet(
                    f"background:{_SECT_BG}; color:{_TEXT_FG};"
                    f" font-size:{_FS_SECT}px; font-weight:600;"
                    " border-radius:3px; padding:2px 6px; border:none;")
                lay.addWidget(lbl)
            if sec_type == "table-col-row":
                lay.addWidget(self._col_row_table(rows, image_base))
            elif sec_type == "table-row-col":
                lay.addWidget(self._row_col_table(rows, image_base))
            else:
                for row in rows:
                    lay.addWidget(self._kv_row(row, image_base))

    # ── Cell / row builders ────────────────────────────────────────────────────

    def _icon_label(self, path: str, image_base: Optional[Path]) -> Optional[QLabel]:
        px = _px(path, image_base)
        if not px:
            return None
        lbl = QLabel()
        lbl.setStyleSheet("background:transparent; border:none;")
        lbl.setPixmap(px.scaled(_ICON_SIZE, _ICON_SIZE,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation))
        return lbl

    def _cell_widget(self, cell: dict, image_base: Optional[Path],
                     is_key: bool = False, font_px: int = _FS_BODY) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        cl = QHBoxLayout(w)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        for p in cell.get("img_paths", []):
            il = self._icon_label(p, image_base)
            if il:
                cl.addWidget(il)
        text = cell.get("text", "")
        if text:
            lbl = (_key_label(text, font_px) if is_key
                   else _val_label(text, font_px))
            cl.addWidget(lbl)
        return w

    def _col_row_table(self, rows: list, image_base: Optional[Path]) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setSpacing(4)
        for col, row in enumerate(rows):
            grid.addWidget(
                self._cell_widget(row[0] if row else {}, image_base,
                                  is_key=True, font_px=_FS_TABLE),
                0, col, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(
                self._cell_widget(row[1] if len(row) > 1 else {}, image_base,
                                  font_px=_FS_TABLE),
                1, col, Qt.AlignmentFlag.AlignCenter)
        return w

    def _row_col_table(self, rows: list, image_base: Optional[Path]) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setSpacing(4)
        for r_i, row in enumerate(rows):
            for c_i, cell in enumerate(row):
                grid.addWidget(
                    self._cell_widget(cell, image_base, is_key=(r_i == 0),
                                      font_px=_FS_TABLE),
                    r_i, c_i, Qt.AlignmentFlag.AlignCenter)
        return w

    def _kv_row(self, row: list, image_base: Optional[Path]) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        rl = QHBoxLayout(w)
        rl.setContentsMargins(0, 2, 0, 2)
        rl.setSpacing(8)
        for i, cell in enumerate(row):
            is_key = (i == 0 and len(row) == 2)
            sub = cell.get("sub_items")
            if sub and not is_key:
                cw = self._sub_items_widget(sub, image_base)
            else:
                cw = QWidget()
                cw.setStyleSheet("background:transparent;")
                cl = QHBoxLayout(cw)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setSpacing(6)
                for p in cell.get("img_paths", []):
                    il = self._icon_label(p, image_base)
                    if il:
                        cl.addWidget(il)
                text = cell.get("text", "")
                if text:
                    lbl = (_key_label(text) if is_key
                           else _val_label(text))
                    cl.addWidget(lbl, 0 if cell.get("img_paths") else 1)
            rl.addWidget(cw, 1 if i > 0 else 0)
        return w

    def _sub_items_widget(self, sub_items: list,
                          image_base: Optional[Path]) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(3)
        for sub in sub_items:
            rw = QWidget()
            rw.setStyleSheet("background:transparent;")
            rl = QHBoxLayout(rw)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(5)
            text = sub.get("text", "")
            if text:
                rl.addWidget(_val_label(text))
            for p in sub.get("img_paths", []):
                il = self._icon_label(p, image_base)
                if il:
                    rl.addWidget(il)
            rl.addStretch()
            vl.addWidget(rw)
        return w


# ── Card group ─────────────────────────────────────────────────────────────────

class MonsterCardGroup(QWidget):
    """Card grid with responsive columns: `cols` is the maximum; columns are
    dropped as the panel narrows (each card keeps ≥ _CARD_MIN_W), down to a
    single full-width column where cards stack vertically."""

    open_web = pyqtSignal(str)

    def __init__(self, cols: int = _COLS, parent=None) -> None:
        super().__init__(parent)
        self._max_cols = max(1, cols)
        self._cols = self._max_cols
        self.setStyleSheet(f"QWidget {{ background:{_GROUP_BG}; }}")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background:{_GROUP_BG}; border:none; }}")

        self._container = QWidget()
        self._container.setStyleSheet(f"QWidget {{ background:{_GROUP_BG}; }}")

        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(16, 12, 16, 12)
        self._grid.setHorizontalSpacing(20)  # gap between card columns
        self._grid.setVerticalSpacing(12)     # gap between card rows
        self._apply_column_stretch()

        scroll.setWidget(self._container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self._cards: List[MonsterCard] = []
        self._image_base: Optional[Path] = None

    def set_image_base(self, path: Optional[Path]) -> None:
        self._image_base = path

    # ── responsive columns ─────────────────────────────────────────────────────
    def _effective_cols(self) -> int:
        """Columns that fit at the current width, capped at the per-game max."""
        m = self._grid.contentsMargins()
        avail = self.width() - m.left() - m.right()
        sp = self._grid.horizontalSpacing()
        fit = (avail + sp) // (_CARD_MIN_W + sp)
        return max(1, min(self._max_cols, fit))

    def _apply_column_stretch(self) -> None:
        # Stretch only the active columns; zero the rest so dropped columns
        # from a previous layout don't keep claiming width.
        for c in range(max(self._grid.columnCount(), self._max_cols)):
            self._grid.setColumnStretch(c, 1 if c < self._cols else 0)

    def _place_cards(self) -> None:
        self._apply_column_stretch()
        for i, card in enumerate(self._cards):
            grid_row, grid_col = divmod(i, self._cols)
            self._grid.addWidget(card, grid_row, grid_col, Qt.AlignmentFlag.AlignTop)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cols = self._effective_cols()
        if cols != self._cols:
            self._cols = cols
            for card in self._cards:
                self._grid.removeWidget(card)
            self._place_cards()

    def show_monsters(self, names: List[str],
                      imported: Dict[str, dict]) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        self._cols = self._effective_cols()
        for name in names:
            sections = imported.get(name)
            card = MonsterCard(name, sections, self._image_base)
            card.open_web.connect(self.open_web)
            self._cards.append(card)
        self._place_cards()

    def clear(self) -> None:
        self.show_monsters([], {})
