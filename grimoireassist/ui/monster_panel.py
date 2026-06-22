"""Panel that shows the monster info site page for detected monsters.

A row of buttons (one per detected monster name) sits above an embedded web view
that loads `<url_template>` for the selected monster. When the OCR area is empty
the view is cleared.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    _HAVE_WEBENGINE = True
except Exception:  # pragma: no cover - WebEngine missing
    _HAVE_WEBENGINE = False


def to_slug(name: str) -> str:
    """monsterbuddy-style slug: lowercase, non-alphanumerics -> single hyphen."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


_BLANK_HTML = (
    "<html><body style='background:#15151b;color:#5a5a63;font-family:Segoe UI;"
    "display:flex;align-items:center;justify-content:center;height:100%;margin:0'>"
    "<div>No monsters detected</div></body></html>"
)


class MonsterPanel(QWidget):
    def __init__(self, url_template: str, slug_map: Optional[Dict[str, str]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.url_template = url_template
        self.slug_map = slug_map or {}
        self.current: Optional[str] = None
        self._buttons: List[QPushButton] = []

        self.setStyleSheet(
            "QWidget { background:#15151b; color:#e8e8ec; }"
            "QPushButton { background:#2a2a36; border:none; border-radius:6px;"
            " padding:6px 12px; color:#e8e8ec; }"
            "QPushButton:checked { background:#ffd479; color:#1a1a1a; font-weight:600; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self.status_label = QLabel("Watching OCR area…")
        self.status_label.setStyleSheet("font-size:14px; font-weight:600; color:#6b6b75;")
        outer.addWidget(self.status_label)

        # row of monster-name buttons
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(6)
        self._btn_row.addStretch(1)
        outer.addLayout(self._btn_row)

        # embedded page
        if _HAVE_WEBENGINE:
            self.web = QWebEngineView()
            self.web.setHtml(_BLANK_HTML)
            outer.addWidget(self.web, 1)
        else:
            self.web = None
            self._fallback = QLabel("PyQt6-WebEngine not installed — cannot embed page.")
            self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._fallback.setStyleSheet("color:#c66;")
            outer.addWidget(self._fallback, 1)

    # -- updates ---------------------------------------------------------
    def set_status(self, text: str, active: bool) -> None:
        color = "#3bd16f" if active else "#6b6b75"
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"font-size:14px; font-weight:600; color:{color};")

    def set_monsters(self, names: List[str]) -> None:
        self._rebuild_buttons(names)
        if not names:
            self.current = None
            self._load_blank()
            return
        # keep showing the current monster if it's still present, else the first
        target = self.current if self.current in names else names[0]
        self.show_monster(target)

    def show_monster(self, name: str) -> None:
        self.current = name
        for b in self._buttons:
            b.setChecked(b.text() == name)
        if self.web is not None:
            slug = self.slug_map.get(name) or to_slug(name)
            self.web.setUrl(QUrl(self.url_template.format(name=slug)))

    # -- helpers ---------------------------------------------------------
    def _rebuild_buttons(self, names: List[str]) -> None:
        # clear existing buttons (keep trailing stretch at index -1)
        while self._btn_row.count() > 1:
            item = self._btn_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._buttons = []
        for name in names:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, n=name: self.show_monster(n))
            self._btn_row.insertWidget(self._btn_row.count() - 1, btn)
            self._buttons.append(btn)

    def _load_blank(self) -> None:
        for b in self._buttons:
            b.setChecked(False)
        if self.web is not None:
            self.web.setHtml(_BLANK_HTML)
