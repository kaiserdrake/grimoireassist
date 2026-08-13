"""Controller button-map overlay: layout data, sizing, move, collapse, config.

Runs on Qt's offscreen platform, so it needs no display. Widgets are driven by
calling their handlers directly rather than posting synthetic events — the point
is the geometry and collapse logic, not Qt's own event delivery. The host is
shown, because Qt only delivers resize events to a visible parent, and the
overlay tracks its host through a resize event filter.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before any PyQt6 import

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from grimoireassist.config import Config
from grimoireassist.ui.controller_overlay import (MIN_WIDTH,
                                                  ControllerMapOverlay)
from grimoireassist.ui.controllers import (LAYOUT_ORDER, LAYOUTS, get_layout,
                                           valid_id)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app):
    widget = QWidget()
    widget.resize(900, 600)
    widget.show()
    yield widget
    widget.close()


def _event(kind, pos: QPointF, button, buttons) -> QMouseEvent:
    """Drag handling works off globalPosition deltas, so the global point has to
    be supplied explicitly — the short QMouseEvent constructor fills it with the
    real cursor position. Passing `pos` for both keeps the deltas honest."""
    return QMouseEvent(kind, pos, pos, button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def _press(overlay, pos: QPointF) -> None:
    overlay.mousePressEvent(_event(
        QMouseEvent.Type.MouseButtonPress, pos, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton))


def _move(overlay, pos: QPointF) -> None:
    overlay.mouseMoveEvent(_event(
        QMouseEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton))


def _release(overlay, pos: QPointF) -> None:
    overlay.mouseReleaseEvent(_event(
        QMouseEvent.Type.MouseButtonRelease, pos, Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton))


# ---- layout data -------------------------------------------------------
def test_every_pad_is_complete():
    assert set(LAYOUT_ORDER) == set(LAYOUTS)
    for pad in LAYOUTS.values():
        assert len(pad.faces) == 4 and len(pad.shoulders) == 4
        assert all(b.label for b in pad.faces + pad.shoulders)
        assert pad.name and pad.short


def test_bottom_face_button_differs_per_pad():
    """The whole reason the overlay exists: the same physical position under
    your thumb is called something different on every pad, and Nintendo swaps
    A/B relative to Xbox."""
    assert LAYOUTS["playstation"].bottom.label == "✕"
    assert LAYOUTS["switch"].bottom.label == "B"
    assert LAYOUTS["xbox"].bottom.label == "A"
    # ...and the swap runs the other way round on the right-hand button.
    assert LAYOUTS["switch"].right.label == "A"
    assert LAYOUTS["xbox"].right.label == "B"


def test_unknown_pad_ids_fall_back():
    assert get_layout("nope", "xbox").id == "xbox"
    assert get_layout("switch", "xbox").id == "switch"
    assert valid_id("nope", "switch") == "switch"
    assert valid_id("xbox", "switch") == "xbox"


# ---- sizing ------------------------------------------------------------
def test_height_follows_width_and_floors_at_min(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    tall = overlay.height()
    assert overlay.width() == 420

    overlay._wanted = 320
    overlay._refit()
    assert overlay.width() == 320
    assert overlay.height() < tall          # the body keeps its aspect

    overlay._wanted = 10                    # absurd request
    overlay._refit()
    assert overlay.width() == MIN_WIDTH


def test_host_cap_does_not_destroy_the_requested_width(host):
    """A shrunken host caps the overlay, but the user's choice survives it."""
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=600)
    assert overlay.width() == 600

    host.resize(400, 600)
    assert overlay.width() < 600            # capped to what the host holds

    host.resize(900, 600)
    assert overlay.width() == 600           # request restored


def test_overlay_stays_inside_a_shrinking_host(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch",
                                   width=420, fx=1.0, fy=1.0)
    host.resize(500, 400)
    assert overlay.x() >= 0 and overlay.y() >= 0
    assert overlay.x() + overlay.width() <= host.width()
    assert overlay.y() + overlay.height() <= host.height()


