"""Monster info UI.

- `MonsterNav`   : status text ("X detected") + a button per detected monster.
                  Lives in the toolbar/navbar next to the burger menu.
- `MonsterPanel` : the embedded web area (monster page + grimoire view) and the
                  idle countdown label. This is the central widget.

The Grimoire view (index 1 in the stack) is toggled by the toolbar button in
MainWindow via set_grimoire_visible(). Auth cookies are stored in a persistent
QWebEngineProfile so the user stays logged in between sessions.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    _HAVE_WEBENGINE = True
except Exception:  # pragma: no cover - WebEngine missing
    _HAVE_WEBENGINE = False

GRIMOIRE_URL = "https://grimoire.laeradsphere.com/"
_GRIMOIRE_PROFILE_NAME = "grimoire_persistent"


def _inject_rotate_fix(page) -> None:
    """Strip the 180° rotation from the grimoire drawer pull-tabs.
    QtWebEngine misrenders writing-mode + any 180° rotation (shows mirrored/upside-down).
    Removing the rotation leaves writing-mode: vertical-rl which renders the text
    correctly sideways. Regular Chrome is unaffected — this script only runs in the app."""
    from PyQt6.QtWebEngineCore import QWebEngineScript
    css = (
        ".notes-toc-pull {"
        "  writing-mode: horizontal-tb !important;"
        "  text-orientation: unset !important;"
        "  white-space: nowrap !important;"
        "  width: 80px !important;"
        "  height: 16px !important;"
        "  right: -32px !important;"
        "  transform: translateY(-50%) rotate(-90deg) !important;"
        "}"
        ".recent-drawer-pull {"
        "  writing-mode: horizontal-tb !important;"
        "  text-orientation: unset !important;"
        "  white-space: nowrap !important;"
        "  width: 80px !important;"
        "  height: 16px !important;"
        "  margin: 32px -32px 0 -32px !important;"
        "  align-self: flex-start !important;"
        "  transform: rotate(-90deg) !important;"
        "}"
    )
    script = QWebEngineScript()
    script.setName("grimoire-rotate-fix")
    script.setSourceCode(
        "(function(){"
        "  var s = document.createElement('style');"
        f"  s.textContent = {css!r};"
        "  document.head.appendChild(s);"
        "})();"
    )
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    page.scripts().insert(script)


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


class MonsterNav(QWidget):
    """Status text + one button per detected monster. Designed for the toolbar."""

    monster_selected = pyqtSignal(str)  # emits the chosen name, or "" when cleared

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current: Optional[str] = None
        self._buttons: List[QPushButton] = []
        self.setStyleSheet(
            "QLabel { color:#cfcfd6; }"
            "QPushButton { background:#2a2a36; border:none; border-radius:6px;"
            " padding:4px 10px; color:#e8e8ec; }"
            "QPushButton:checked { background:#ffd479; color:#1a1a1a; font-weight:600; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(8)

        self.status_label = QLabel("Tracking areas...")
        self.status_label.setStyleSheet("font-weight:600; color:#6b6b75;")
        row.addWidget(self.status_label)

        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(6)
        row.addLayout(self._btn_row)
        row.addStretch(1)

    def set_status(self, text: str, active: bool, error: bool = False) -> None:
        if error:
            color = "#ff6b6b"
        elif active:
            color = "#3bd16f"
        else:
            color = "#6b6b75"
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-weight:600; color:{color};")

    def set_monsters(self, names: List[str]) -> None:
        self._rebuild_buttons(names)
        if not names:
            self.current = None
            self.monster_selected.emit("")
            return
        target = self.current if self.current in names else names[0]
        self._select(target)

    def _select(self, name: str) -> None:
        self.current = name
        for b in self._buttons:
            b.setChecked(b.text() == name)
        self.monster_selected.emit(name)

    def _rebuild_buttons(self, names: List[str]) -> None:
        while self._btn_row.count():
            item = self._btn_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._buttons = []
        for name in names:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, n=name: self._select(n))
            self._btn_row.addWidget(btn)
            self._buttons.append(btn)


class MonsterPanel(QWidget):
    """Embedded web area (monster page + grimoire view) + idle countdown."""

    def __init__(self, url_template: str, slug_map: Optional[Dict[str, str]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.url_template = url_template
        self.slug_map = slug_map or {}
        self.current: Optional[str] = None

        self.setStyleSheet("QWidget { background:#15151b; color:#e8e8ec; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._countdown_label = QLabel()
        self._countdown_label.setStyleSheet(
            "font-size:11px; color:#5a5a63; padding:4px 10px;")
        self._countdown_label.setVisible(False)
        outer.addWidget(self._countdown_label)

        # stacked web area: index 0 = monster view, index 1 = grimoire view
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        if _HAVE_WEBENGINE:
            self.web = QWebEngineView()
            self.web.setHtml(_BLANK_HTML)
            self._stack.addWidget(self.web)  # index 0

            # persistent profile keeps cookies/localStorage between app sessions
            self._grimoire_profile = QWebEngineProfile(_GRIMOIRE_PROFILE_NAME)
            self._grimoire_profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
            )
            self._grimoire_web = QWebEngineView()
            grimoire_page = QWebEnginePage(self._grimoire_profile, self._grimoire_web)
            self._grimoire_web.setPage(grimoire_page)
            _inject_rotate_fix(grimoire_page)
            self._grimoire_web.setUrl(QUrl(GRIMOIRE_URL))
            self._stack.addWidget(self._grimoire_web)  # index 1
        else:
            self.web = None
            self._grimoire_web = None
            self._grimoire_profile = None
            fallback = QLabel("PyQt6-WebEngine not installed — cannot embed page.")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color:#c66;")
            self._stack.addWidget(fallback)

    # -- grimoire toggle (called by MainWindow toolbar button) -----------
    def set_grimoire_visible(self, visible: bool) -> None:
        self._stack.setCurrentIndex(1 if visible else 0)

    def set_countdown(self, seconds: Optional[int]) -> None:
        """Show a countdown label. Pass None to hide it."""
        if seconds is None:
            self._countdown_label.setVisible(False)
        else:
            self._countdown_label.setText(
                f"Transitioning view in {seconds}s of no object detected")
            self._countdown_label.setVisible(True)

    def open_grimoire_url(self, url: str) -> None:
        """Navigate the grimoire view to a URL and bring it to front (for OCR results)."""
        if self._grimoire_web is not None:
            self._grimoire_web.setUrl(QUrl(url))
        self.set_grimoire_visible(True)

    # -- monster page ----------------------------------------------------
    def show_monster(self, name: str) -> None:
        """Load the monster info page; an empty name shows the blank placeholder."""
        self.current = name or None
        if self.web is None:
            return
        if not name:
            self.web.setHtml(_BLANK_HTML)
            return
        slug = self.slug_map.get(name) or to_slug(name)
        self.web.setUrl(QUrl(self.url_template.format(name=slug)))
