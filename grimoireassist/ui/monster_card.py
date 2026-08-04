"""Local monster info card — renders imported data without a web view.

Cards fill the full height of the panel and are laid out in a single
horizontal carousel: when more cards are detected than fit across the width,
the strip scrolls sideways (arrow buttons, mouse wheel, or drag on the
scrollbar). Card content is never stretched to fill the card — it stays
top-aligned, with blank space below when a monster has little data, and the
card body scrolls internally when it has more than the height can hold.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QEasingCurve, QPointF, QPropertyAnimation, QRectF, QVariantAnimation, Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
    QScrollArea, QScroller, QScrollerProperties, QSizePolicy, QVBoxLayout,
    QWidget,
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
_CARD_MIN_W  = 340   # narrowest a card slot may get before a column is dropped
_ARROW_W     = 48    # width of the carousel's edge chevron zone
_ARROW_H     = 112   # height of that zone (vertically centred)

# Font sizes (px)
_FS_NAME  = 18
_FS_SECT  = 14
_FS_BODY  = 17
_FS_TABLE = 20   # table cells read from a distance more than prose rows
_FS_DIM   = 21


# Slim scrollbars, so the in-card and carousel bars don't eat visual space.
_SCROLLBAR_CSS = """
QScrollBar:vertical { background:transparent; width:8px; margin:0; }
QScrollBar:horizontal { background:transparent; height:8px; margin:0; }
QScrollBar::handle { background:#4a4a68; border-radius:4px; min-height:24px;
                     min-width:24px; }
