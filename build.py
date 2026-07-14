"""Build a standalone double-click Omnia Desktop Clipper with PyInstaller.

PyInstaller cannot cross-compile, so run this ON EACH target OS to get that OS's artifact:

    pip install -r requirements.txt pyinstaller
    python build.py

Output (under ``dist/``):
    * macOS   -> ``Omnia Desktop Clipper.app``   (double-click; grant the permissions in the README)
    * Windows -> ``Omnia Desktop Clipper.exe``
    * Linux   -> ``Omnia Desktop Clipper`` (a folder/binary; wrap into an AppImage separately)

No Python install is then needed by end users.
"""

from __future__ import annotations

import subprocess
import sys

APP_NAME = "Omnia Desktop Clipper"


def build_args() -> list[str]:
    """Return the PyInstaller command line for the current OS."""
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",  # GUI/tray app: no console window
        "--name",
        APP_NAME,
        # RapidOCR + ONNX Runtime bundle model/data files + native libs that PyInstaller's
        # analysis misses; --collect-all pulls their data, binaries, and submodules.
        "--collect-all",
        "rapidocr_onnxruntime",
        "--collect-all",
        "onnxruntime",
        "--collect-submodules",
        "pynput",
    ]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "com.omnia.desktopclipper"]
    args += ["run_desktop_clipper.py"]
    return args


def main() -> int:
    """Run PyInstaller; return its exit code."""
    return subprocess.call(build_args())


if __name__ == "__main__":
    raise SystemExit(main())
