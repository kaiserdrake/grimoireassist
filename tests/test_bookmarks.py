"""Grimoire focus-README bookmarks: URL construction + markdown parsing."""
import pytest

from grimoireassist.config import Config, GrimoireConfig


def test_readme_url_built_from_base_and_user():
    g = GrimoireConfig(base_url="https://grimoire.example.com", user="kaiserdrake")
    assert g.readme_raw_url() == (
        "https://grimoire.example.com/api/focus/readme/raw?user=kaiserdrake")


def test_readme_url_trims_trailing_slash_and_quotes_user():
    g = GrimoireConfig(base_url="https://grimoire.example.com/", user="a b/c")
    assert g.readme_raw_url() == (
        "https://grimoire.example.com/api/focus/readme/raw?user=a%20b%2Fc")


@pytest.mark.parametrize("base,user", [
    ("https://grimoire.example.com", ""),
    ("https://grimoire.example.com", "   "),
    ("", "kaiserdrake"),
])
def test_readme_url_empty_when_unconfigured(base, user):
    assert GrimoireConfig(base_url=base, user=user).readme_raw_url() == ""


def test_grimoire_section_round_trips_through_config(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.grimoire.user = "kaiserdrake"
    cfg.save(path)
    reloaded = Config.load(path)
    assert reloaded.grimoire.user == "kaiserdrake"
    assert reloaded.grimoire.base_url == "https://grimoire.laeradsphere.com"


def test_grimoire_defaults_when_section_missing():
    cfg = Config.from_dict({})
    assert cfg.grimoire.user == ""
    assert cfg.grimoire.readme_raw_url() == ""


def test_parse_bookmarks_reads_only_the_bookmarks_section():
    from grimoireassist.ui.browser import parse_bookmarks
    md = """# Notes

* [not a bookmark](https://example.com/nope)

# Bookmarks

* [Tier List](https://example.com/tiers)
- [Builds](https://example.com/builds)

# To do next

* [also not a bookmark](https://example.com/nope2)
"""
    assert parse_bookmarks(md) == [
        ("Tier List", "https://example.com/tiers"),
        ("Builds", "https://example.com/builds"),
    ]


# ── sync triggers (offscreen Qt) ───────────────────────────────────────────────

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app, monkeypatch):
    """A BrowserPanel whose fetches are counted instead of hitting the network."""
    from grimoireassist.ui import browser as browser_mod

    cfg = Config()
    cfg.grimoire.user = "kaiserdrake"
    p = browser_mod.BrowserPanel(cfg=cfg)
    p.calls = []

    def fake_thread(target=None, daemon=None):
        p.calls.append(p._bookmarks_url)

        class _NoopThread:
            def start(self_inner):
                pass
        return _NoopThread()

    monkeypatch.setattr(browser_mod.threading, "Thread", fake_thread)
    return p


def test_sync_uses_the_configured_user(panel):
    panel.sync_bookmarks()
    assert panel.calls == [
        "https://grimoire.laeradsphere.com/api/focus/readme/raw?user=kaiserdrake"]


def test_sync_rereads_url_after_user_changes(panel):
    panel.sync_bookmarks()
    panel._cfg.grimoire.user = "someone-else"
    panel.sync_bookmarks()
    assert panel.calls[-1].endswith("?user=someone-else")


def test_view_triggered_syncs_are_throttled(panel):
    panel.sync_bookmarks(force=False)
    panel.sync_bookmarks(force=False)
    panel.sync_bookmarks(force=False)
    assert len(panel.calls) == 1, "burst of view triggers should collapse to one"


def test_throttle_expires(panel, monkeypatch):
    from grimoireassist.ui import browser as browser_mod
    panel.sync_bookmarks(force=False)
    now = [browser_mod.time.monotonic() + browser_mod._SYNC_MIN_INTERVAL_S + 1]
    monkeypatch.setattr(browser_mod.time, "monotonic", lambda: now[0])
    panel.sync_bookmarks(force=False)
    assert len(panel.calls) == 2


def test_force_ignores_the_throttle(panel):
    """★ and a user change must always refetch, however recent the last sync."""
    panel.sync_bookmarks(force=False)
    panel.sync_bookmarks(force=True)
    assert len(panel.calls) == 2


def test_no_fetch_without_a_user(app, monkeypatch):
    from grimoireassist.ui import browser as browser_mod
    cfg = Config()          # grimoire.user defaults to ""
    p = browser_mod.BrowserPanel(cfg=cfg)
    calls = []
    monkeypatch.setattr(browser_mod.threading, "Thread",
                        lambda **kw: calls.append(1))
    p.sync_bookmarks()
    assert calls == []


# ── end-to-end against a real local server ─────────────────────────────────────
#
# The WebEngine-backed paths (new_tab / tab switch) cannot be covered here:
# constructing a QWebEngineProfile aborts the process under Qt's offscreen
# platform. BrowserPanel itself builds fine, so the fetch/parse/emit chain below
# runs unmocked over real HTTP.

def _serve(markdown: str):
    """A one-request-per-hit HTTP server; returns (base_url, hits, shutdown)."""
    import http.server
    import threading as _threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = markdown.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    _threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}", hits, srv.shutdown


def test_fetch_and_parse_end_to_end(app):
    """config → URL → real HTTP GET → markdown parse → bookmark list."""
    from PyQt6.QtCore import QEventLoop, QTimer
    from grimoireassist.ui.browser import BrowserPanel

    base, hits, shutdown = _serve(
        "# Notes\n\nblah\n\n# Bookmarks\n\n"
        "* [Tier List](https://example.com/tiers)\n"
        "* [Builds](https://example.com/builds)\n")
    try:
        cfg = Config()
        cfg.grimoire.base_url = base
        cfg.grimoire.user = "kaiserdrake"
        panel = BrowserPanel(cfg=cfg)

        loop = QEventLoop()
        panel._bookmarks_fetched.connect(lambda *a: loop.quit())
        QTimer.singleShot(5000, loop.quit)          # don't hang the suite
        panel.sync_bookmarks()
        loop.exec()

        assert hits == ["/api/focus/readme/raw?user=kaiserdrake"]
        assert panel._bookmarks == [
            ("Tier List", "https://example.com/tiers"),
            ("Builds", "https://example.com/builds"),
        ]
    finally:
        shutdown()


def test_stale_list_survives_a_failed_fetch(app):
    """A 404 (the state the real endpoint is in) must not blank the bookmarks."""
    from PyQt6.QtCore import QEventLoop, QTimer
    from grimoireassist.ui.browser import BrowserPanel

    cfg = Config()
    cfg.grimoire.base_url = "http://127.0.0.1:1"   # nothing listening
    cfg.grimoire.user = "kaiserdrake"
    panel = BrowserPanel(cfg=cfg)
    panel._bookmarks = [("Kept", "https://example.com/kept")]

    errors = []
    loop = QEventLoop()
    panel._bookmarks_fetched.connect(lambda _s, _b, e: (errors.append(e), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    panel.sync_bookmarks()
    loop.exec()

    assert errors and errors[0], "a dead endpoint should report an error"
    assert panel._bookmarks == [("Kept", "https://example.com/kept")]
