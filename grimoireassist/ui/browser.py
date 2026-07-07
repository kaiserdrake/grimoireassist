"""In-app tabbed web browser drawer.

Sits in the right pane of the main-window splitter: when open it takes 75%
of the width and the tracking/grimoire view keeps 25%; when closed the main
view fills the window. Tabs share one persistent QWebEngineProfile
("browser_persistent" — separate from the grimoire view's profile, which is
recreated on every game switch) so logins survive restarts.

New tabs show the current game's bookmarks, fetched from a raw-markdown
Grimoire endpoint (game.bookmarks_url). Bookmarks live under a heading:

    # Bookmarks

    * [link1](url)
    * [link2](url)

The list is re-fetched at startup, on game switch, and via the ★ Sync button.
"""
from __future__ import annotations

import html
import re
import threading
import urllib.request
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QTabWidget, QToolButton, QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    _HAVE_WEBENGINE = True
except Exception:  # pragma: no cover - WebEngine missing
    _HAVE_WEBENGINE = False

from .monster_panel import _inject_clipboard_fix

_BROWSER_PROFILE_NAME = "browser_persistent"


# ── bookmarks markdown ─────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_LINK_ITEM_RE = re.compile(r"^\s*[*+-]\s+\[([^\]]+)\]\(\s*(\S+?)\s*\)")


def parse_bookmarks(md: str) -> List[Tuple[str, str]]:
    """Extract (title, url) list items under the `# Bookmarks` heading.

    The section ends at the next heading of any level."""
    out: List[Tuple[str, str]] = []
    in_section = False
    for line in md.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            in_section = h.group(2).strip().lower() == "bookmarks"
            continue
        if not in_section:
            continue
        m = _LINK_ITEM_RE.match(line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip()))
    return out


