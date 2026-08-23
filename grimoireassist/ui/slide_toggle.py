"""A sliding ON/OFF toggle for the toolbar.

Two labelled halves with a highlight that slides behind the active one — the
shape the Auto Switch control introduced. It reads at a glance from across the
room, which a checkable QToolButton does not: a pressed-in icon button is easy
to misread while a match is running.

Both halves are click targets (clicking the active one is a no-op), Space and
Return flip it, and `toggled` fires only on a real change — never from
`set_on`, so a host can push state back without re-entering its own slot.
"""
from __future__ import annotations

from PyQt6.QtCore import (QEasingCurve, QRectF, Qt, QVariantAnimation,
                          pyqtSignal)
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import QWidget

_TRACK = QColor("#2a2a36")
_KNOB_ON = QColor("#3f9a54")    # green while on
_KNOB_OFF = QColor("#4a4a57")   # muted grey while off
_TEXT_ACTIVE = QColor("#ffffff")
_TEXT_IDLE = QColor("#cfcfd6")

_HEIGHT = 24
_PAD = 24        # slack around the widest label, so neither half clips
_TEXT_PX = 12


class SlideToggle(QWidget):
    """Sliding ON/OFF toggle. `labels` is (on_text, off_text)."""

    toggled = pyqtSignal(bool)  # True = on

    def __init__(self, labels: tuple[str, str] = ("On", "Off"),
                 tooltip: str = "", on: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._labels = (str(labels[0]), str(labels[1]))
        self._on = bool(on)
        # highlight position: 0 = on (left half), 1 = off (right half)
        self._knob = 0.0 if self._on else 1.0
        self._anim = QVariantAnimation(self, duration=140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if tooltip:
            self.setToolTip(tooltip)
        f = self.font()
        f.setPixelSize(_TEXT_PX)
        self.setFont(f)
        # each half fits the widest label (bold, so the active state doesn't clip)
        f.setBold(True)
        self._half = max(QFontMetrics(f).horizontalAdvance(t)
                         for t in self._labels) + _PAD
        self.setFixedSize(self._half * 2, _HEIGHT)

    def is_on(self) -> bool:
        return self._on

    def set_on(self, on: bool) -> None:
        """Push state in without emitting — for syncing to config or a menu."""
        self._on = bool(on)
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(0.0 if self._on else 1.0)
        self._anim.start()

    def _select(self, on: bool) -> None:
        if on != self._on:
            self.set_on(on)
            self.toggled.emit(self._on)

    def _on_anim(self, value) -> None:
        self._knob = float(value)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._select(event.position().x() < self._half)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self._select(not self._on)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_TRACK)
        p.drawRoundedRect(r, radius, radius)
        knob = QRectF(self._knob * self._half, 0, self._half, r.height())
        p.setBrush(_KNOB_ON if self._on else _KNOB_OFF)
        p.drawRoundedRect(knob.adjusted(2, 2, -2, -2), radius - 2, radius - 2)
        active = 0 if self._on else 1
        f = p.font()
        for i, text in enumerate(self._labels):
            seg = QRectF(i * self._half, 0, self._half, r.height())
            f.setBold(i == active)
            p.setFont(f)
            p.setPen(_TEXT_ACTIVE if i == active else _TEXT_IDLE)
            p.drawText(seg, Qt.AlignmentFlag.AlignCenter, text)


class AutoSwitchToggle(SlideToggle):
    """The Auto Switch control: when ON, the main window follows OCR
    detections (tracking view while monsters are seen, Grimoire after the idle
    timeout). When OFF, the view only changes when the user picks it by hand.
    Independent from the manual Grimoire view button — showing the Grimoire
    never flips this toggle."""

    def __init__(self, parent=None) -> None:
        super().__init__(
            labels=("Auto On", "Auto Off"),
            tooltip="Auto Switch: follow OCR detections to the tracking view",
            on=True, parent=parent)
