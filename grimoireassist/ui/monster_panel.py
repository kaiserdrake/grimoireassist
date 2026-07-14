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

from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve, QRectF, Qt, QUrl, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..ocr import conf_level
from .monster_card import MonsterCardGroup

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

_shared_profile: "Optional[QWebEngineProfile]" = None


def shared_grimoire_profile() -> "QWebEngineProfile":
    """The persistent (logged-in) grimoire profile, shared by every
    MonsterPanel. One long-lived instance: MonsterPanel is rebuilt on each
    game switch, and two live same-named profiles over one storage directory
    is unsupported by Qt (cookie persistence can silently break)."""
    global _shared_profile
    if _shared_profile is None:
        _shared_profile = QWebEngineProfile(_GRIMOIRE_PROFILE_NAME)
        _shared_profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
    return _shared_profile


def _inject_clipboard_fix(page) -> None:
    """Patch navigator.clipboard.writeText so it falls back to execCommand when
    the Chromium clipboard IPC is blocked inside Qt WebEngine."""
    from PyQt6.QtWebEngineCore import QWebEngineScript
    js = """
(function () {
  if (window.__gaClipboardPatched) return;
  window.__gaClipboardPatched = true;

  function _execCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch(e) {}
    document.body.removeChild(ta);
    return ok ? Promise.resolve() : Promise.reject(new Error('execCommand copy failed'));
  }

  var _orig = (navigator.clipboard || {}).writeText;
  Object.defineProperty(navigator, 'clipboard', {
    value: Object.assign({}, navigator.clipboard, {
      writeText: function (text) {
        var p = _orig ? _orig.call(navigator.clipboard, text) : Promise.reject();
        return p.catch(function () { return _execCopy(text); });
      }
    }),
    configurable: true, writable: false
  });
})();
"""
    script = QWebEngineScript()
    script.setName("ga-clipboard-fix")
    script.setSourceCode(js)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    page.scripts().insert(script)


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
    """Sliding two-position toggle: 'Auto Switch' vs 'Grimoire'.

    Auto Switch follows detections (tracking view while monsters are seen,
    Grimoire after the idle timeout); Grimoire pins the Grimoire view
    regardless of detections. A highlight slides behind the active label."""

    mode_changed = pyqtSignal(str)  # "auto" | "grimoire"

    _LABELS = ("Auto Switch", "Grimoire")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = "auto"
        self._knob = 0.0   # highlight position: 0 = Auto Switch, 1 = Grimoire
        self._anim = QVariantAnimation(self, duration=140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        f = self.font()
        f.setPixelSize(12)
        self.setFont(f)
        fm = self.fontMetrics()
        # each half fits the widest label (bold, so the active state doesn't clip)
        f.setBold(True)
        self._half = max(QFontMetrics(f).horizontalAdvance(t)
                         for t in self._LABELS) + 24
        self.setFixedSize(self._half * 2, 24)

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(0.0 if mode == "auto" else 1.0)
        self._anim.start()

    def _select(self, mode: str) -> None:
        if mode != self._mode:
            self.set_mode(mode)
            self.mode_changed.emit(mode)

    def _on_anim(self, value) -> None:
        self._knob = float(value)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._select(
                "auto" if event.position().x() < self._half else "grimoire")

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self._select("grimoire" if self._mode == "auto" else "auto")
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2a2a36"))
        p.drawRoundedRect(r, radius, radius)
        knob = QRectF(self._knob * self._half, 0, self._half, r.height())
        p.setBrush(QColor("#5b3fa6"))
        p.drawRoundedRect(knob.adjusted(2, 2, -2, -2), radius - 2, radius - 2)
        active = 0 if self._mode == "auto" else 1
        f = p.font()
        for i, text in enumerate(self._LABELS):
            seg = QRectF(i * self._half, 0, self._half, r.height())
            f.setBold(i == active)
            p.setFont(f)
            p.setPen(QColor("#ffffff") if i == active else QColor("#cfcfd6"))
            p.drawText(seg, Qt.AlignmentFlag.AlignCenter, text)


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
                 requires_login: bool = False, notes_url: str = "",
                 imported_data: Optional[Dict] = None,
                 image_base: Optional[Path] = None,
                 cards_per_row: int = 4,
                 parent=None) -> None:
        super().__init__(parent)
        self.url_template = url_template
        self.slug_map = slug_map or {}
        self.url_style = url_style
        self.multi_joiner = multi_joiner
        self.current: Optional[str] = None
        self._loaded_url: Optional[str] = None
        self._imported: Dict = imported_data or {}
        self._grimoire_visible = False   # grimoire view pinned on top of the stack

        self.setStyleSheet("QWidget { background:#15151b; color:#e8e8ec; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._countdown_label = QLabel()
        self._countdown_label.setStyleSheet(
            "font-size:11px; color:#5a5a63; padding:4px 10px;")
        self._countdown_label.setVisible(False)
        outer.addWidget(self._countdown_label)

        # Stack layout:
        #   index 0 — MonsterCardGroup (local data, instant; always present)
        #   index 1 — Grimoire notes view (🔮 toggle; unchanged)
        #   index 2 — On-demand web view (Full page / fallback when no import)
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, 1)

        # index 0 — local cards
        self._card_group = MonsterCardGroup(cols=cards_per_row)
        self._card_group.set_image_base(image_base)
        self._card_group.open_web.connect(self._open_web_for_monster)
        self._stack.addWidget(self._card_group)

        if _HAVE_WEBENGINE:
            self._profile = shared_grimoire_profile()
            # index 1 — Grimoire notes (unchanged)
            notes = notes_url or GRIMOIRE_URL
            self._grimoire_web = self._make_view(persistent=True,
                                                 grimoire_fix=is_grimoire(notes))
            self._grimoire_web.setUrl(QUrl(notes))
            self._stack.addWidget(self._grimoire_web)

            # index 2 — on-demand monster info web view
            self.web = self._make_view(persistent=requires_login,
                                       grimoire_fix=is_grimoire(url_template))
            self.web.setHtml(_BLANK_HTML)
            self._stack.addWidget(self.web)
        else:
            self._grimoire_web = None
            self._profile = None
            self.web = None
            fallback = QLabel("PyQt6-WebEngine not installed — cannot embed page.")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color:#c66;")
            self._stack.addWidget(fallback)

    def _make_view(self, persistent: bool, grimoire_fix: bool) -> "QWebEngineView":
        """A web view: persistent (logged-in) profile or the default; optional grimoire
        CSS fix; camera/mic and clipboard granted so in-page actions work."""
        view = QWebEngineView()
        if persistent:
            view.setPage(QWebEnginePage(self._profile, view))
        page = view.page()
        if grimoire_fix:
            _inject_rotate_fix(page)
        _inject_clipboard_fix(page)

        # Allow JS clipboard access (navigator.clipboard API + execCommand fallback)
        try:
            from PyQt6.QtWebEngineCore import QWebEngineSettings
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        except Exception:
            pass

        try:
            page.featurePermissionRequested.connect(
                lambda origin, feature, p=page: self._grant_permission(p, origin, feature))
        except Exception:
            pass
        return view

    # -- permissions -----------------------------------------------------
    def _grant_permission(self, page, origin, feature) -> None:
        F = QWebEnginePage.Feature
        # Build the set of features we grant; ClipboardReadWrite added when available
        granted = {F.MediaVideoCapture, F.MediaAudioCapture, F.MediaAudioVideoCapture}
        try:
            granted.add(F.ClipboardReadWrite)
        except AttributeError:
            pass  # Qt < 6.2
        policy = (QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
                  if feature in granted
                  else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
        page.setFeaturePermission(origin, feature, policy)

    # -- grimoire toggle (called by MainWindow toolbar button) -----------
    def set_grimoire_visible(self, visible: bool) -> None:
        self._grimoire_visible = visible
        if visible:
            self._stack.setCurrentIndex(1)   # grimoire notes
        else:
            self._show_monster_view()

    def set_countdown(self, seconds: Optional[int]) -> None:
        if seconds is None:
            self._countdown_label.setVisible(False)
        else:
            self._countdown_label.setText(
                f"Transitioning view in {seconds}s of no object detected")
            self._countdown_label.setVisible(True)

    def open_grimoire_url(self, url: str) -> None:
        if self._grimoire_web is not None:
            self._grimoire_web.setUrl(QUrl(url))
        self.set_grimoire_visible(True)

    # -- local card display ----------------------------------------------
    def show_monsters(self, names: List[str]) -> None:
        """Show detected monsters. Uses local cards if imported data exists,
        otherwise falls back to the web view. While the Grimoire view is
        pinned (set_grimoire_visible(True)), content still updates in the
        background but the visible view is not switched."""
        self.current = names[0] if names else None

        # Always update the card group (shows placeholder card if no import)
        self._card_group.show_monsters(names, self._imported)

        # If no imported data at all, fall back to web view at index 2
        if not self._imported and names and self.web is not None:
            url = build_monster_url(self.url_template, names, self.url_style,
                                    self.slug_map, self.multi_joiner)
            if url != self._loaded_url:
                self._loaded_url = url
                self.web.setUrl(QUrl(url)) if url else self.web.setHtml(_BLANK_HTML)
        if not self._grimoire_visible:
            self._show_monster_view()

    def _show_monster_view(self) -> None:
        """Pick the non-grimoire view: web fallback when there is no imported
        data but a monster is detected, local cards otherwise."""
        if not self._imported and self.current and self.web is not None:
            self._stack.setCurrentIndex(2)
        else:
            self._stack.setCurrentIndex(0)

    def show_monster(self, name: str) -> None:
        self.show_monsters([name] if name else [])

    def _open_web_for_monster(self, name: str) -> None:
        """Open the web view for a single monster (called by 'Full page' button)."""
        if self.web is None:
            return
        url = build_monster_url(self.url_template, [name], self.url_style,
                                self.slug_map, self.multi_joiner)
        if url:
            self.web.setUrl(QUrl(url))
            self._loaded_url = url
        else:
            self.web.setHtml(_BLANK_HTML)
        self._stack.setCurrentIndex(2)

    def update_imported_data(self, data: dict, image_base: Optional[Path] = None) -> None:
        """Refresh local data after a re-import without rebuilding the panel."""
        self._imported = data
        self._card_group.set_image_base(image_base)
