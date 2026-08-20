"""OCR model pre-load reporting.

Warmup runs on a plain background thread, so its result has to cross into the
Qt thread before it can be shown. Two ways that used to fail, both silently:

* the message was posted with `QTimer.singleShot` from that thread — a thread
  with no Qt event loop, where the timer simply never fires; and
* the text was built inside a deferred lambda closing over `except ... as exc`,
  a name Python unbinds at the end of the handler.

Either way a failed model load told the user nothing. `_start_warmup` touches
only `self.engine`, `self.statusBar()` and `self._warmup_status`, so it is
driven here against a stand-in that carries a real signal — the cross-thread
delivery is the thing under test, so it must not be stubbed out.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from grimoireassist.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, timeout=0):
        self.messages.append(text)


class _Window(QObject):
    """The slice of MainWindow that _start_warmup actually uses."""

    _warmup_status = pyqtSignal(str, int)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._bar = _FakeStatusBar()
        self._warmup_status.connect(
            lambda msg, ms: self._bar.showMessage(msg, ms))

    def statusBar(self):
        return self._bar


class BoomEngine:
    ready = False

    def warmup(self):
        raise RuntimeError("CUDA out of memory")


class SlowButFineEngine:
    ready = False

    def warmup(self):
        pass


class AlreadyReadyEngine:
    ready = True

    def warmup(self):                      # pragma: no cover - must not be called
        raise AssertionError("warmup called for an already-ready engine")


def _pump_until(app, predicate, timeout=5.0):
    """Run the event loop until `predicate` holds (the worker thread posts into it)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def test_a_failed_model_load_reaches_the_status_bar(app):
    """The regression: the reason must be shown, not swallowed by a NameError."""
    win = _Window(BoomEngine())
    MainWindow._start_warmup(win)
    assert _pump_until(app, lambda: any("failed" in m for m in win._bar.messages)), \
        f"no failure message; saw {win._bar.messages}"
    failure = [m for m in win._bar.messages if "failed" in m][-1]
    assert "Boom model load failed" in failure
    assert "CUDA out of memory" in failure   # the actual cause, not a generic string


def test_a_successful_load_reports_ready(app):
    win = _Window(SlowButFineEngine())
    MainWindow._start_warmup(win)
    assert _pump_until(app, lambda: any("ready" in m for m in win._bar.messages)), \
        f"no ready message; saw {win._bar.messages}"
    assert not any("failed" in m for m in win._bar.messages)


def test_an_engine_that_needs_no_warmup_is_left_alone(app):
    win = _Window(AlreadyReadyEngine())
    MainWindow._start_warmup(win)
    app.processEvents()
    assert win._bar.messages == ["OCR engine: AlreadyReady"]