def _bookmarks_html(bookmarks: List[Tuple[str, str]], hint: str = "") -> str:
    """Dark-themed new-tab page listing the game's bookmarks."""
    if bookmarks:
        items = "\n".join(
            f'<a class="bm" href="{html.escape(url, quote=True)}">'
            f'{html.escape(title)}<span class="url">{html.escape(url)}</span></a>'
            for title, url in bookmarks)
    else:
        items = f'<p class="hint">{html.escape(hint or "No bookmarks yet.")}</p>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  body {{ background:#15151b; color:#e8e8ec; margin:0;
         font-family:'Segoe UI',sans-serif; }}
  .wrap {{ max-width:560px; margin:48px auto; padding:0 24px; }}
  h1 {{ font-size:18px; color:#9a9aa3; font-weight:600;
        border-bottom:1px solid #2a2a36; padding-bottom:8px; }}
  a.bm {{ display:block; padding:10px 14px; margin:6px 0; border-radius:6px;
          background:#1a1a24; color:#e8e8ec; text-decoration:none;
          font-size:14px; }}
  a.bm:hover {{ background:#2a2a36; }}
  a.bm .url {{ display:block; color:#5a5a63; font-size:11px; margin-top:2px;
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .hint {{ color:#5a5a63; font-size:13px; }}
</style></head><body><div class="wrap">
  <h1>Bookmarks</h1>
  {items}
</div></body></html>"""


# ── web view ───────────────────────────────────────────────────────────────────

if _HAVE_WEBENGINE:

    class _WebView(QWebEngineView):
        """Tab view; routes window.open / target=_blank into a new tab."""

        def __init__(self, panel: "BrowserPanel") -> None:
            super().__init__()
            self._panel = panel
            self.is_bookmarks_page = False

        def createWindow(self, _type):  # noqa: N802 (Qt override)
            # Chromium sets the URL on the returned view after this call.
            return self._panel.new_tab(url=None, focus=True, blank=True)


# ── panel ──────────────────────────────────────────────────────────────────────

class BrowserPanel(QWidget):
    """Tabbed browser drawer with a nav bar and per-game bookmarks."""

    status_message = pyqtSignal(str)
    # (seq, bookmarks, error) — emitted from the fetch thread, handled on the
    # UI thread via the queued connection Qt uses for cross-thread signals.
    _bookmarks_fetched = pyqtSignal(int, list, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._profile = None
        self._bookmarks: List[Tuple[str, str]] = []
        self._bookmarks_url: str = ""
        self._sync_seq = 0          # ignore results from stale fetches
        self._bookmarks_fetched.connect(self._on_bookmarks_fetched)

        # plain QWidgets only paint stylesheet backgrounds with this attribute
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "BrowserPanel { background:#15151b; }"
            "QWidget#navbar { background:#1a1a24;"
            "  border-bottom:1px solid #2a2a36; }"
            "QToolButton { background:transparent; color:#9a9aa3; border:none;"
            "  border-radius:4px; padding:3px 8px; font-size:14px; }"
            "QToolButton:hover { background:#2a2a36; color:#e8e8ec; }"
            "QLineEdit { background:#15151b; color:#e8e8ec;"
            "  border:1px solid #2a2a36; border-radius:4px; padding:3px 8px;"
            "  font-size:12px; }"
            "QLineEdit:focus { border-color:#5b3fa6; }"
            "QTabWidget::pane { border:none; }"
            "QTabBar { background:#15151b; }"
            "QTabBar::tab { background:#1a1a24; color:#9a9aa3; padding:5px 10px;"
            "  border:1px solid #2a2a36; border-bottom:none; margin-right:2px;"
            "  border-top-left-radius:4px; border-top-right-radius:4px;"
            "  max-width:180px; font-size:11px; }"
            "QTabBar::tab:selected { background:#2a2a36; color:#e8e8ec; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── nav bar ─────────────────────────────────────────────────────
        navbar = QWidget()
        navbar.setObjectName("navbar")
        navbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav = QHBoxLayout(navbar)
        nav.setContentsMargins(6, 4, 6, 4)
        nav.setSpacing(4)

        def _btn(text: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            nav.addWidget(b)
            return b

        self._back_btn = _btn("◀", "Back", self._go_back)
        self._fwd_btn = _btn("▶", "Forward", self._go_forward)
        self._reload_btn = _btn("⟳", "Reload", self._reload)

        self._url_bar = QLineEdit()
        self._url_bar.setPlaceholderText("Enter address…")
        self._url_bar.setClearButtonEnabled(True)
        self._url_bar.returnPressed.connect(self._navigate)
        nav.addWidget(self._url_bar, 1)

        self._newtab_btn = _btn("＋", "New tab (Ctrl+T)", lambda: self.new_tab())
        self._sync_btn = _btn("★", "Sync game bookmarks",
                              lambda: self.sync_bookmarks())
        outer.addWidget(navbar)

        # ── tabs / fallback ─────────────────────────────────────────────
        if _HAVE_WEBENGINE:
            self._tabs = QTabWidget()
            self._tabs.setTabsClosable(True)
            self._tabs.setMovable(True)
            self._tabs.setDocumentMode(True)
            self._tabs.setElideMode(Qt.TextElideMode.ElideRight)
            self._tabs.tabCloseRequested.connect(self.close_tab)
            self._tabs.currentChanged.connect(self._on_current_changed)
            outer.addWidget(self._tabs, 1)
        else:
            self._tabs = None
            fallback = QLabel("PyQt6-WebEngine not installed — browser unavailable.")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color:#c66;")
            outer.addWidget(fallback, 1)

        # shortcuts only fire while the drawer has focus
        for seq, slot in (("Ctrl+T", self.new_tab),
                          ("Ctrl+W", self._close_current_tab),
                          ("Ctrl+L", self.focus_url_bar)):
            sc = QShortcut(QKeySequence(seq), self, activated=slot)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    # ── lifecycle ───────────────────────────────────────────────────────
    def ensure_ready(self) -> None:
        """Create the profile and first tab on first open (idempotent)."""
        if not _HAVE_WEBENGINE:
            return
        self._ensure_profile()
        if self._tabs.count() == 0:
            self.new_tab(focus=True)

    def _ensure_profile(self) -> None:
        if self._profile is None:
            # A *named* profile is persistent (disk cache + storage); keep it
            # distinct from the grimoire view's profile, which is recreated on
            # every game switch and would contend for the same storage dir.
            self._profile = QWebEngineProfile(_BROWSER_PROFILE_NAME, self)
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)

    # ── tabs ────────────────────────────────────────────────────────────
    def new_tab(self, url: Optional[str] = None, focus: bool = True,
                blank: bool = False):
        """Open a tab. With no url it shows the bookmarks page (or a blank
        page when `blank`, used for createWindow targets)."""
        if not _HAVE_WEBENGINE:
            return None
        self._ensure_profile()
        view = self._make_view()
        idx = self._tabs.addTab(view, "New Tab")
        if url:
            view.setUrl(QUrl(url))
        elif not blank:
            self._show_bookmarks_page(view)
        if focus:
            self._tabs.setCurrentIndex(idx)
        return view

    def close_tab(self, index: int) -> None:
        if self._tabs is None:
            return
        view = self._tabs.widget(index)
        self._tabs.removeTab(index)
        if view is not None:
            view.deleteLater()
        if self._tabs.count() == 0:
            self.new_tab(focus=True)

    def _close_current_tab(self) -> None:
        if self._tabs is not None and self._tabs.count():
            self.close_tab(self._tabs.currentIndex())

    def current_view(self):
        return self._tabs.currentWidget() if self._tabs is not None else None

    def focus_url_bar(self) -> None:
        self._url_bar.setFocus()
        self._url_bar.selectAll()

    def _make_view(self):
        view = _WebView(self)
        view.setPage(QWebEnginePage(self._profile, view))
        page = view.page()
        _inject_clipboard_fix(page)
        try:
            from PyQt6.QtWebEngineCore import QWebEngineSettings
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        except Exception:
            pass
        try:
            page.featurePermissionRequested.connect(
                lambda origin, feature, p=page:
                    self._grant_permission(p, origin, feature))
        except Exception:
            pass
        view.titleChanged.connect(lambda t, v=view: self._on_title_changed(v, t))
        view.iconChanged.connect(lambda i, v=view: self._on_icon_changed(v, i))
        view.urlChanged.connect(lambda u, v=view: self._on_url_changed(v, u))
        return view

    def _grant_permission(self, page, origin, feature) -> None:
        F = QWebEnginePage.Feature
        granted = {F.MediaVideoCapture, F.MediaAudioCapture,
                   F.MediaAudioVideoCapture}
        try:
            granted.add(F.ClipboardReadWrite)
        except AttributeError:
            pass  # Qt < 6.2
        policy = (QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
                  if feature in granted
                  else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser)
        page.setFeaturePermission(origin, feature, policy)

    # ── per-view signals ────────────────────────────────────────────────
    def _on_title_changed(self, view, title: str) -> None:
        idx = self._tabs.indexOf(view)
        if idx >= 0 and title:
            self._tabs.setTabText(idx, title)
            self._tabs.setTabToolTip(idx, title)

    def _on_icon_changed(self, view, icon) -> None:
        idx = self._tabs.indexOf(view)
        if idx >= 0:
            self._tabs.setTabIcon(idx, icon)

    def _on_url_changed(self, view, url: QUrl) -> None:
        if url.scheme() in ("http", "https"):
            view.is_bookmarks_page = False
        if view is self.current_view():
            self._sync_url_bar(view)

    def _on_current_changed(self, _index: int) -> None:
        self._sync_url_bar(self.current_view())

    def _sync_url_bar(self, view) -> None:
        if view is None:
            self._url_bar.clear()
            return
        url = view.url()
        text = url.toString() if url.scheme() in ("http", "https") else ""
        self._url_bar.setText(text)
        self._url_bar.setCursorPosition(0)

    # ── navigation ──────────────────────────────────────────────────────
    def _navigate(self) -> None:
        text = self._url_bar.text().strip()
        view = self.current_view()
        if not text or view is None:
            return
        view.setUrl(QUrl.fromUserInput(text))
        view.setFocus()

    def _go_back(self) -> None:
        if self.current_view() is not None:
            self.current_view().back()

    def _go_forward(self) -> None:
        if self.current_view() is not None:
            self.current_view().forward()

    def _reload(self) -> None:
        if self.current_view() is not None:
            self.current_view().reload()

    # ── bookmarks ───────────────────────────────────────────────────────
    def set_game_bookmarks(self, url: str) -> None:
        """Point the drawer at a game's bookmarks note and fetch it.

        Called at startup and on every game switch."""
        self._bookmarks_url = (url or "").strip()
        self._bookmarks = []
        if self._bookmarks_url:
            self.sync_bookmarks()
        else:
            self._refresh_bookmark_tabs()

    def sync_bookmarks(self) -> None:
        """Fetch the bookmarks markdown in the background."""
        url = self._bookmarks_url
        if not url:
            self.status_message.emit(
                "No bookmarks URL for this game — set one in the game entry.")
            self._refresh_bookmark_tabs()
            return
        self._sync_seq += 1
        seq = self._sync_seq

        def _fetch() -> None:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "GrimoireAssist"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
            except Exception as exc:
                self._bookmarks_fetched.emit(seq, [], str(exc))
                return
            self._bookmarks_fetched.emit(seq, parse_bookmarks(text), "")

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_bookmarks_fetched(self, seq: int, bookmarks: list, error: str) -> None:
        if seq != self._sync_seq:
            return  # a newer sync (e.g. game switch) superseded this one
        if error:
            # keep the previous list; just report the failure
            self.status_message.emit(f"Bookmark sync failed: {error}")
            return
        self._bookmarks = list(bookmarks)
        self.status_message.emit(
            f"Bookmarks synced: {len(self._bookmarks)} link(s)")
        self._refresh_bookmark_tabs()

    def _show_bookmarks_page(self, view) -> None:
        view.is_bookmarks_page = True
        hint = ("No bookmarks URL for this game — add one in the game entry, "
                "then press ★ to sync."
                if not self._bookmarks_url else
                "No bookmarks found under a '# Bookmarks' heading — "
                "press ★ to sync again.")
        view.setHtml(_bookmarks_html(self._bookmarks, hint))
        idx = self._tabs.indexOf(view)
        if idx >= 0:
            self._tabs.setTabText(idx, "Bookmarks")

    def _refresh_bookmark_tabs(self) -> None:
        """Re-render tabs still showing the bookmarks page after a sync."""
        if self._tabs is None:
            return
        for i in range(self._tabs.count()):
            view = self._tabs.widget(i)
            if getattr(view, "is_bookmarks_page", False):
                self._show_bookmarks_page(view)
