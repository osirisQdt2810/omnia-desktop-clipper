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
BUNDLE_ID = "com.omnia.desktopclipper"


def make_icns() -> Path | None:
    """Render the app's blue-"+" mark to an ``.icns`` (macOS) so the bundle icon matches the tray.

    Uses the same painted icon as the running app (``ui.icon.plus_pixmap``) → an ``.iconset`` →
    ``iconutil``. Best-effort: on any failure the app just builds with PyInstaller's default icon.
    """
    if sys.platform != "darwin":
        return None
    try:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        from omnia_desktop_clipper.ui.icon import plus_pixmap

        # Keep a reference: PyQt6 destroys the C++ QApplication if the Python object is GC'd,
        # which would break QPixmap construction below.
        _app = QApplication.instance() or QApplication([])
        iconset = Path("build") / "omnia.iconset"
        iconset.mkdir(parents=True, exist_ok=True)
        # macOS iconset: each logical size at 1x and 2x, with the exact required filenames.
        for logical in (16, 32, 128, 256, 512):
            plus_pixmap(logical).save(str(iconset / f"icon_{logical}x{logical}.png"))
            plus_pixmap(logical * 2).save(str(iconset / f"icon_{logical}x{logical}@2x.png"))
        icns = Path("build") / "omnia.icns"
        subprocess.check_call(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)])
        return icns
    except Exception as exc:
        print(f"Icon generation skipped ({exc}); building with the default icon.")
        return None


def build_args(icon: Path | None = None) -> list[str]:
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
    if icon is not None:
        args += ["--icon", str(icon)]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "com.omnia.desktopclipper"]
    args += ["run_desktop_clipper.py"]
    return args


def sign_app(app_path: Path) -> None:
    """Re-sign the macOS ``.app`` so its code requirement is the STABLE bundle identifier.

    Why this matters (the reason a rebuilt app "loses" its permissions): PyInstaller ad-hoc-signs
    the app with a **cdhash**-based designated requirement, and the cdhash **changes on every
    build**. macOS TCC pins an Accessibility / Input-Monitoring grant to the app's requirement AT
    GRANT TIME, so after the next rebuild the running app's cdhash no longer matches the granted
    requirement — the grant is silently ignored, ``AXIsProcessTrusted`` returns False, pynput's
    listener bails, and the floating "+" never appears. Re-signing so the *designated requirement*
    is ``identifier "com.omnia.desktopclipper"`` (stable) makes TCC keep the grant across rebuilds:
    grant Accessibility + Input Monitoring **once**, then it's just build + double-click.

    Security note: an identifier-only requirement is weaker than cdhash/certificate pinning — any
    app ad-hoc-signed with the same bundle id would satisfy the same grant. That's an acceptable
    trade-off for a personal, locally-built tool. For cryptographic pinning instead, sign with a
    self-signed code-signing certificate (see the README).
    """
    if not app_path.is_dir():
        return
    try:
        subprocess.check_call(
            [
                "codesign",
                "--force",
                "--deep",
                "--sign",
                "-",  # ad-hoc
                "--identifier",
                BUNDLE_ID,
                "--requirements",
                f'=designated => identifier "{BUNDLE_ID}"',
                str(app_path),
            ]
        )
        print(f"Signed {app_path.name} with a stable identifier requirement ({BUNDLE_ID}).")
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"Warning: could not re-sign the app ({exc}); permissions may reset each rebuild.")


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
    rc = subprocess.call(build_args(make_icns()))
    if rc == 0 and sys.platform == "darwin":
        # Re-sign with a stable identifier requirement BEFORE installing, so the copy in
        # /Applications carries it too — this is what keeps TCC grants across rebuilds.
        sign_app(Path("dist") / f"{APP_NAME}.app")
        if "--no-install" not in sys.argv:
            install_app()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
