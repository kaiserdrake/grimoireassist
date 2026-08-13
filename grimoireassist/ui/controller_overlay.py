"""Controller button reference: two pads side by side, floating over the
tracking view.

Switching between a PlayStation, Switch, Xbox or Steam Deck pad mid-session is
confusing because the four face buttons share one physical diamond but carry
different labels. This overlay shows the chosen pair's real layouts next to each
other — no mapping lines, the shared geometry does the work.

Like `InputPreview` it is a plain child of the main pane, living outside its
layout and placed by hand, with an event filter on the parent so it follows
window resizes. Unlike the preview it is *movable*: drag it anywhere, and the
position is kept as a fraction of the free space so it holds its relative place
(and never lands off-screen) when the window is resized.

The header strip names the current pair and carries a chevron that folds the
diagram down to a compact pill, in place. `geometry_changed` and
`collapsed_changed` fire once per gesture so the host can persist the state.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from .controllers import (DEFAULT_LEFT, DEFAULT_RIGHT, ControllerLayout,
                          get_layout)

DEFAULT_WIDTH = 420   # overall width in px; the height follows
MIN_WIDTH = 260       # below this the button labels stop being readable
_MARGIN = 12          # gap kept to the host's edges when clamping

# The header is drawn in real pixels and never scales with the width, so the
# title stays readable and the chevron stays a consistent click target.
_HEADER_H = 26
_CHEVRON_W = 24
_TITLE_PT = 8.0

# The pads below it are drawn in this design space and scaled by width/_BODY_W,
# so the width is the only degree of freedom — same contract as the PiP.
_BODY_W = 480.0
_BODY_H = 220.0
_BODY_ASPECT = _BODY_W / _BODY_H

_HALF_W = _BODY_W / 2      # one pad's column
_PILL_W, _PILL_H = 34.0, 22.0
_PILL_INSET = 14.0
_TRIGGER_Y, _BUMPER_Y = 12.0, 40.0
_FACE_R = 23.0             # face-button radius
_DIAMOND_CY = 142.0        # diamond centre within the body
_DIAMOND_OFF = 46.0        # centre-to-centre distance out to each button

_GRIP = 16                 # bottom-right resize handle, in real px

_BACKDROP = QColor(14, 14, 20, 225)
_BORDER = "#3a3a4a"
_BORDER_HI = "#6a6a80"
_MUTED = "#8a8a99"
_TEXT = "#c8c8d2"


def _clamp01(value: float) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


class ControllerMapOverlay(QWidget):
    """Floating two-pad button reference over the parent's client area."""

    geometry_changed = pyqtSignal(float, float, int)  # fx, fy, width
    collapsed_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget, left_id: str = DEFAULT_LEFT,
                 right_id: str = DEFAULT_RIGHT, width: int = DEFAULT_WIDTH,
                 fx: float = 0.5, fy: float = 0.08,
                 collapsed: bool = False) -> None:
        super().__init__(parent)
        self._left: ControllerLayout = get_layout(left_id, DEFAULT_LEFT)
        self._right: ControllerLayout = get_layout(right_id, DEFAULT_RIGHT)
        self._collapsed = bool(collapsed)
        self._wanted = max(MIN_WIDTH, int(width))  # width the user asked for
        self._fx = _clamp01(fx)   # position as a fraction of the free space,
        self._fy = _clamp01(fy)   # so a resized host keeps it in proportion
        self._hover_chevron = False
        self._move: Optional[tuple] = None    # (press point, widget pos at press)
        self._resize: Optional[tuple] = None  # (press point, width at press)
        self._press_chevron = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setToolTip("Drag to move · chevron collapses · corner resizes")
        self._refit()
        parent.installEventFilter(self)

    # ---- state -----------------------------------------------------------
    def set_pair(self, left_id: str, right_id: str) -> None:
        """Show a different pair of pads (the burger menu drives this)."""
        self._left = get_layout(left_id, DEFAULT_LEFT)
        self._right = get_layout(right_id, DEFAULT_RIGHT)
        self._refit(keep_pos=True)  # the collapsed pill sizes to the title
        self.update()

    def set_collapsed(self, collapsed: bool) -> None:
        """Fold to the header pill, or unfold back to the user's width."""
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._refit(keep_pos=True)  # folds in place: the top-left stays put
        self.update()
        self.collapsed_changed.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def geometry_setting(self) -> tuple[float, float, int]:
        """(fx, fy, width) — what the host persists to config."""
        return (self._fx, self._fy, self._wanted)

    def _title(self) -> str:
        """Full pad names while expanded; short ones in the pill, where the
        width is driven by the text itself."""
        if self._collapsed:
            return f"{self._left.short} → {self._right.short}"
        return f"{self._left.name}  →  {self._right.name}"

    def _title_font(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(_TITLE_PT)
        return font

    # ---- sizing ----------------------------------------------------------
    def _collapsed_width(self) -> int:
        """The pill is only as wide as its title needs."""
        metrics = QFontMetrics(self._title_font())
        return int(metrics.horizontalAdvance(self._title())
                   + 12 + 8 + _CHEVRON_W + 8)

    def _target_size(self) -> tuple[int, int]:
        """Size to lay out at: the requested width capped to what the host can
        hold. The request is kept intact, so growing the host restores it."""
        host = self.parentWidget()
        max_w, max_h = 4096, 4096
        if host is not None and host.width() > 0:
            max_w = max(MIN_WIDTH, host.width() - 2 * _MARGIN)
            max_h = max(_HEADER_H, host.height() - 2 * _MARGIN)
        if self._collapsed:
            return (min(self._collapsed_width(), max_w), _HEADER_H)
        width = min(self._wanted, max_w)
        body_h = max_h - _HEADER_H
        if body_h > 0:
            width = min(width, round(body_h * _BODY_ASPECT))
        width = max(MIN_WIDTH, int(width))
        return (width, _HEADER_H + max(1, round(width / _BODY_ASPECT)))

    def _refit(self, keep_pos: bool = False) -> None:
        width, height = self._target_size()
        if (width, height) != (self.width(), self.height()):
            self.setFixedSize(width, height)
        if keep_pos:
            # A collapse or a grip drag anchors the top-left corner.
            self._move_within_host(self.x(), self.y())
            self._store_fractions()
        else:
            self._reposition()
        self.raise_()

    # ---- placement -------------------------------------------------------
    def _free_space(self) -> tuple[int, int]:
        host = self.parentWidget()
        if host is None:
            return (0, 0)
        return (max(0, host.width() - self.width()),
                max(0, host.height() - self.height()))

    def _move_within_host(self, x: int, y: int) -> None:
        free_w, free_h = self._free_space()
        self.move(min(max(0, int(x)), free_w), min(max(0, int(y)), free_h))

    def _reposition(self) -> None:
        """Place from the stored fractions (never writes them back, so a host
        too small to honour them doesn't destroy the user's choice)."""
        free_w, free_h = self._free_space()
        self._move_within_host(round(self._fx * free_w),
                               round(self._fy * free_h))

    def _store_fractions(self) -> None:
        """Record where the user put it, as a share of the free space."""
        free_w, free_h = self._free_space()
        if free_w:
            self._fx = self.x() / free_w
        if free_h:
            self._fy = self.y() / free_h

    def _emit_geometry(self) -> None:
        self.geometry_changed.emit(self._fx, self._fy, self._wanted)

    # ---- hit boxes -------------------------------------------------------
    def _header_rect(self) -> QRect:
        return QRect(0, 0, self.width(), _HEADER_H)

    def _chevron_rect(self) -> QRect:
        return QRect(self.width() - _CHEVRON_W - 6, 3, _CHEVRON_W, _HEADER_H - 6)

    def _grip_rect(self) -> QRect:
        if self._collapsed:
            return QRect()  # nothing to resize while folded
        return QRect(self.width() - _GRIP, self.height() - _GRIP, _GRIP, _GRIP)

    # ---- mouse -----------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(ev)
            return
        pos = ev.position().toPoint()
        if self._chevron_rect().contains(pos):
            self._press_chevron = True
        elif self._grip_rect().contains(pos):
            self._resize = (ev.globalPosition().toPoint(), self.width())
        else:
            self._move = (ev.globalPosition().toPoint(), self.pos())
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:
        if self._resize is not None:
            start, base = self._resize
            pos = ev.globalPosition().toPoint()
            dx, dy = pos.x() - start.x(), pos.y() - start.y()
            # The grabbed corner travels (+1, +1/aspect) per pixel of added
            # width, so project the pointer delta onto that direction — a
            # free-hand drag still tracks the pointer closely.
            k = 1.0 / _BODY_ASPECT
            self._wanted = max(MIN_WIDTH,
                               round(base + (dx + dy * k) / (1 + k * k)))
            self._refit(keep_pos=True)
            self.update()
            ev.accept()
            return
        if self._move is not None:
            start, origin = self._move
            pos = ev.globalPosition().toPoint()
            self._move_within_host(origin.x() + pos.x() - start.x(),
                                   origin.y() + pos.y() - start.y())
            ev.accept()
            return
        self._update_cursor(ev.position().toPoint())
        hovered = self._chevron_rect().contains(ev.position().toPoint())
        if hovered != self._hover_chevron:
            self._hover_chevron = hovered
            self.update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._press_chevron:
            self._press_chevron = False
            # Only a release still on the chevron counts, so a sloppy drag that
            # started there doesn't toggle.
            if self._chevron_rect().contains(ev.position().toPoint()):
                self.set_collapsed(not self._collapsed)
            ev.accept()
            return
        if self._resize is not None:
            self._resize = None
            self._wanted = self.width()  # drop any slack past the host cap
            self._store_fractions()
            self._emit_geometry()
            self.update()
            ev.accept()
            return
        if self._move is not None:
            _start, origin = self._move
            self._move = None
            if self.pos() != origin:
                self._store_fractions()
                self._emit_geometry()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:
        if (ev.button() == Qt.MouseButton.LeftButton
                and self._header_rect().contains(ev.position().toPoint())):
            self._move = None  # the first click of the pair started a move
            self.set_collapsed(not self._collapsed)
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def _update_cursor(self, pos) -> None:
        if self._chevron_rect().contains(pos):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._grip_rect().contains(pos):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)

    def leaveEvent(self, ev) -> None:
        self._hover_chevron = False
        self.unsetCursor()
        self.update()
        super().leaveEvent(ev)

    # ---- painting --------------------------------------------------------
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_backdrop(p)
        self._paint_header(p)
        if self._collapsed:
            return
        p.save()
        p.translate(0.0, float(_HEADER_H))
        scale = self.width() / _BODY_W
        p.scale(scale, scale)
        self._paint_pad(p, self._left, 0.0)
        self._paint_pad(p, self._right, _HALF_W)
        p.setPen(QPen(QColor(_BORDER), 1.0))
        p.drawLine(QPointF(_HALF_W, 12.0), QPointF(_HALF_W, _BODY_H - 12.0))
        p.restore()
        self._paint_grip(p)

    def _paint_backdrop(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)   # borderless: the fill alone frames it
        p.setBrush(_BACKDROP)
        p.drawRoundedRect(QRectF(0.0, 0.0, self.width(), self.height()), 10, 10)

    def _paint_header(self, p: QPainter) -> None:
        font = self._title_font()
        p.setFont(font)
        p.setPen(QColor(_MUTED))
        box = QRect(12, 0, max(0, self.width() - 12 - _CHEVRON_W - 14), _HEADER_H)
        p.drawText(box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(font).elidedText(
                       self._title(), Qt.TextElideMode.ElideRight, box.width()))
        if not self._collapsed:
            p.setPen(QPen(QColor(_BORDER), 1.0))
            p.drawLine(1, _HEADER_H, self.width() - 2, _HEADER_H)
        self._paint_chevron(p)

    def _paint_chevron(self, p: QPainter) -> None:
        """Points up while expanded (click folds it), down while collapsed."""
        box = self._chevron_rect()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(_BORDER_HI if self._hover_chevron else _BORDER), 1.0))
        p.drawRoundedRect(QRectF(box), 4, 4)
        cx, cy = box.center().x() + 0.5, box.center().y() + 0.5
        arm, rise = 4.0, 2.0
        tip = -rise if self._collapsed else rise
        p.setPen(QPen(QColor(_TEXT), 1.4))
        p.drawLine(QPointF(cx - arm, cy + tip), QPointF(cx, cy - tip))
        p.drawLine(QPointF(cx, cy - tip), QPointF(cx + arm, cy + tip))

    def _paint_pad(self, p: QPainter, pad: ControllerLayout, x0: float) -> None:
        """One pad in body design units, its column starting at x0."""
        right_x = x0 + _HALF_W - _PILL_INSET - _PILL_W
        for btn, x, y in ((pad.lt, x0 + _PILL_INSET, _TRIGGER_Y),
                          (pad.rt, right_x, _TRIGGER_Y),
                          (pad.lb, x0 + _PILL_INSET, _BUMPER_Y),
                          (pad.rb, right_x, _BUMPER_Y)):
            self._paint_pill(p, btn, QRectF(x, y, _PILL_W, _PILL_H))
        cx = x0 + _HALF_W / 2
        for btn, dx, dy in ((pad.top, 0.0, -_DIAMOND_OFF),
                            (pad.right, _DIAMOND_OFF, 0.0),
                            (pad.bottom, 0.0, _DIAMOND_OFF),
                            (pad.left, -_DIAMOND_OFF, 0.0)):
            self._paint_face(p, btn, cx + dx, _DIAMOND_CY + dy)

    def _paint_pill(self, p: QPainter, btn, rect: QRectF) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(btn.fill))
        p.drawRoundedRect(rect, 5, 5)
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        p.setFont(font)
        p.setPen(QColor(btn.text))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, btn.label)

    def _paint_face(self, p: QPainter, btn, cx: float, cy: float) -> None:
        rect = QRectF(cx - _FACE_R, cy - _FACE_R, _FACE_R * 2, _FACE_R * 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(btn.fill))
        p.drawEllipse(rect)
        font = QFont(self.font())
        font.setPointSizeF(14.0)
        p.setFont(font)
        p.setPen(QColor(btn.text))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, btn.label)

    def _paint_grip(self, p: QPainter) -> None:
        """Three short diagonals in the bottom-right corner: the resize handle."""
        p.setPen(QPen(QColor(255, 255, 255, 110 if self._resize else 70), 1.0))
        right, bottom = self.width() - 3, self.height() - 3
        for off in (4, 8, 12):
            p.drawLine(right - off, bottom, right, bottom - off)

    # ---- host tracking ---------------------------------------------------
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self._refit()  # re-clamp the size, then re-apply the fractions
        return False

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._refit()
