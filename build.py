"""Build a standalone double-click Omnia Desktop Clipper with PyInstaller.

PyInstaller cannot cross-compile, so run this ON EACH target OS to get that OS's artifact:

    pip install -r requirements.txt pyinstaller
    python build.py            # macOS: also copies the .app into /Applications
    python build.py --no-install   # build only, don't touch /Applications

Output (under ``dist/``):
    * macOS   -> ``Omnia Desktop Clipper.app``   (double-click; grant the permissions in the README)
    * Windows -> ``Omnia Desktop Clipper.exe``
    * Linux   -> ``Omnia Desktop Clipper`` (a folder/binary; wrap into an AppImage separately)

On macOS the built ``.app`` is also installed into ``/Applications`` (fallback ``~/Applications``)
so you can launch it from Launchpad / Spotlight instead of digging into ``dist/``. Pass
``--no-install`` to skip. No Python install is then needed by end users.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

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


def install_app() -> Path | None:
    """Copy the built macOS ``.app`` into /Applications (fallback ~/Applications).

    Uses ``ditto`` (preserves the bundle) and replaces any previous copy. Returns the install
    path, or ``None`` if there's nothing to install or every destination was unwritable.
    """
    src = Path("dist") / f"{APP_NAME}.app"
    if not src.is_dir():
        print(f"Nothing to install: {src} not found.")
        return None
    for dest_dir in (Path("/Applications"), Path.home() / "Applications"):
        dest = dest_dir / f"{APP_NAME}.app"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            subprocess.check_call(["ditto", str(src), str(dest)])
            print(f"\nInstalled: {dest}")
            print("Launch it from Launchpad / Spotlight / the Applications folder.")
            print(
                "First launch: grant BOTH Accessibility AND Input Monitoring "
                "(System Settings → Privacy & Security) for the floating '+' to work."
            )
            return dest
        except (PermissionError, subprocess.CalledProcessError, OSError) as exc:
            print(f"Could not install to {dest_dir}: {exc}")
    print("Install skipped — run the app from dist/ or copy it manually.")
    return None


def main() -> int:
    """Run PyInstaller; on macOS, also install the .app into /Applications (unless --no-install)."""
    rc = subprocess.call(build_args())
    if rc == 0 and sys.platform == "darwin" and "--no-install" not in sys.argv:
        install_app()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
