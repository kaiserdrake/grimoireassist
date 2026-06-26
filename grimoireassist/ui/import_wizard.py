"""Import wizard: fetches raw markdown from the Grimoire API and saves
per-monster data + images locally.

Strategy
--------
Grimoire exposes a direct API endpoint for raw markdown:
  GET /api/note-files/<fileId>/raw

The fileId is extracted from the notes URL query string (?fileId=NN).
The fetch is made from within the authenticated QWebEngineView so the
session cookies are automatically included — no separate auth handling needed.

The raw markdown is then parsed in Python and any images referenced as
![alt](url) in table cells are downloaded via JS fetch with credentials.

Source format expected
----------------------
  # Game Title          →  _game_title metadata
  ## Monster Name       →  one entry per monster
  ### Section Name      →  subsection (optional; tables before any ### go to _root)
  | col | col |         →  table rows; two-column = key/value, any width OK
  ![alt](url)           →  image in a cell; downloaded to games/<id>/images/
"""
from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    _HAVE_WEBENGINE = True
except Exception:
    _HAVE_WEBENGINE = False

# JS that fetches the markdown API URL from within the authenticated page context
_FETCH_MD_JS = """
(async function() {{
  try {{
    const r = await fetch({url!r}, {{credentials: 'include'}});
    if (!r.ok) return JSON.stringify({{error: r.status + ' ' + r.statusText}});
    return await r.text();
  }} catch(e) {{ return JSON.stringify({{error: String(e)}}); }}
}})()
"""

# JS to download one image as a base64 data-URL
_FETCH_IMG_JS = """
(async function() {{
  try {{
    const r = await fetch({url!r}, {{credentials: 'include'}});
    const b = await r.blob();
    return await new Promise(res => {{
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => res(null);
      fr.readAsDataURL(b);
    }});
  }} catch(e) {{ return null; }}
}})()
"""

_GRIMOIRE_BASE = "https://grimoire.laeradsphere.com"
_IMG_RE   = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
_ICON_RE  = re.compile(r':icon\[([^\]]+)\]')   # :icon[elem_fire] → "Elem Fire"
_HTML_TAG = re.compile(r'<[^>]+>')             # strip residual HTML tags


def _clean_cell(text: str) -> str:
    """Normalise a table cell: resolve Grimoire shortcodes and strip HTML."""
    text = _ICON_RE.sub(lambda m: m.group(1).replace("_", " ").title(), text)
    text = _HTML_TAG.sub(" ", text)
    text = re.sub(r'[ \t]+', ' ', text).strip(" ,")
    return text


def _api_url_from_notes_url(notes_url: str) -> Optional[str]:
    """Derive the raw-markdown API URL from a Grimoire notes page URL.

    https://grimoire.laeradsphere.com/game/236/258/notes?fileId=14
    →  https://grimoire.laeradsphere.com/api/note-files/14/raw
    """
    try:
        parsed = urlparse(notes_url)
        params = parse_qs(parsed.query)
        file_id = params.get("fileId", [None])[0]
        if file_id:
            return f"{_GRIMOIRE_BASE}/api/note-files/{file_id}/raw"
    except Exception:
        pass
    return None


def _parse_markdown(md: str) -> Dict[str, Any]:
    """Parse Grimoire-style markdown into the data.json structure."""
    result: Dict[str, Any] = {}
    current_monster: Optional[str] = None
    current_section = "_root"

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#### "):
            if current_monster:
                current_section = s[5:].strip()
        elif s.startswith("### "):
            if current_monster:
                current_section = s[4:].strip()
        elif s.startswith("## "):
            current_monster = s[3:].strip()
            current_section = "_root"
            if current_monster:
                result[current_monster] = {}
        elif s.startswith("# "):
            result["_game_title"] = s[2:].strip()
            current_monster = None
            current_section = "_root"
        elif s.startswith("|") and current_monster is not None:
            cells = [c.strip() for c in s.strip("|").split("|")]
            # Skip separator rows  |---|---|
            if all(re.fullmatch(r"[-: ]+", c) for c in cells if c):
                continue
            row = []
            for cell in cells:
                imgs = _IMG_RE.findall(cell)
                text = _clean_cell(_IMG_RE.sub("", cell))
                row.append({"text": text,
                             "imgs": [url for _alt, url in imgs],
                             "img_paths": []})
            result[current_monster].setdefault(current_section, []).append(row)

    return result


