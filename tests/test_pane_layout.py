"""Configurable pane layout: which section is pinned left, which swap right.

The rules live in ui.pane_layout (pure); MainWindow moves three section hosts
between a left and a right stack. These drive the real MainWindow methods
against a stand-in holding real widgets, with the web views stubbed out.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QSplitter, QStackedWidget, QToolBar,
                             QToolButton, QVBoxLayout, QWidget)

from grimoireassist.config import Config
from grimoireassist.ui import monster_panel, pane_layout
from grimoireassist.ui.main_window import MainWindow
from grimoireassist.ui.monster_panel import MonsterPanel
from grimoireassist.ui.pane_layout import (BROWSER, CLASSIC, GRIMOIRE, TRACKING,
                                           placement)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- the rules
def test_unknown_layouts_fall_back_to_classic():
    assert pane_layout.normalize(None) == CLASSIC
    assert pane_layout.normalize("sideways") == CLASSIC
    assert pane_layout.normalize(" Browser ") == BROWSER


def test_classic_is_the_old_behaviour():
    """Tracking and Grimoire swap on the left; the browser is a drawer."""
    assert placement(CLASSIC, False, False) == (TRACKING, None)
    assert placement(CLASSIC, True, False) == (GRIMOIRE, None)
    assert placement(CLASSIC, False, True) == (TRACKING, BROWSER)
    assert pane_layout.DEFAULT_LAYOUT == CLASSIC


@pytest.mark.parametrize("layout,pair", [
    (TRACKING, (GRIMOIRE, BROWSER)),
    (GRIMOIRE, (TRACKING, BROWSER)),
    (BROWSER, (TRACKING, GRIMOIRE)),
])
def test_pinned_layouts_keep_one_left_and_swap_the_others(layout, pair):
    assert pane_layout.swap_pair(layout) == pair
    for g in (False, True):
        for b in (False, True):
            left, right = placement(layout, g, b)
            assert left == layout
            assert right in pair       # the right pane never closes


def test_pinned_right_pane_follows_the_state():
    assert placement(TRACKING, False, False) == (TRACKING, GRIMOIRE)
    assert placement(TRACKING, False, True) == (TRACKING, BROWSER)
    assert placement(GRIMOIRE, False, False) == (GRIMOIRE, TRACKING)
    assert placement(GRIMOIRE, False, True) == (GRIMOIRE, BROWSER)
    assert placement(BROWSER, False, True) == (BROWSER, TRACKING)
    assert placement(BROWSER, True, True) == (BROWSER, GRIMOIRE)


def test_buttons_and_auto_switch_per_layout():
    assert not pane_layout.browser_toggles(BROWSER)
    assert not pane_layout.grimoire_toggles(GRIMOIRE)
    assert all(pane_layout.browser_toggles(l) for l in (CLASSIC, TRACKING, GRIMOIRE))
    # Auto Switch flips tracking/Grimoire, so only where they share a pane.
    assert pane_layout.auto_switch_applies(CLASSIC)
    assert pane_layout.auto_switch_applies(BROWSER)
    assert not pane_layout.auto_switch_applies(TRACKING)
    assert not pane_layout.auto_switch_applies(GRIMOIRE)


# ---------------------------------------------------------------- config
def test_config_defaults_to_classic():
    ui = Config().ui
    assert ui.pane_layout == CLASSIC
    assert ui.pane_split_ratios == {}


def test_config_roundtrip():
    cfg = Config()
    cfg.ui.pane_layout = GRIMOIRE
    cfg.ui.pane_split_ratios = {GRIMOIRE: 0.3, TRACKING: 0.6}
    back = Config.from_dict(cfg.to_dict()).ui
    assert back.pane_layout == GRIMOIRE
    assert back.pane_split_ratios == {GRIMOIRE: 0.3, TRACKING: 0.6}


def test_config_rejects_junk():
    ui = Config.from_dict({"ui": {
        "pane_layout": "diagonal",
        "pane_split_ratios": {"tracking": 5, "nowhere": 0.4, "browser": "x"},
    }}).ui
    assert ui.pane_layout == CLASSIC
    assert ui.pane_split_ratios == {TRACKING: 0.9}
    assert Config.from_dict({"ui": {"pane_split_ratios": "x"}}).ui.pane_split_ratios == {}


# ---------------------------------------------------------------- the panel
class _StubView(QWidget):
    """Stands in for QWebEngineView: the panel only loads URLs into it."""

    def setUrl(self, _url):
        pass

    def setHtml(self, _html):
        pass


@pytest.fixture
def panel(app, monkeypatch):
    monkeypatch.setattr(monster_panel, "_HAVE_WEBENGINE", True)
    monkeypatch.setattr(monster_panel, "shared_grimoire_profile", lambda: None)
    monkeypatch.setattr(MonsterPanel, "_make_view",
                        lambda self, persistent, grimoire_fix: _StubView())
    return MonsterPanel("https://example.test/{slug}")


def test_panel_swaps_grimoire_in_its_stack(panel):
    panel.set_grimoire_visible(True)
    assert panel._stack.currentWidget() is panel.grimoire_view()
    panel.set_grimoire_visible(False)
    assert panel._stack.currentWidget() is panel._card_group


def test_panel_lends_out_the_grimoire_and_takes_it_back(panel):
    view = panel.grimoire_view()
    assert panel.detach_grimoire() is view
    assert panel.is_grimoire_detached()
    assert panel._stack.indexOf(view) < 0
    panel.set_grimoire_visible(True)          # Auto Switch etc. can't pull it back
    assert panel._stack.currentWidget() is panel._card_group
    panel.attach_grimoire()
    assert not panel.is_grimoire_detached()
    assert panel._stack.indexOf(view) >= 0
    panel.set_grimoire_visible(True)
    assert panel._stack.currentWidget() is view


# ---------------------------------------------------------------- the window
class _FakeBrowser(QWidget):
    def __init__(self):
        super().__init__()
        self.ready = 0
        self.focused = 0

    def ensure_ready(self):
        self.ready += 1

    def focus_url_bar(self):
        self.focused += 1

    def sync_bookmarks(self, force=False):
        pass


class _FakePreview:
    def __init__(self):
        self.inset = None

    def set_bottom_inset(self, value):
        self.inset = value


class _LayoutWindow:
    """The slice of MainWindow that places the three sections."""

    _section_host = MainWindow._section_host
    _place_grimoire_view = MainWindow._place_grimoire_view
    _apply_pane_layout = MainWindow._apply_pane_layout
    _remember_split = MainWindow._remember_split
    _apply_split = MainWindow._apply_split
    _set_pane_layout = MainWindow._set_pane_layout
    _save_split_ratios = MainWindow._save_split_ratios
    _sync_layout_actions = MainWindow._sync_layout_actions
    _toggle_browser = MainWindow._toggle_browser
    _set_browser_open = MainWindow._set_browser_open
    _toggle_grimoire_view = MainWindow._toggle_grimoire_view
    _set_grimoire = MainWindow._set_grimoire
    _update_view_buttons = MainWindow._update_view_buttons
    _update_grimoire_btn = MainWindow._update_grimoire_btn
    _update_browser_btn = MainWindow._update_browser_btn
    _apply_dialogue_visibility = MainWindow._apply_dialogue_visibility
    _sync_preview_inset = MainWindow._sync_preview_inset
    _start_idle = MainWindow._start_idle

    def __init__(self, panel, layout=CLASSIC):
        self.cfg = Config()
        self.saves = 0
        self.cfg.save = self._save
        self.cfg.ui.pane_layout = layout
        self.panel = panel
        self._pane_layout = layout
        self._grimoire_shown = False
        self._browser_shown = not pane_layout.browser_toggles(layout)
        self._browser_on_screen = False
        self._right_on_screen = False
        self._applied_layout = None
        self._split_ratios = {CLASSIC: 0.5}
        self._auto_switch = True
        self._detections = []
        self.idle_started = 0
        self.overlay_syncs = 0
        self._debug_widget = QWidget()
        self._debug_widget.setVisible(False)
        self._preview = _FakePreview()

        self._tracking_host = QWidget()
        QVBoxLayout(self._tracking_host).addWidget(panel)
        self._dialogue_widget = QWidget(self._tracking_host)
        self._grimoire_host = QWidget()
        QVBoxLayout(self._grimoire_host)
        self.browser = _FakeBrowser()
        self._left_stack = QStackedWidget()
        self._right_stack = QStackedWidget()
        self._right_stack.setVisible(False)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._left_stack)
        self._splitter.addWidget(self._right_stack)
        self._splitter.setChildrenCollapsible(False)
        self.window = QWidget()
        QVBoxLayout(self.window).addWidget(self._splitter)
        self.window.resize(1000, 600)

        tb = QToolBar()
        self.grimoire_btn = QToolButton()
        self.grimoire_btn.setCheckable(True)
        self._grimoire_btn_act = tb.addWidget(self.grimoire_btn)
        self.browser_btn = QToolButton()
        self.browser_btn.setCheckable(True)
        self._browser_btn_act = tb.addWidget(self.browser_btn)
        self._toolbar = tb

        self._place_grimoire_view()
        self._apply_pane_layout()
        self._set_grimoire(False)
        self.window.show()
        QApplication.processEvents()

    def _save(self, *_a):
        self.saves += 1

    def _cancel_idle(self):
        pass

    @property
    def _idle_timer(self):
        win = self

        class _T:
            def isActive(self):
                return False

            def start(self):
                win.idle_started += 1
        return _T()

    def _update_countdown(self):
        pass

    def _sync_overlays(self):
        self.overlay_syncs += 1

    # what is on screen, by section name
    def on_screen(self):
        out = set()
        if self._left_stack.currentWidget() is self._tracking_host:
            out.add(GRIMOIRE if self.panel._stack.currentWidget()
                    is self.panel.grimoire_view() else TRACKING)
        elif self._left_stack.currentWidget() is self._grimoire_host:
            out.add(GRIMOIRE)
        elif self._left_stack.currentWidget() is self.browser:
            out.add(BROWSER)
        if self._right_stack.isVisible():
            cur = self._right_stack.currentWidget()
            if cur is self.browser:
                out.add(BROWSER)
            elif cur is self._grimoire_host:
                out.add(GRIMOIRE)
            elif cur is self._tracking_host:
                out.add(GRIMOIRE if self.panel._stack.currentWidget()
                        is self.panel.grimoire_view() else TRACKING)
        return out

    def left(self):
        return self._left_stack.currentWidget()

    def close(self):
        self.window.close()


@pytest.fixture
def make_win(panel):
    made = []

    def _make(layout=CLASSIC):
        win = _LayoutWindow(panel, layout)
        made.append(win)
        return win
    yield _make
    for w in made:
        w.close()


def test_classic_starts_as_before(make_win):
    win = make_win(CLASSIC)
    assert win.left() is win._tracking_host
    assert not win._right_stack.isVisible()
    assert win.on_screen() == {TRACKING}
    assert win._grimoire_btn_act.isVisible() and win._browser_btn_act.isVisible()


def test_classic_buttons(make_win):
    win = make_win(CLASSIC)
    win._toggle_grimoire_view()
    assert win.on_screen() == {GRIMOIRE}
    assert win.grimoire_btn.isChecked()
    win._toggle_browser()
    assert win.on_screen() == {GRIMOIRE, BROWSER}
    assert win.browser_btn.isChecked()
    assert win.browser.focused == 1
    win._toggle_browser()
    assert win.on_screen() == {GRIMOIRE}
    assert not win._right_stack.isVisible()


def test_tracking_pinned_swaps_grimoire_and_browser(make_win):
    win = make_win(TRACKING)
    assert win.left() is win._tracking_host
    assert win.on_screen() == {TRACKING, GRIMOIRE}
    assert win.panel.is_grimoire_detached()
    assert win._grimoire_host.isAncestorOf(win.panel.grimoire_view())
    # Shown, not just parented: the stack had hidden it as a background page.
    assert win.panel.grimoire_view().isVisible()
    # Both buttons swap the right pane; they always agree.
    win._toggle_grimoire_view()
    assert win.on_screen() == {TRACKING, BROWSER}
    assert win.browser_btn.isChecked() and not win.grimoire_btn.isChecked()
    win._toggle_browser()
    assert win.on_screen() == {TRACKING, GRIMOIRE}
    assert win.grimoire_btn.isChecked() and not win.browser_btn.isChecked()


def test_grimoire_pinned_swaps_tracking_and_browser(make_win):
    win = make_win(GRIMOIRE)
    assert win.left() is win._grimoire_host
    assert win.panel.grimoire_view().isVisible()
    assert win.on_screen() == {GRIMOIRE, TRACKING}
    assert not win._grimoire_btn_act.isVisible()   # nothing for it to toggle
    win._toggle_grimoire_view()                    # e.g. a stray shortcut: no-op
    assert win.on_screen() == {GRIMOIRE, TRACKING}
    win._toggle_browser()
    assert win.on_screen() == {GRIMOIRE, BROWSER}


def test_browser_pinned_swaps_tracking_and_grimoire(make_win):
    win = make_win(BROWSER)
    assert win.left() is win.browser
    assert win.on_screen() == {BROWSER, TRACKING}
    assert not win._browser_btn_act.isVisible()
    win._toggle_browser()                          # Ctrl+B: nothing to close
    assert win.on_screen() == {BROWSER, TRACKING}
    win._toggle_grimoire_view()
    assert win.on_screen() == {BROWSER, GRIMOIRE}
    assert not win.panel.is_grimoire_detached()


def test_auto_switch_leaves_detached_layouts_alone(make_win):
    win = make_win(TRACKING)
    win._set_grimoire(True)                        # what the idle timeout calls
    assert win._grimoire_shown is False
    assert win.on_screen() == {TRACKING, GRIMOIRE}
    win._start_idle()
    assert win.idle_started == 0


def test_dialogue_strip_stays_with_tracking(make_win):
    win = make_win(TRACKING)
    assert win._dialogue_widget.isVisibleTo(win._tracking_host)
    win._set_pane_layout(CLASSIC)
    win._toggle_grimoire_view()                    # Grimoire covers tracking
    assert not win._dialogue_widget.isVisibleTo(win._tracking_host)


def test_switching_layouts_live_through_every_preset(make_win):
    win = make_win(CLASSIC)
    for layout in (TRACKING, GRIMOIRE, BROWSER, CLASSIC, GRIMOIRE, TRACKING, CLASSIC):
        win._set_pane_layout(layout)
        assert win.cfg.ui.pane_layout == layout
        assert win._applied_layout == layout
        assert pane_layout.pinned(layout) in (None, *win.on_screen())
        # Every host sits in exactly one pane.
        for host in (win._tracking_host, win._grimoire_host, win.browser):
            in_left = win._left_stack.indexOf(host) >= 0
            in_right = win._right_stack.indexOf(host) >= 0
            assert in_left != in_right
        assert win.panel.is_grimoire_detached() == pane_layout.grimoire_detached(layout)
    assert win.saves == 7
    assert win.overlay_syncs > 0                   # overlays re-raised each time


def test_switching_back_to_classic_reattaches_the_grimoire(make_win):
    win = make_win(GRIMOIRE)
    win._set_pane_layout(CLASSIC)
    view = win.panel.grimoire_view()
    assert win.panel._stack.indexOf(view) >= 0
    assert win._grimoire_host.layout().count() == 0
    win._toggle_grimoire_view()
    assert win.on_screen() == {GRIMOIRE}


def test_open_browser_stays_open_across_a_switch(make_win):
    win = make_win(CLASSIC)
    win._set_browser_open(True)
    win._set_pane_layout(TRACKING)
    assert win.on_screen() == {TRACKING, BROWSER}
    win._set_pane_layout(BROWSER)
    win._set_pane_layout(CLASSIC)
    assert win.on_screen() == {TRACKING, BROWSER}  # a pinned browser was up


def test_split_ratio_is_kept_per_layout(make_win):
    win = make_win(TRACKING)
    total = sum(win._splitter.sizes())
    win._splitter.setSizes([int(total * 0.3), total - int(total * 0.3)])
    QApplication.processEvents()
    win._set_pane_layout(GRIMOIRE)                 # notes tracking's 0.3
    assert win._split_ratios[TRACKING] == pytest.approx(0.3, abs=0.02)
    assert win.cfg.ui.pane_split_ratios[TRACKING] == pytest.approx(0.3, abs=0.02)
    QApplication.processEvents()
    left, right = win._splitter.sizes()
    assert left / (left + right) == pytest.approx(0.5, abs=0.02)   # grimoire default
    win._set_pane_layout(TRACKING)
    QApplication.processEvents()
    left, right = win._splitter.sizes()
    assert left / (left + right) == pytest.approx(0.3, abs=0.02)


def test_classic_ratio_still_lives_in_browser_split_ratio(make_win):
    win = make_win(CLASSIC)
    win._set_browser_open(True)
    QApplication.processEvents()
    total = sum(win._splitter.sizes())
    win._splitter.setSizes([int(total * 0.7), total - int(total * 0.7)])
    QApplication.processEvents()
    win._set_browser_open(False)                   # drawer closing notes it
    win._save_split_ratios()
    assert win.cfg.ui.browser_split_ratio == pytest.approx(0.7, abs=0.02)
    assert CLASSIC not in win.cfg.ui.pane_split_ratios


def test_debug_panel_only_insets_the_pip_over_tracking(make_win):
    win = make_win(CLASSIC)
    win._debug_widget.setVisible(True)
    win._sync_preview_inset()
    assert win._preview.inset == win._debug_widget.sizeHint().height()
    win._set_pane_layout(BROWSER)                  # PiP now floats over the browser
    assert win._preview.inset == 0
