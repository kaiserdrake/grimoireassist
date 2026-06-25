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
from urllib.parse import quote

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..ocr import conf_level

# button colours per OCR confidence level
_LEVEL_STYLE = {
    "high": ("#2e9e54", "#eafff0"),   # green
    "mid": ("#d98a2b", "#1a1a1a"),    # orange
    "low": ("#cdbf3a", "#1a1a1a"),    # yellow
}

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


def is_grimoire(url: str) -> bool:
    return "grimoire.laeradsphere.com" in (url or "")


def build_monster_url(url_template, names, url_style="path", slug_map=None, joiner=" || "):
    """Build the info URL for the given monster name(s).

    - "path"  : single monster, slug in the path (/monsters/<slug>).
    - "search": term(s) in a query (?st1=...); multiple are joined with `joiner`.
    Returns None when there are no names. The substituted value is URL-encoded, so
    "Barroth"+"Anjanath" (search) -> st1=anjanath%20%7C%7C%20barroth (sorted).
    """
    slug_map = slug_map or {}

    def term(n):
        if url_style == "search":
            return n.strip().lower()
        return slug_map.get(n) or to_slug(n)

    # sort + dedupe so the same set of monsters always yields the same URL
    # (detection-order changes won't trigger a page reload)
    terms = sorted({term(n) for n in names if n and n.strip()})
    if not terms:
        return None
    value = joiner.join(terms) if url_style == "search" else terms[0]
    return url_template.format(name=quote(value, safe=""))


_BLANK_HTML = (
    "<html><body style='background:#15151b;color:#5a5a63;font-family:Segoe UI;"
    "display:flex;align-items:center;justify-content:center;height:100%;margin:0'>"
    "<div>No monsters detected</div></body></html>"
)


class MonsterNav(QWidget):
    """Status pills (source + tracking) + one button per detected monster."""

    monster_selected = pyqtSignal(str)  # emits the chosen name, or "" when cleared

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current: Optional[str] = None
        self._buttons: List[QPushButton] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        self.source_pill = QLabel()
        self.tracking_pill = QLabel()
        self.set_source("No Source", False)
        self.set_tracking("Idle", None)
        row.addWidget(self.source_pill)
        row.addWidget(self.tracking_pill)
        row.addSpacing(6)

        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(6)
        row.addLayout(self._btn_row)
        row.addStretch(1)

    def _style_pill(self, label: QLabel, text: str, ok: Optional[bool]) -> None:
        # gray pill background; font colour signals status (green=good, red=error)
        if ok is True:
            fg = "#3bd16f"
        elif ok is False:
            fg = "#ff6b6b"
        else:
            fg = "#9a9aa3"
        label.setText(text)
        label.setStyleSheet(
            f"background:#2a2a36; color:{fg}; border-radius:9px;"
            " padding:2px 10px; font-weight:600; font-size:12px;")

    def set_source(self, text: str, ok: Optional[bool]) -> None:
        self._style_pill(self.source_pill, text, ok)

    def set_tracking(self, text: str, ok: Optional[bool]) -> None:
        self._style_pill(self.tracking_pill, text, ok)

    def set_monsters(self, detections: list) -> None:
        """`detections` = [(name, confidence), ...]. Rebuilds the buttons only; the panel
        view is driven separately (so it can show all detected monsters at once)."""
        names = [n for n, _ in detections]
        self._rebuild_buttons(detections)
        if self.current not in names:
            self.current = None
        for b in self._buttons:
            b.setChecked(b.text() == self.current)

    def _select(self, name: str) -> None:
        self.current = name
        for b in self._buttons:
            b.setChecked(b.text() == name)
        self.monster_selected.emit(name)

    def _rebuild_buttons(self, detections: list) -> None:
        while self._btn_row.count():
            item = self._btn_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._buttons = []
        for name, conf in detections:
            level = conf_level(conf)
            bg, fg = _LEVEL_STYLE.get(level, _LEVEL_STYLE["low"])
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setToolTip(f"{level} confidence ({conf:.0%})")
            btn.setStyleSheet(
                f"QPushButton {{ background:{bg}; color:{fg}; border:2px solid {bg};"
                " border-radius:6px; padding:3px 10px; }"
                " QPushButton:checked { border:2px solid #ffffff; }")
            btn.clicked.connect(lambda _c, n=name: self._select(n))
            self._btn_row.addWidget(btn)
            self._buttons.append(btn)


