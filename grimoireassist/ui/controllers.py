"""Face-button and shoulder layouts for the controller reference overlay.

Deliberately Qt-free: `config.py` validates the configured pad ids against
`LAYOUTS`, and it must stay importable without a display.

Every layout names its buttons by *physical position* — `top`/`right`/`bottom`/
`left` are where your thumb goes, not what the label says. That is the whole
point of the overlay: the same diamond position is called ✕ on a PlayStation
pad, B on a Switch pad and A on an Xbox pad.

Buttons are drawn as dark faces with a coloured glyph, the way modern DualSense
and Xbox pads look — the label stays legible when the overlay is scaled down.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FACE = "#23232e"      # button face; the same on every pad
NEUTRAL = "#e8e8ec"   # label colour where the real pad doesn't colour-code


@dataclass(frozen=True)
class Button:
    label: str          # "✕", "A", "L1" … drawn as plain text
    fill: str = FACE    # face colour
    text: str = NEUTRAL  # label colour


@dataclass(frozen=True)
class ControllerLayout:
    """One pad. Face buttons are keyed by physical position, not by name."""
    id: str
    name: str
    short: str   # used in the overlay's collapsed pill, where space is tight
    top: Button
    right: Button
    bottom: Button
    left: Button
    lb: Button   # left bumper
    rb: Button   # right bumper
    lt: Button   # left trigger
    rt: Button   # right trigger

    @property
    def faces(self) -> tuple[Button, Button, Button, Button]:
        """Face buttons in clockwise-from-top order."""
        return (self.top, self.right, self.bottom, self.left)

    @property
    def shoulders(self) -> tuple[Button, Button, Button, Button]:
        """Bumpers then triggers: (lb, rb, lt, rt)."""
        return (self.lb, self.rb, self.lt, self.rt)


LAYOUTS: dict[str, ControllerLayout] = {
    "playstation": ControllerLayout(
        id="playstation", name="PlayStation", short="PS",
        top=Button("△", text="#5cd6a0"),
        right=Button("○", text="#ef8a76"),
        bottom=Button("✕", text="#5b8fe8"),
        left=Button("□", text="#e06bb8"),
        lb=Button("L1"), rb=Button("R1"), lt=Button("L2"), rt=Button("R2"),
    ),
    "switch": ControllerLayout(
        id="switch", name="Nintendo Switch", short="Switch",
        top=Button("X"), right=Button("A"), bottom=Button("B"), left=Button("Y"),
        lb=Button("L"), rb=Button("R"), lt=Button("ZL"), rt=Button("ZR"),
    ),
    "xbox": ControllerLayout(
        id="xbox", name="Xbox", short="Xbox",
        top=Button("Y", text="#f2d24a"),
        right=Button("B", text="#ef6f6f"),
        bottom=Button("A", text="#5ed684"),
        left=Button("X", text="#5aa8ef"),
        lb=Button("LB"), rb=Button("RB"), lt=Button("LT"), rt=Button("RT"),
    ),
    "steamdeck": ControllerLayout(
        id="steamdeck", name="Steam Deck", short="Deck",
        top=Button("Y", text="#c8c8d2"),
        right=Button("B", text="#c8c8d2"),
        bottom=Button("A", text="#c8c8d2"),
        left=Button("X", text="#c8c8d2"),
        lb=Button("L1"), rb=Button("R1"), lt=Button("L2"), rt=Button("R2"),
    ),
}

# Order the pads appear in the menus.
LAYOUT_ORDER = ("playstation", "switch", "xbox", "steamdeck")

DEFAULT_LEFT = "playstation"
DEFAULT_RIGHT = "switch"


def get_layout(layout_id: str, fallback: str = DEFAULT_LEFT) -> ControllerLayout:
    """Look up a pad by id, falling back to `fallback` for unknown ids (a
    hand-edited config shouldn't take the overlay down)."""
    found: Optional[ControllerLayout] = LAYOUTS.get(str(layout_id))
    if found is not None:
        return found
    return LAYOUTS.get(fallback, LAYOUTS[DEFAULT_LEFT])


def valid_id(layout_id: object, fallback: str) -> str:
    """Normalise a configured pad id to a known one."""
    return str(layout_id) if str(layout_id) in LAYOUTS else fallback
