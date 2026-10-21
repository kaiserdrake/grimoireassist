"""Which of the three main sections sits where.

The window has two panes, left and right, and three sections to put in them:
the tracking view, the Grimoire view and the web browser. A layout pins one
section to the left; the other two take turns on the right. The classic layout
is the odd one out — tracking and Grimoire share the left pane and the browser
is an optional drawer on the right — and stays the default.

Pure data and decisions, no Qt: MainWindow owns the widgets and asks this
module where they go.
"""
from __future__ import annotations

from typing import Optional, Tuple

TRACKING = "tracking"
GRIMOIRE = "grimoire"
BROWSER = "browser"
SECTIONS = (TRACKING, GRIMOIRE, BROWSER)

CLASSIC = "classic"
# A layout is named after the section it pins to the left (classic pins none).
LAYOUTS = (CLASSIC, TRACKING, GRIMOIRE, BROWSER)
DEFAULT_LAYOUT = CLASSIC

LAYOUT_LABELS = {
    CLASSIC: "Tracking ⇄ Grimoire, browser drawer on the right",
    TRACKING: "Tracking on the left · Grimoire ⇄ Browser on the right",
    GRIMOIRE: "Grimoire on the left · Tracking ⇄ Browser on the right",
    BROWSER: "Browser on the left · Tracking ⇄ Grimoire on the right",
}


def normalize(value) -> str:
    """A stored layout name, or the default for anything unknown."""
    value = str(value or "").strip().lower()
    return value if value in LAYOUTS else DEFAULT_LAYOUT


def pinned(layout: str) -> Optional[str]:
    """The section always on the left, or None for the classic layout."""
    layout = normalize(layout)
    return None if layout == CLASSIC else layout


def swap_pair(layout: str) -> Tuple[str, str]:
    """The two sections that take turns in one pane, first one shown by default.

    Classic and browser-pinned swap tracking with Grimoire (inside the tracking
    panel, as before); the other two swap the browser with whichever of
    tracking/Grimoire is not pinned."""
    p = pinned(layout)
    if p is None or p == BROWSER:
        return (TRACKING, GRIMOIRE)
    other = GRIMOIRE if p == TRACKING else TRACKING
    return (other, BROWSER)


def grimoire_detached(layout: str) -> bool:
    """Does the Grimoire need a pane of its own, apart from the tracking panel?

    Only when tracking and Grimoire are not the pair that swaps."""
    return swap_pair(layout) != (TRACKING, GRIMOIRE)


def placement(layout: str, grimoire_shown: bool,
              browser_shown: bool) -> Tuple[str, Optional[str]]:
    """(left, right) section on screen for this layout and view state.

    `grimoire_shown`: Grimoire is on top of tracking (only matters where the
    two swap). `browser_shown`: the drawer is open (classic) or the browser
    has the right pane (where it swaps). Right is None when the right pane is
    closed — only the classic layout ever closes it."""
    p = pinned(layout)
    if p is None:
        left = GRIMOIRE if grimoire_shown else TRACKING
        return left, (BROWSER if browser_shown else None)
    if p == BROWSER:
        return BROWSER, (GRIMOIRE if grimoire_shown else TRACKING)
    first, _ = swap_pair(layout)
    return p, (BROWSER if browser_shown else first)


def visible_sections(layout: str, grimoire_shown: bool,
                     browser_shown: bool) -> set:
    left, right = placement(layout, grimoire_shown, browser_shown)
    return {s for s in (left, right) if s is not None}


def browser_toggles(layout: str) -> bool:
    """Is there anything for the browser button to do? Not when it is pinned."""
    return pinned(layout) != BROWSER


def grimoire_toggles(layout: str) -> bool:
    """Is there anything for the Grimoire button to do? Not when it is pinned."""
    return pinned(layout) != GRIMOIRE


def auto_switch_applies(layout: str) -> bool:
    """Auto Switch flips tracking and Grimoire; it only acts where those two
    share a pane. Elsewhere both stay where the user put them."""
    return not grimoire_detached(layout)