class ViewModeSwitch(QWidget):
    """Two-position switch: 'Auto Switch' (default) vs 'Grimoire' (locked)."""

    mode_changed = pyqtSignal(str)  # "auto" | "grimoire"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._auto = QPushButton("Auto Switch")
        self._gri = QPushButton("Grimoire")
        for b in (self._auto, self._gri):
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto.setChecked(True)
        self._auto.clicked.connect(lambda: self.mode_changed.emit("auto"))
        self._gri.clicked.connect(lambda: self.mode_changed.emit("grimoire"))
        row.addWidget(self._auto)
        row.addWidget(self._gri)
        self.setStyleSheet(
            "QPushButton { background:#2a2a36; color:#cfcfd6; border:none;"
            " padding:4px 12px; font-size:12px; }"
            "QPushButton:checked { background:#5b3fa6; color:#ffffff; font-weight:600; }")

    def mode(self) -> str:
        return "auto" if self._auto.isChecked() else "grimoire"

    def set_mode(self, mode: str) -> None:
        (self._auto if mode == "auto" else self._gri).setChecked(True)


class MonsterPanel(QWidget):
    """Embedded web area (monster info view + secondary "notes" view) + idle countdown.

    Per-game options make it work with ANY site:
      - `url_style`     : "path" (slug in path, one monster) or "search" (query term(s)).
      - `multi_joiner`  : how multiple monsters are joined for search style (e.g. " || ").
      - `requires_login`: monster view uses a persistent (logged-in) profile.
      - `notes_url`     : the secondary view shown by the 🔮 toggle (defaults to grimoire).
    The grimoire-specific CSS fix is applied only to grimoire URLs.
    """

    def __init__(self, url_template: str, slug_map: Optional[Dict[str, str]] = None,
                 url_style: str = "path", multi_joiner: str = " || ",
                 requires_login: bool = False, notes_url: str = "", parent=None) -> None:
        super().__init__(parent)
        self.url_template = url_template
        self.slug_map = slug_map or {}
        self.url_style = url_style
        self.multi_joiner = multi_joiner
        self.current: Optional[str] = None
        self._loaded_url: Optional[str] = None   # to avoid reloading the same URL

        self.setStyleSheet("QWidget { background:#15151b; color:#e8e8ec; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._countdown_label = QLabel()
        self._countdown_label.setStyleSheet(
            "font-size:11px; color:#5a5a63; padding:4px 10px;")
        self._countdown_label.setVisible(False)
        outer.addWidget(self._countdown_label)

        # stacked web area: index 0 = monster info view, index 1 = notes view
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        if _HAVE_WEBENGINE:
            # persistent profile keeps logins/cookies between sessions (for login sites)
            self._profile = QWebEngineProfile(_GRIMOIRE_PROFILE_NAME)
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
            )
            # index 0 — monster info view
            self.web = self._make_view(persistent=requires_login,
                                       grimoire_fix=is_grimoire(url_template))
            self.web.setHtml(_BLANK_HTML)
            self._stack.addWidget(self.web)

            # index 1 — secondary notes view (the 🔮 toggle); always logged in
            notes = notes_url or GRIMOIRE_URL
            self._grimoire_web = self._make_view(persistent=True,
                                                 grimoire_fix=is_grimoire(notes))
            self._grimoire_web.setUrl(QUrl(notes))
            self._stack.addWidget(self._grimoire_web)
        else:
            self.web = None
            self._grimoire_web = None
            self._profile = None
            fallback = QLabel("PyQt6-WebEngine not installed — cannot embed page.")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color:#c66;")
            self._stack.addWidget(fallback)

    def _make_view(self, persistent: bool, grimoire_fix: bool) -> "QWebEngineView":
        """A web view: persistent (logged-in) profile or the default; optional grimoire
        CSS fix; camera/mic granted so 'Insert Image from Camera' works on any site."""
        view = QWebEngineView()
        if persistent:
            view.setPage(QWebEnginePage(self._profile, view))
        page = view.page()
        if grimoire_fix:
            _inject_rotate_fix(page)
        try:
            page.featurePermissionRequested.connect(
                lambda origin, feature, p=page: self._grant_media_permission(p, origin, feature))
        except Exception:
            pass
        return view

    # -- camera/mic permission -------------------------------------------
    def _grant_media_permission(self, page, origin, feature) -> None:
        F = QWebEnginePage.Feature
        media = {F.MediaVideoCapture, F.MediaAudioCapture, F.MediaAudioVideoCapture}
        if feature in media:
            policy = QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
        else:
            policy = QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        page.setFeaturePermission(origin, feature, policy)

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
    def show_monsters(self, names: List[str]) -> None:
        """Show all detected monsters (combined for 'search'; the first for 'path')."""
        self.current = names[0] if names else None
        if self.web is None:
            return
        url = build_monster_url(self.url_template, names, self.url_style, self.slug_map,
                                self.multi_joiner)
        # Skip if the resulting page is unchanged — a different detection order, or a
        # confidence-level change, must NOT reload the page.
        if url == self._loaded_url:
            return
        self._loaded_url = url
        self.web.setUrl(QUrl(url)) if url else self.web.setHtml(_BLANK_HTML)

    def show_monster(self, name: str) -> None:
        """Focus a single monster (e.g. a button click)."""
        self.show_monsters([name] if name else [])
