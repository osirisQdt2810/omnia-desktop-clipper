"""Where the action pill goes and how wide it is — arithmetic, with no Qt in it.

Split out because this is exactly the rule that broke twice. The web clipper shipped a
right-edge clamp tuned for two buttons, grew a third, and put most of it past the edge of the
screen; this app then grew the same third button with **no clamp at all**, so the wand — the new
one — ended up entirely off-screen for a selection near the right edge, where a frameless
always-on-top window is clipped rather than scrollable-to.

Keeping it here rather than inside the widget means the rule is checked on every platform on
every run, instead of only where a ``QApplication`` can be built. That is the house rule
(pure logic separated from Qt glue) applied to the thing that most needed it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The pill's geometry. One source, so the width and the buttons cannot drift apart.
BUTTON_PX = 22
GAP_PX = 4
PAD_PX = 3
#: How far from the pointer the pill sits, down-right, so it is not under the cursor.
CURSOR_OFFSET = 12
#: How close to the screen edge the pill may come.
SCREEN_MARGIN = 4


@dataclass(frozen=True)
class Area:
    """A screen's usable rectangle, in the same terms Qt's ``availableGeometry`` reports."""

    left: int
    top: int
    right: int
    bottom: int


def pill_width(buttons: int) -> int:
    """How wide a pill of ``buttons`` buttons is.

    Derived rather than written down per shape: the old ``+ (GAP if buttons > 1 else 0)``
    under-sized a three-button pill by one gap, and a fourth button would have done it again.
    """
    buttons = max(0, int(buttons))
    return PAD_PX * 2 + buttons * BUTTON_PX + GAP_PX * max(0, buttons - 1)


def pill_height() -> int:
    """How tall the pill is. One row, whatever it carries."""
    return PAD_PX * 2 + BUTTON_PX


def place(x: int, y: int, width: int, height: int, area: Area) -> tuple[int, int]:
    """Where to put a ``width`` x ``height`` pill for a gesture at ``(x, y)``.

    Down-right of the pointer, then pulled back so the WHOLE pill stays inside ``area``. Pulled
    back rather than flipped: the pill is small, and sliding it a few pixels keeps it where the
    eye already is, where flipping it to the other side of the cursor moves the target after the
    user has started aiming at it.

    The left/top clamps come second on purpose. On a screen narrower than the pill the right
    clamp would push it off the left edge instead, and losing the "+" is no better than losing
    the wand.
    """
    px = min(x + CURSOR_OFFSET, area.right - width - SCREEN_MARGIN)
    py = min(y + CURSOR_OFFSET, area.bottom - height - SCREEN_MARGIN)
    return max(px, area.left + SCREEN_MARGIN), max(py, area.top + SCREEN_MARGIN)
