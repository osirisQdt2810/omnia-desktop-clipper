"""PyInstaller entry point for the packaged Omnia Desktop Clipper app.

PyInstaller freezes a script (not a ``-m`` module), so this thin launcher just calls the
package's ``main()``. See ``build.py`` for the build command.
"""

from omnia_desktop_clipper.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
