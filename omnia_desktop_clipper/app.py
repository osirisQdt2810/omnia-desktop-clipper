"""``ClipperApp``: wires config, tray, global hotkeys, capture, popup, and client.

Two capture paths, both ending in the same confirm-popup -> AnkiConnect add:

* the capture hotkey / tray "Capture now" -> clipboard selection -> context sentence
  (via the OS accessibility provider, falling back to the selection);
* the OCR hotkey / tray "Capture text from screen" -> drag a screen region -> RapidOCR text.

Hotkey callbacks fire on pynput's listener thread, so they are bounced to the Qt main
thread via queued signals before touching any UI.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QDialog, QStyle

from . import config as config_module
from . import platform as platform_helpers
from .anki import AnkiConnectClient, AnkiConnectError
from .capture.base import SelectionCapture
from .capture.clipboard import build_clipboard_capture
from .capture.context import ContextProvider, build_context_provider
from .capture.ocr import RapidOcrEngine, RegionOcrCapture
from .config import Config
from .hotkey import GlobalHotkey
from .ui.popup import CapturePopup
from .ui.region_overlay import RegionSelectOverlay, grab_region
from .ui.settings import SettingsDialog
from .ui.tray import ClipperTray

_TOAST_TITLE = "Omnia Desktop Clipper"
_MAX_TOAST_WORD = 40


class ClipperApp(QObject):
    """The application controller: owns config and the wired components."""

    # Emitted from the hotkey (worker) threads; the queued connections run the connected
    # slots on the Qt main thread where this QObject lives.
    _capture_requested = pyqtSignal()
    _ocr_requested = pyqtSignal()

    def __init__(self, app: QApplication) -> None:
        """Build and wire every component from the loaded config.

        Args:
            app: The running ``QApplication``.
        """
        super().__init__()
        self._app = app
        self._config: Config = config_module.load()
        self._client = self._build_client()
        self._capture: SelectionCapture = build_clipboard_capture(
            use_command_key=sys.platform == "darwin"
        )
        self._context: ContextProvider = build_context_provider()
        self._ocr = RegionOcrCapture(RapidOcrEngine(), grab_region)
        self._tray = ClipperTray(
            icon=self._icon(),
            on_capture=self.capture_and_add,
            on_ocr=self.capture_ocr_and_add,
            on_settings=self.open_settings,
            on_quit=self._app.quit,
        )
        self._hotkey = GlobalHotkey(self._config.hotkey, self._on_hotkey)
        self._ocr_hotkey = GlobalHotkey(self._config.ocr_hotkey, self._on_ocr_hotkey)

        self._capture_requested.connect(self.capture_and_add)
        self._ocr_requested.connect(self.capture_ocr_and_add)
        self._app.aboutToQuit.connect(self._shutdown)

    def start(self) -> None:
        """Show the tray icon and start listening for the global hotkeys."""
        self._tray.show()
        self._hotkey.start()
        self._ocr_hotkey.start()

    def capture_and_add(self) -> None:
        """Capture the selection, resolve its context, confirm, and add the note."""
        selection = self._capture.capture()
        if not selection:
            self._tray.show_message(_TOAST_TITLE, "Nothing was selected to capture.")
            return
        word = selection.strip()
        # The context provider returns the enclosing sentence (macOS Accessibility) or, on
        # any other platform / failure, the selection itself.
        context = self._context.resolve(selection)
        self._confirm_and_add(word, context)

    def capture_ocr_and_add(self) -> None:
        """Drag-select a screen region, OCR it, confirm, and add the note."""
        region = RegionSelectOverlay().select_region()
        if region is None:
            return
        try:
            text = self._ocr.capture(region)
        except Exception as exc:  # OCR must never crash the app
            self._tray.show_message(f"{_TOAST_TITLE} — OCR failed", str(exc))
            return
        if not text:
            self._tray.show_message(
                _TOAST_TITLE, "No text found in the selected region."
            )
            return
        # First line is the likely target word; the whole recognised block is the context.
        word = text.splitlines()[0].strip() or text.strip()
        self._confirm_and_add(word, text.strip())

    def open_settings(self) -> None:
        """Open the settings dialog (with live AnkiConnect pickers) and apply changes."""
        dialog = SettingsDialog(self._config, client=self._client)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_config = dialog.result_config()
        config_module.save(new_config)
        self._apply_config(new_config)

    def _confirm_and_add(self, word: str, context: str) -> None:
        """Show the confirm popup prefilled with ``word``/``context``; add on Accept."""
        popup = CapturePopup(
            word=word,
            context=context,
            deck=self._config.deck_name,
            model=self._config.model_name,
            position=self._cursor_pos(),
        )
        if popup.exec() != QDialog.DialogCode.Accepted:
            return
        edited_word, edited_context = popup.word(), popup.context()
        if not edited_word and not edited_context:
            return
        self._add_note(edited_word, edited_context)

    def _add_note(self, word: str, context: str) -> None:
        """Send the note to AnkiConnect and toast success/failure."""
        try:
            self._client.add_note(
                word,
                context,
                deck=self._config.deck_name,
                model=self._config.model_name,
                field_map=self._config.field_map,
                tags=self._config.tags(),
            )
        except AnkiConnectError as exc:
            self._tray.show_message(f"{_TOAST_TITLE} — failed", str(exc))
            return
        shown = word or context
        if len(shown) > _MAX_TOAST_WORD:
            shown = shown[: _MAX_TOAST_WORD - 1] + "…"
        self._tray.show_message(_TOAST_TITLE, f"Added to Anki: {shown}")

    def _apply_config(self, new_config: Config) -> None:
        """Adopt ``new_config``, rebuilding the client/hotkeys when they change."""
        client_changed = (
            new_config.ankiconnect_url != self._config.ankiconnect_url
            or new_config.api_key != self._config.api_key
        )
        hotkey_changed = new_config.hotkey != self._config.hotkey
        ocr_hotkey_changed = new_config.ocr_hotkey != self._config.ocr_hotkey
        self._config = new_config
        if client_changed:
            self._client = self._build_client()
        if hotkey_changed:
            self._hotkey.stop()
            self._hotkey = GlobalHotkey(new_config.hotkey, self._on_hotkey)
            self._hotkey.start()
        if ocr_hotkey_changed:
            self._ocr_hotkey.stop()
            self._ocr_hotkey = GlobalHotkey(new_config.ocr_hotkey, self._on_ocr_hotkey)
            self._ocr_hotkey.start()

    def _on_hotkey(self) -> None:
        """Capture-hotkey callback (worker thread): hop to the Qt main thread."""
        self._capture_requested.emit()

    def _on_ocr_hotkey(self) -> None:
        """OCR-hotkey callback (worker thread): hop to the Qt main thread."""
        self._ocr_requested.emit()

    def _shutdown(self) -> None:
        """Release the OS hotkey hooks on quit."""
        self._hotkey.stop()
        self._ocr_hotkey.stop()

    def _build_client(self) -> AnkiConnectClient:
        """Construct an AnkiConnect client from the current config."""
        return AnkiConnectClient(self._config.ankiconnect_url, self._config.api_key)

    def _cursor_pos(self) -> tuple[int, int] | None:
        """Return the cursor position for popup placement (best-effort)."""
        try:
            return platform_helpers.cursor_pos()
        except (RuntimeError, ImportError):
            return None

    def _icon(self) -> QIcon:
        """Return a stock icon for the tray (a placeholder until artwork ships)."""
        style = self._app.style()
        if style is None:  # pragma: no cover - a QApplication always has a style
            return QIcon()
        return style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
