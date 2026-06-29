"""Import wizard: fetches raw markdown from the Grimoire API and saves
per-monster data + images locally.

Strategy
--------
Grimoire exposes a direct API endpoint for raw markdown:
  GET /api/note-files/<fileId>/raw

The fileId is extracted from the notes URL query string (?fileId=NN).
Markdown is fetched via urllib (unauthenticated; Grimoire's raw API is public).
Images referenced as ![alt](url) are downloaded via urllib in a background
thread and saved to games/<id>/import/images/.

Source format expected
----------------------
  # Game Title          →  _game_title metadata
  ## Monster Name       →  one entry per monster
  ### Section Name      →  subsection with optional :::meta type: xxx ::: block
  **Key:** value        →  key-value pair (key or value may contain ![img](url))
  | col | col |         →  legacy table rows (still supported)
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from PyQt6.QtCore import QTimer, QUrl, pyqtSignal
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

_GRIMOIRE_BASE = "https://grimoire.laeradsphere.com"
_IMG_RE    = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
_ICON_RE   = re.compile(r':icon\[([^\]]+)\]')   # :icon[elem_fire] → "Elem Fire"
_HTML_TAG  = re.compile(r'<[^>]+>')             # strip residual HTML tags
_BOLD_KV_RE = re.compile(r'^\*\*(.+?)\*\*\s*(.*)', re.DOTALL)


def _clean_cell(text: str) -> str:
    """Normalise a table cell: resolve Grimoire shortcodes and strip all HTML."""
    text = _ICON_RE.sub(lambda m: m.group(1).replace("_", " ").title(), text)
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
    text = _HTML_TAG.sub("", text)
    text = re.sub(r'[ \t]+', ' ', text).strip(" ,")
    return text


def _clean_value_html(text: str) -> str:
    """Normalise a value cell, preserving <span color> for rich-text rendering.

    Strips everything except span tags that carry a color style — these are
    used for weakness ratings (red = worse, lightblue/green = better).
    """
    text = _ICON_RE.sub(lambda m: m.group(1).replace("_", " ").title(), text)
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)

    def _keep_color(m: re.Match) -> str:
        style = m.group(1)
        cm = re.search(r'color\s*:\s*([^;",]+)', style)
        return f'<span style="color:{cm.group(1).strip()}">' if cm else ''

    text = re.sub(r'<span\b[^>]*style="([^"]*)"[^>]*>', _keep_color,
                  text, flags=re.IGNORECASE)
    # Remove all HTML except the span tags we just simplified
    text = re.sub(r'<(?!/?span\b)[^>]+>', '', text)
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


def _sec_append(result: Dict, monster: str, section: str,
                sec_type: Optional[str], row: list) -> None:
    """Append a row to a monster section, creating it with type metadata if needed."""
    existing = result[monster].get(section)
    if existing is None:
        if sec_type:
            result[monster][section] = {"_type": sec_type, "rows": [row]}
        else:
            result[monster][section] = [row]
    elif isinstance(existing, dict):
        existing["rows"].append(row)
    else:
        existing.append(row)


def _parse_markdown(md: str) -> Dict[str, Any]:
    """Parse Grimoire-style markdown into the data.json structure.

    Sections may carry a type hint inside a :::meta … ::: directive block:
      type: key-value-pair   →  **Key:** value lines rendered as key : value
      type: table-col-row    →  keys become column headers, values become a single row
      type: table-row-col    →  first **…** line is column headers, rest are data rows
    Legacy | table | rows are still supported for older data.
    """
    result: Dict[str, Any] = {}
    current_monster: Optional[str] = None
    current_section = "_root"
    current_section_type: Optional[str] = None
    in_front_matter = False
    front_matter_buf: List[str] = []
    in_code_block = False

    for line in md.splitlines():
        s = line.strip()

        # Fenced code blocks (``` … ```) — ignore everything inside, including
        # any metadata-looking content, so it isn't parsed as real data.
        if s.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Meta directive block delimiters (:::meta … :::)
        if s == ":::meta" and not in_front_matter:
            in_front_matter = True
            front_matter_buf = []
            continue
        if s == ":::" and in_front_matter:
            in_front_matter = False
            for fm in front_matter_buf:
                if fm.startswith("type:"):
                    current_section_type = fm[5:].strip()
                    # Pre-create section entry so _type is stored even before rows
                    if current_monster is not None:
                        result[current_monster].setdefault(
                            current_section,
                            {"_type": current_section_type, "rows": []})
            continue

        if in_front_matter:
            front_matter_buf.append(s)
            continue

        if s.startswith("#### "):
            if current_monster:
                current_section = s[5:].strip()
                current_section_type = None
        elif s.startswith("### "):
            if current_monster:
                current_section = s[4:].strip()
                current_section_type = None
        elif s.startswith("## "):
            current_monster = s[3:].strip()
            current_section = "_root"
            current_section_type = None
            if current_monster:
                result[current_monster] = {}
        elif s.startswith("Image:") and current_monster is not None:
            url = s[6:].strip()
            if url:
                result[current_monster]["_image_url"] = url
        elif s.startswith("# "):
            result["_game_title"] = s[2:].strip()
            current_monster = None
            current_section = "_root"
            current_section_type = None
        elif s.startswith("**") and current_monster is not None:
            # Key-value line: **Key:** value  or  **![icon](url):** value
            m = _BOLD_KV_RE.match(s)
            if not m:
                continue
            key_raw = m.group(1).rstrip(":")
            val_raw = m.group(2).strip()
            key_imgs = _IMG_RE.findall(key_raw)
            key_text = _clean_cell(_IMG_RE.sub("", key_raw))

            # Split <br>-separated values into sub_items
            br_parts = re.split(r'<br\s*/?>', val_raw, flags=re.IGNORECASE)
            if len(br_parts) > 1:
                sub_items = []
                for part in br_parts:
                    part = part.strip()
                    if not part:
                        continue
                    p_imgs = _IMG_RE.findall(part)
                    p_text = _clean_value_html(_IMG_RE.sub("", part))
                    sub_items.append({
                        "text": p_text,
                        "imgs": [u for _, u in p_imgs],
                        "img_paths": [],
                    })
                val_cell = {"text": "", "imgs": [], "img_paths": [],
                            "sub_items": sub_items}
            else:
                val_imgs = _IMG_RE.findall(val_raw)
                val_text = _clean_value_html(_IMG_RE.sub("", val_raw))
                val_cell = {"text": val_text,
                            "imgs": [u for _, u in val_imgs], "img_paths": []}

            row = [
                {"text": key_text, "imgs": [u for _, u in key_imgs], "img_paths": []},
                val_cell,
            ]
            _sec_append(result, current_monster, current_section,
                        current_section_type, row)
        elif s.startswith("|") and current_monster is not None:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r"[-: ]+", c) for c in cells if c):
                continue
            row = []
            for cell in cells:
                imgs = _IMG_RE.findall(cell)
                text = _clean_cell(_IMG_RE.sub("", cell))
                row.append({"text": text,
                             "imgs": [url for _alt, url in imgs],
                             "img_paths": []})
            _sec_append(result, current_monster, current_section, None, row)

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
            "Fetches monster data from the Grimoire API and saves it locally.\n"
            "You must be logged into Grimoire (open the notes view first)."))

        # Manual URL entry — only shown when auto-derivation fails
        self._url_row = QWidget()
        rl = QHBoxLayout(self._url_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        rl.addWidget(QLabel("API URL:"))
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(
            f"{_GRIMOIRE_BASE}/api/note-files/<fileId>/raw")
        rl.addWidget(self._url_edit, 1)
        lay.addWidget(self._url_row)

        self._go_btn = QPushButton("Import")
        self._go_btn.clicked.connect(self._start_import)
        lay.addWidget(self._go_btn)

        self._status = QLabel("Ready")
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
        api_url = self._url_edit.text().strip() or self._api_url
        if not api_url:
            self._status.setText("Please enter the API URL.")
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
        """Walk all cells, assign each unique icon URL a shared path under
        images/icons/ and queue it for download exactly once.
        Also queue monster portrait images stored under _image_url."""
        url_to_rel: dict = {}   # url -> rel_path  (dedup across all monsters)
        for monster, sections in self._raw.items():
            if monster.startswith("_") or not isinstance(sections, dict):
                continue
            # Monster portrait image
            portrait_url = sections.get("_image_url", "")
            if portrait_url and portrait_url.startswith("http"):
                slug = _safe_name(monster)
                rel = f"images/monsters/{slug}.png"
                self._image_queue.append((rel, portrait_url))
                sections["_image_path"] = rel
            for sec_val in sections.values():
                if isinstance(sec_val, dict):
                    rows = sec_val.get("rows", [])
                elif isinstance(sec_val, list):
                    rows = sec_val
                else:
                    continue
                for row in rows:
                    for cell in row:
                        if not isinstance(cell, dict):
                            continue
                        cell["img_paths"] = self._queue_imgs(
                            cell.get("imgs", []), url_to_rel)
                        for sub in cell.get("sub_items", []):
                            sub["img_paths"] = self._queue_imgs(
                                sub.get("imgs", []), url_to_rel)

    def _queue_imgs(self, urls: list, url_to_rel: dict) -> list:
        """Map a list of image URLs to shared rel-paths, queuing new ones."""
        paths = []
        for url in urls:
            if not url or not url.startswith("http"):
                continue
            if url not in url_to_rel:
                fname = url.rsplit("/", 1)[-1].split("?")[0] or "icon.png"
                rel = f"images/icons/{fname}"
                url_to_rel[url] = rel
                self._image_queue.append((rel, url))
            paths.append(url_to_rel[url])
        return paths

    def _download_next_image(self) -> None:
        self._progress.setValue(self._total_images - len(self._image_queue))
        if not self._image_queue:
            self._finish()
            return
        rel_path, url = self._image_queue.pop(0)
        dest = self.save_dir / rel_path

        # Skip if already cached on disk
        if dest.exists():
            QTimer.singleShot(0, self._download_next_image)
            return

        save_dir = self.save_dir

        def _fetch():
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 GrimoireAssist/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = r.read()
                dest = save_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                if rel_path.endswith(".png"):
                    # Convert any format (including AVIF) to PNG via Pillow
                    try:
                        import io
                        from PIL import Image
                        img = Image.open(io.BytesIO(data)).convert("RGBA")
                        buf = io.BytesIO()
                        img.save(buf, "PNG")
                        dest.write_bytes(buf.getvalue())
                    except Exception:
                        dest.write_bytes(data)
                else:
                    dest.write_bytes(data)
            except Exception:
                pass
            QTimer.singleShot(0, self._download_next_image)

        threading.Thread(target=_fetch, daemon=True).start()

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