# ---- moving ------------------------------------------------------------
def test_drag_moves_the_overlay_and_reports_once(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    reported = []
    overlay.geometry_changed.connect(
        lambda fx, fy, w: reported.append((fx, fy, w)))
    origin = overlay.pos()

    _press(overlay, QPointF(100, 60))       # body, not header/chevron/grip
    _move(overlay, QPointF(140, 90))
    assert overlay.pos().x() == origin.x() + 40
    assert overlay.pos().y() == origin.y() + 30
    assert reported == []                   # nothing persisted mid-drag

    _release(overlay, QPointF(140, 90))
    assert len(reported) == 1
    assert reported[0][2] == 420            # width untouched by a move


def test_grip_drag_resizes_from_a_fixed_top_left(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    reported = []
    overlay.geometry_changed.connect(lambda *a: reported.append(a))
    origin = overlay.pos()
    grip = QPointF(overlay._grip_rect().center())

    _press(overlay, grip)
    _move(overlay, QPointF(grip.x() + 60, grip.y() + 25))
    assert overlay.width() > 420
    assert overlay.pos() == origin           # grows away from the anchor

    _release(overlay, QPointF(grip.x() + 60, grip.y() + 25))
    assert len(reported) == 1
    assert reported[0][2] == overlay.width()


def test_the_grip_is_gone_while_collapsed(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420,
                                   collapsed=True)
    assert overlay._grip_rect().isNull()


def test_a_drag_that_does_not_move_reports_nothing(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    reported = []
    overlay.geometry_changed.connect(lambda *a: reported.append(a))
    _press(overlay, QPointF(100, 60))
    _release(overlay, QPointF(100, 60))
    assert reported == []


def test_position_is_proportional_across_hosts(app, host):
    """The stored fractions place the overlay in the same relative spot on a
    differently sized host, instead of stranding it off-screen."""
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    _press(overlay, QPointF(100, 60))
    _move(overlay, QPointF(200, 160))
    _release(overlay, QPointF(200, 160))
    fx, fy, width = overlay.geometry_setting()

    other = QWidget()
    other.resize(1400, 900)
    other.show()
    try:
        clone = ControllerMapOverlay(other, "playstation", "switch",
                                     width=width, fx=fx, fy=fy)
        assert clone.x() == pytest.approx(fx * (other.width() - clone.width()),
                                          abs=1)
        assert clone.y() == pytest.approx(fy * (other.height() - clone.height()),
                                          abs=1)
    finally:
        other.close()


# ---- collapsing --------------------------------------------------------
def test_collapse_folds_in_place_and_restores(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    before_pos, before_size = overlay.pos(), overlay.size()
    reported = []
    overlay.collapsed_changed.connect(reported.append)

    overlay.set_collapsed(True)
    assert overlay.is_collapsed()
    assert overlay.pos() == before_pos          # folds from the top-left
    assert overlay.height() < before_size.height()
    assert overlay.width() < before_size.width()
    assert reported == [True]

    overlay.set_collapsed(True)                 # already folded: no-op
    assert reported == [True]

    overlay.set_collapsed(False)
    assert overlay.size() == before_size        # exact restore
    assert overlay.pos() == before_pos
    assert reported == [True, False]


def test_chevron_click_toggles_collapse(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    chevron = QPointF(overlay._chevron_rect().center())

    _press(overlay, chevron)
    _release(overlay, chevron)
    assert overlay.is_collapsed()

    chevron = QPointF(overlay._chevron_rect().center())  # the pill is narrower
    _press(overlay, chevron)
    _release(overlay, chevron)
    assert not overlay.is_collapsed()


def test_release_away_from_the_chevron_does_not_toggle(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    _press(overlay, QPointF(overlay._chevron_rect().center()))
    _release(overlay, QPointF(20, 60))
    assert not overlay.is_collapsed()


def test_collapsed_overlay_still_tracks_the_host(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch",
                                   width=420, fx=1.0, fy=1.0, collapsed=True)
    host.resize(320, 240)
    assert overlay.x() + overlay.width() <= host.width()
    assert overlay.y() + overlay.height() <= host.height()


def test_pair_selection_updates_both_sides(host):
    overlay = ControllerMapOverlay(host, "playstation", "switch", width=420)
    overlay.set_pair("xbox", "steamdeck")
    assert overlay._left.id == "xbox" and overlay._right.id == "steamdeck"
    overlay.set_pair("bogus", "switch")          # a hand-edited config
    assert overlay._left.id in LAYOUTS


# ---- config ------------------------------------------------------------
def test_ui_config_controller_map_defaults():
    ui = Config().ui
    assert ui.show_controller_map is False
    assert ui.controller_map_left == "playstation"
    assert ui.controller_map_right == "switch"
    assert ui.controller_map_collapsed is False


def test_ui_config_controller_map_roundtrip():
    cfg = Config()
    cfg.ui.show_controller_map = True
    cfg.ui.controller_map_left = "xbox"
    cfg.ui.controller_map_right = "steamdeck"
    cfg.ui.controller_map_width = 512
    cfg.ui.controller_map_x = 0.25
    cfg.ui.controller_map_y = 0.75
    cfg.ui.controller_map_collapsed = True
    back = Config.from_dict(cfg.to_dict()).ui
    assert back.show_controller_map is True
    assert (back.controller_map_left, back.controller_map_right) == ("xbox",
                                                                    "steamdeck")
    assert back.controller_map_width == 512
    assert (back.controller_map_x, back.controller_map_y) == (0.25, 0.75)
    assert back.controller_map_collapsed is True


def test_ui_config_rejects_junk():
    ui = Config.from_dict({"ui": {
        "controller_map_left": "gamecube",   # not a pad we know
        "controller_map_x": 7.5,             # out of range
        "controller_map_y": "nope",
        "controller_map_width": 10,          # below the minimum
    }}).ui
    assert ui.controller_map_left == "playstation"
    assert ui.controller_map_x == 1.0
    assert ui.controller_map_y == 0.0
    assert ui.controller_map_width == MIN_WIDTH
