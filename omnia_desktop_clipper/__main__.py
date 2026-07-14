"""Entry point: build the ``QApplication``, start the clipper, run the loop.

Run with ``python -m omnia_desktop_clipper``.
"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from .app import ClipperApp


def main() -> int:
    """Start the tray app and run the Qt event loop; return the exit code."""
    app = QApplication(sys.argv)
    # A tray app has no main window; don't quit when the last dialog closes.
    app.setQuitOnLastWindowClosed(False)
    clipper = ClipperApp(app)
    clipper.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
