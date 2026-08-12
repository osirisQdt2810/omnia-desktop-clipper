"""Make a Qt window float above the frontmost app (macOS), with or without taking focus.

The clipper is a background tray app: while you are selecting text in another app, a plain Qt
window — especially a ``Qt.Tool`` one — is hidden behind that app, so neither the floating "+"
nor the lookup panel would ever be visible. Promoting the underlying ``NSWindow`` to a status
window level with an all-spaces collection behaviour fixes that.

The two callers need opposite focus behaviour, which is the whole reason this is a parameter:

* the "+" / magnifier overlay must NOT activate — activating the clipper would make the source
  app lose its selection, and the capture that runs on click would grab nothing;
* the lookup panel SHOULD activate, because it is a thing you read, scroll and dismiss with Esc.

Best-effort throughout: a no-op off macOS or when pyobjc is unavailable.
"""

from __future__ import annotations

import sys
from typing import Any

# NSStatusWindowLevel: above normal and floating windows, below the screen saver.
_STATUS_WINDOW_LEVEL = 25


def _is_cocoa() -> bool:
    """Whether Qt is running on the native macOS (Cocoa) platform plugin."""
    try:
        from PyQt6.QtGui import QGuiApplication

        return QGuiApplication.platformName() == "cocoa"
    except Exception:
        return False


def promote_over_all_apps(widget: Any, *, activate: bool = False) -> None:
    """Raise ``widget``'s native window above every app, optionally focusing the clipper.

    Args:
        widget: A shown ``QWidget`` (its ``winId()`` must already exist).
        activate: Take keyboard focus. Leave ``False`` for anything shown while the user is
            working in another app and whose click must not disturb that app's selection.
    """
    if sys.platform != "darwin":
        return
    if not _is_cocoa():
        # Under a non-Cocoa Qt platform plugin (offscreen, minimal, a test harness) winId() is
        # NOT an NSView pointer. Handing it to objc dereferences a foreign handle and SEGFAULTS
        # — a crash no try/except can catch, so the guard has to come first.
        return
    try:
        import objc
        from AppKit import (
            NSApplication,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
        )

        ns_view = objc.objc_object(c_void_p=int(widget.winId()))
        ns_window = ns_view.window()
        if ns_window is None:
            return
        ns_window.setLevel_(_STATUS_WINDOW_LEVEL)
        ns_window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        ns_window.orderFrontRegardless()  # show over the frontmost app
        if activate:
            # A background (accessory) app cannot focus a window without activating itself.
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            ns_window.makeKeyAndOrderFront_(None)
    except Exception:
        pass