QScrollBar::handle:hover { background:#5e5e84; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
"""


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
        # Cards take whatever height the carousel gives them; the body scrolls
        # internally rather than the card growing past the viewport.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(200)
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

        # Trailing stretch: content stays packed at the top and the leftover
        # height is simply left blank — the rows are never spread to fill.
        body_lay.addStretch()

        body_scroll = QScrollArea(frame)
        body_scroll.setWidget(body)
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body_scroll.viewport().setAutoFillBackground(False)
        body_scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
            + _SCROLLBAR_CSS)
        lay.addWidget(body_scroll, 1)   # takes the card's leftover height

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

class _CarouselArea(QScrollArea):
    """Horizontal strip: the wheel scrolls sideways (there is no vertical
    scrolling here — cards are exactly viewport-height)."""

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
            return
        super().wheelEvent(event)


class _EdgeChevron(QWidget):
    """Bare chevron at the strip's edge — no frame, no fill, no button
    chrome. It rests at a low opacity and, on hover, brightens and lays a
    soft gradient scrim over the card behind it so the glyph stays legible."""

    clicked = pyqtSignal()

    def __init__(self, direction: int, parent: QWidget) -> None:
        super().__init__(parent)
        self._dir = direction          # -1 = points left, +1 = points right
        self._glow = 0.0               # 0 = resting, 1 = hovered
        self.setFixedSize(_ARROW_W, _ARROW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._anim = QVariantAnimation(self, duration=150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_glow)

    def _on_glow(self, value) -> None:
        self._glow = float(value)
        self.update()

    def _animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._glow)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event) -> None:
        self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate_to(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Scrim: a soft vignette anchored to the outer edge, so the chevron
        # never floats unreadably over bright artwork. Radial (not a banded
        # rectangle) and sized to fade out well inside the widget, so there is
        # no hard edge anywhere — nothing that reads as a button face.
        radius = min(float(w), h / 2.0)
        centre = QPointF(0.0 if self._dir < 0 else float(w), h / 2.0)
        scrim = QRadialGradient(centre, radius)
        alpha = 70 + 90 * self._glow
        scrim.setColorAt(0.0, QColor(10, 10, 16, int(alpha)))
        scrim.setColorAt(0.55, QColor(10, 10, 16, int(alpha * 0.45)))
        scrim.setColorAt(1.0, QColor(10, 10, 16, 0))
        p.fillRect(QRectF(0, 0, w, h), scrim)

        # Chevron: two strokes, round caps/join, nudged toward its edge on hover.
        p.setPen(QPen(QColor(255, 255, 255, int(150 + 105 * self._glow)), 2.4,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        cx = w / 2 + self._dir * (2.0 + 2.0 * self._glow)
        cy = h / 2
        arm = 6.5
        tip = QPointF(cx + self._dir * arm * 0.55, cy)
        back = self._dir * -arm * 0.55
        path = QPainterPath(QPointF(cx + back, cy - arm))
        path.lineTo(tip)
        path.lineTo(QPointF(cx + back, cy + arm))
        p.drawPath(path)


class MonsterCardGroup(QWidget):
    """Full-height card carousel.

    Cards sit in one horizontal row, each as tall as the panel. `cols` is how
    many are shown at once; the slot width shrinks with the panel and columns
    are dropped once a slot would fall below _CARD_MIN_W. Any card past that
    count stays in the strip and is reached by scrolling — the edge chevrons,
    dragging the strip with the mouse, the wheel, or the scrollbar. The
    chevrons wrap around: paging past the last card returns to the first.
    """

    open_web = pyqtSignal(str)

    def __init__(self, cols: int = _COLS, parent=None) -> None:
        super().__init__(parent)
        self._max_cols = max(1, cols)
        self._cols = self._max_cols
        self.setStyleSheet(f"QWidget {{ background:{_GROUP_BG}; }}")

        self._scroll = _CarouselArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background:{_GROUP_BG}; border:none; }}"
            + _SCROLLBAR_CSS)

        self._container = QWidget()
        self._container.setStyleSheet(f"QWidget {{ background:{_GROUP_BG}; }}")

        self._row = QHBoxLayout(self._container)
        self._row.setContentsMargins(16, 12, 16, 12)
        self._row.setSpacing(20)   # gap between cards
        # Trailing stretch keeps a short strip left-aligned instead of the
        # cards spreading out to fill the width.
        self._row.addStretch(1)

        self._scroll.setWidget(self._container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._scroll)

        self._enable_drag_scroll()

        self._bar = self._scroll.horizontalScrollBar()
        self._anim = QPropertyAnimation(self._bar, b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._bar.valueChanged.connect(self._sync_arrows)
        self._bar.rangeChanged.connect(lambda *_: self._sync_arrows())

        self._prev_btn = self._make_arrow(-1)
        self._next_btn = self._make_arrow(+1)

        self._cards: List[MonsterCard] = []
        self._image_base: Optional[Path] = None
        self._sync_arrows()

    def _enable_drag_scroll(self) -> None:
        """Grab-and-drag panning (with flick momentum) anywhere on the strip.

        QScroller is used rather than a hand-rolled event filter because it
        also works when the press lands on a card's own child widgets, and it
        still lets a click through when the pointer barely moved."""
        viewport = self._scroll.viewport()
        QScroller.grabGesture(
            viewport, QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        scroller = QScroller.scroller(viewport)
        props = scroller.scrollerProperties()
        M = QScrollerProperties.ScrollMetric
        off = QScrollerProperties.OvershootPolicy.OvershootAlwaysOff
        props.setScrollMetric(M.VerticalOvershootPolicy, off)
        props.setScrollMetric(M.HorizontalOvershootPolicy, off)
        props.setScrollMetric(M.DragStartDistance, 0.004)  # ~4 mm before it pans
        props.setScrollMetric(M.DecelerationFactor, 0.9)
        scroller.setScrollerProperties(props)
        viewport.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_image_base(self, path: Optional[Path]) -> None:
        self._image_base = path

    # ── carousel controls ──────────────────────────────────────────────────────
    def _make_arrow(self, direction: int) -> _EdgeChevron:
        arrow = _EdgeChevron(direction, self)  # child of the group: floats over the strip
        arrow.clicked.connect(lambda d=direction: self._scroll_by(d))
        arrow.hide()
        return arrow

    def _step(self) -> int:
        """One card + gap: how far a single chevron click travels."""
        return self._card_width() + self._row.spacing()

    def _scroll_by(self, direction: int) -> None:
        """Page one card along, wrapping around at either end."""
        lo, hi = self._bar.minimum(), self._bar.maximum()
        if hi <= lo:
            return
        value = self._bar.value()
        at_edge = value >= hi if direction > 0 else value <= lo
        if at_edge:
            target = lo if direction > 0 else hi   # loop to the other end
        else:
            target = min(hi, max(lo, value + direction * self._step()))
        # The wrap travels the whole strip, so give it a little longer.
        self._anim.stop()
        self._anim.setDuration(420 if at_edge else 220)
        self._anim.setStartValue(value)
        self._anim.setEndValue(target)
        self._anim.start()

    def _sync_arrows(self) -> None:
        """Both chevrons stay available whenever the strip is scrollable —
        either one always leads somewhere, because the ends wrap."""
        scrollable = self._bar.maximum() > self._bar.minimum()
        for arrow in (self._prev_btn, self._next_btn):
            arrow.setVisible(scrollable)
            if scrollable:
                arrow.raise_()

    def _place_arrows(self) -> None:
        y = max(0, (self.height() - _ARROW_H) // 2)
        self._prev_btn.move(0, y)
        self._next_btn.move(max(0, self.width() - _ARROW_W), y)

    # ── responsive slot width ──────────────────────────────────────────────────
    def _metrics(self) -> tuple:
        """(columns, card width) for the current viewport: as many columns as
        the per-game max allows while each card stays ≥ _CARD_MIN_W."""
        m = self._row.contentsMargins()
        sp = self._row.spacing()
        avail = self._scroll.viewport().width() - m.left() - m.right()
        if avail <= 0:
            return 1, _CARD_MIN_W
        cols = max(1, min(self._max_cols, (avail + sp) // (_CARD_MIN_W + sp)))
        return cols, max(1, (avail - sp * (cols - 1)) // cols)

    def _card_width(self) -> int:
        return self._metrics()[1]

    def _apply_widths(self) -> None:
        self._cols, width = self._metrics()
        for card in self._cards:
            card.setFixedWidth(width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_widths()
        self._place_arrows()
        self._sync_arrows()

    def show_monsters(self, names: List[str],
                      imported: Dict[str, dict]) -> None:
        for card in self._cards:
            self._row.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        for name in names:
            sections = imported.get(name)
            card = MonsterCard(name, sections, self._image_base)
            card.open_web.connect(self.open_web)
            # insert before the trailing stretch
            self._row.insertWidget(self._row.count() - 1, card)
            self._cards.append(card)
        self._apply_widths()
        self._bar.setValue(0)
        self._place_arrows()
        self._sync_arrows()

    def clear(self) -> None:
        self.show_monsters([], {})