def _safe_name(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", s.strip().lower())[:40]


class ImportWizard(QDialog):
    """Dialog that imports per-monster data from Grimoire via its raw markdown API."""

    import_done      = pyqtSignal(str)        # game_id
    _fetch_result    = pyqtSignal(str, str)   # (markdown, error) — thread → main

    def __init__(self, game_id: str, notes_url: str,
                 profile: Optional["QWebEngineProfile"],
                 save_dir: Path,
                 parent=None) -> None:
        super().__init__(parent)
        self.game_id = game_id
        self.save_dir = save_dir
        self._profile = profile
        self._raw: Dict[str, Any] = {}
        self._image_queue: List[tuple] = []
        self._total_images = 0

        # Derive the API URL from the notes URL
        self._api_url = _api_url_from_notes_url(notes_url or "")

        self.setWindowTitle("Import monster data")
        self.setMinimumWidth(480)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy())
        self.setStyleSheet(
            "QDialog { background:#15151b; }"
            "QLabel { color:#e8e8ec; }"
            "QLineEdit { background:#2a2a36; color:#e8e8ec; border:1px solid #3a3a50;"
            "  border-radius:4px; padding:4px 8px; }"
            "QPushButton { background:#2a2a36; color:#e8e8ec; border:none;"
            "  border-radius:6px; padding:6px 16px; }"
            "QPushButton:hover { background:#3a3a50; }"
            "QPushButton:disabled { color:#4a4a55; }")

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        lay.addWidget(QLabel(
            "Fetches raw markdown from the Grimoire API and saves it locally.\n"
            "You must be logged into Grimoire (open the notes view first)."))

        # API URL field — auto-filled from notes_url, editable as fallback
        url_row = QWidget()
        rl = QHBoxLayout(url_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        rl.addWidget(QLabel("API URL:"))
        self._url_edit = QLineEdit(self._api_url or "")
        self._url_edit.setPlaceholderText(
            f"{_GRIMOIRE_BASE}/api/note-files/<fileId>/raw")
        rl.addWidget(self._url_edit, 1)
        lay.addWidget(url_row)

        self._go_btn = QPushButton("Import")
        self._go_btn.clicked.connect(self._start_import)
        lay.addWidget(self._go_btn)

        self._status = QLabel("Ready" if self._api_url
                               else "Could not extract fileId from notes URL — enter the API URL manually")
        self._status.setStyleSheet("color:#9a9aa3; font-size:12px;")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { background:#2a2a36; border-radius:4px; height:8px; }"
            "QProgressBar::chunk { background:#5b3fa6; border-radius:4px; }")
        lay.addWidget(self._progress)

        # Hidden web view — only needed to make authenticated fetch() calls;
        # no visual content is shown to the user.
        if _HAVE_WEBENGINE:
            self._web = QWebEngineView()
            self._web.setFixedHeight(0)
            self._web.setVisible(False)
            if profile:
                self._web.setPage(QWebEnginePage(profile, self._web))
            self._web.load(QUrl(_GRIMOIRE_BASE))
            lay.addWidget(self._web)
        else:
            self._web = None

        close_row = QWidget()
        cl = QHBoxLayout(close_row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addStretch()
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        self._fetch_result.connect(self._on_markdown_fetched)
        cl.addWidget(self._close_btn)
        lay.addWidget(close_row)

    # ------------------------------------------------------------------ import

    def _start_import(self) -> None:
        api_url = self._url_edit.text().strip()
        if not api_url:
            self._status.setText("Please enter the API URL")
            return
        self._go_btn.setEnabled(False)
        self._status.setText(f"Fetching markdown from {api_url} …")

        def _fetch():
            try:
                req = urllib.request.Request(
                    api_url,
                    headers={"Accept": "text/plain, text/markdown, */*",
                             "User-Agent": "GrimoireAssist/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    text = resp.read().decode("utf-8")
                self._fetch_result.emit(text, "")
            except Exception as exc:
                self._fetch_result.emit("", str(exc))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_markdown_fetched(self, result: str, error: str) -> None:
        if error:
            self._status.setText(f"Fetch failed: {error}")
            self._go_btn.setEnabled(True)
            return
        if not result.strip():
            self._status.setText("Empty response — check the API URL.")
            self._go_btn.setEnabled(True)
            return
        self._process_markdown(result)

    def _process_markdown(self, md: str) -> None:
        self._raw = _parse_markdown(md)
        monster_count = sum(1 for k in self._raw if not k.startswith("_"))
        if monster_count == 0:
            self._status.setText(
                "Markdown fetched but no ## Monster sections found.\n"
                f"Preview: {md[:300]!r}")
            self._go_btn.setEnabled(True)
            return

        self._status.setText(
            f"Parsed {monster_count} monster(s). Downloading images…")
        self._image_queue = []
        self._collect_images()
        self._total_images = len(self._image_queue)
        if self._total_images:
            self._progress.setMaximum(self._total_images)
            self._progress.setValue(0)
            self._progress.setVisible(True)
            self._download_next_image()
        else:
            self._finish()

    # ------------------------------------------------------------------ images

    def _collect_images(self) -> None:
        for monster, sections in self._raw.items():
            if monster.startswith("_") or not isinstance(sections, dict):
                continue
            slug = _safe_name(monster)
            for sec_key, rows in sections.items():
                sec_name = _safe_name(sec_key)
                if not isinstance(rows, list):
                    continue
                for row_i, row in enumerate(rows):
                    for col_i, cell in enumerate(row):
                        if not isinstance(cell, dict):
                            continue
                        paths = []
                        for img_i, url in enumerate(cell.get("imgs", [])):
                            if not url or not url.startswith("http"):
                                continue
                            rel = (f"images/{slug}/"
                                   f"{sec_name}_{row_i}_{col_i}_{img_i}.png")
                            self._image_queue.append((rel, url))
                            paths.append(rel)
                        cell["img_paths"] = paths

    def _download_next_image(self) -> None:
        self._progress.setValue(self._total_images - len(self._image_queue))
        if not self._image_queue:
            self._finish()
            return
        rel_path, url = self._image_queue[0]
        self._web.page().runJavaScript(
            _FETCH_IMG_JS.format(url=url),
            lambda data: self._on_image_data(rel_path, data))

    def _on_image_data(self, rel_path: str, data_url: Optional[str]) -> None:
        self._image_queue.pop(0)
        if data_url and "," in data_url:
            try:
                dest = self.save_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
            except Exception:
                pass
        self._download_next_image()

    # ------------------------------------------------------------------ save

    def _finish(self) -> None:
        self._progress.setVisible(False)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        data_file = self.save_dir / "data.json"
        data_file.write_text(
            json.dumps(self._raw, ensure_ascii=False, indent=2),
            encoding="utf-8")
        monster_count = sum(1 for k in self._raw if not k.startswith("_"))
        self._status.setText(
            f"Done — {monster_count} monster(s) saved to {data_file.parent}")
        self._go_btn.setEnabled(True)
        self.import_done.emit(self.game_id)
