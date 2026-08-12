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

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QDialog

from . import config as config_module
from . import platform as platform_helpers
from .anki import AnkiConnectClient, AnkiConnectError
from .browsers import is_browser
from .capture.base import SelectionCapture
from .capture.clipboard import build_clipboard_capture
from .capture.context import (
    AccessibilityWarmer,
    ContextProvider,
    build_context_provider,
)
from .capture.ocr import RapidOcrEngine, RegionOcrCapture
from .config import Config
from .hotkey import GlobalHotkey
from .lookup.client import LookupClient
from .lookup.service import LookupService
from .mouse_watcher import GlobalMouseWatcher
from .ui.icon import plus_icon
from .ui.action_overlay import ActionOverlay
from .ui.lookup_panel import LookupPanel
from .ui.popup import CapturePopup
from .ui.region_overlay import RegionSelectOverlay, grab_region
from .ui.settings import SettingsDialog
from .ui.tray import ClipperTray

_TOAST_TITLE = "Omnia Desktop Clipper"
_MAX_TOAST_WORD = 40


def _warm_macos_trust_cache() -> None:
    """Pre-resolve pyobjc's ``AXIsProcessTrusted`` on the main thread before starting pynput.

    pyobjc's lazy constant import is NOT thread-safe — its ``funcmap.pop`` is destructive — so the
    two-plus pynput listener threads (the hotkeys and the "+" mouse hook) racing its FIRST
    resolution crash with ``KeyError: 'AXIsProcessTrusted'`` and die silently: no hotkey, no "+".
    Resolving it once here, single-threaded, caches it so the listener threads read a ready
    attribute. macOS-only; best-effort (never raises).
    """
    if sys.platform != "darwin":
        return
    try:
        import HIServices  # from pyobjc-framework-ApplicationServices (pynput's own access path)

        HIServices.AXIsProcessTrusted()
    except Exception:
        pass


def _request_macos_accessibility() -> None:
    """On macOS, register this app for Accessibility and prompt to grant it (once).

    The floating "+" mouse hook and the copy-capture need **Accessibility** (pynput gates on
    ``AXIsProcessTrusted``) on top of Input Monitoring. Calling ``AXIsProcessTrustedWithOptions``
    with the prompt option makes the app appear under Privacy & Security → Accessibility and shows
    the grant dialog on first launch (a silent no-op once granted). Input Monitoring is then
    prompted automatically when pynput creates its event tap. Best-effort; no-op elsewhere.
    """
    if sys.platform != "darwin":
        return
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        pass


class ClipperApp(QObject):
    """The application controller: owns config and the wired components."""

    # Emitted from the hotkey (worker) threads; the queued connections run the connected
    # slots on the Qt main thread where this QObject lives.
    _capture_requested = pyqtSignal()
    _ocr_requested = pyqtSignal()
    # Fired from the mouse-watcher thread with the cursor position; the queued connection shows
    # the floating "+" on the Qt main thread.
    _plus_requested = pyqtSignal(int, int)
    # Fetched media bytes coming back from a worker thread; the queued connection delivers them
    # to the panel's callback on the Qt main thread (QPixmap is main-thread-only).
    _media_ready = pyqtSignal(object, object)

    def __init__(self, app: QApplication) -> None:
        """Build and wire every component from the loaded config.

        Args:
            app: The running ``QApplication``.
        """
        super().__init__()
        self._app = app
        self._app.setWindowIcon(plus_icon())  # dock / window icon matches the tray + web clipper
        self._config: Config = config_module.load()
        self._client = self._build_client()
        self._capture: SelectionCapture = build_clipboard_capture(
            use_command_key=sys.platform == "darwin"
        )
        self._context: ContextProvider = build_context_provider()
        self._ocr = RegionOcrCapture(RapidOcrEngine(), grab_region)
        self._tray = ClipperTray(
            icon=self._icon(),
            enabled=self._config.enabled,
            on_toggle_enabled=self._on_toggle_enabled,
            on_capture=self.capture_and_add,
            on_ocr=self.capture_ocr_and_add,
            on_settings=self.open_settings,
            on_quit=self._app.quit,
        )
        self._hotkey = GlobalHotkey(self._config.hotkey, self._on_hotkey)
        self._ocr_hotkey = GlobalHotkey(self._config.ocr_hotkey, self._on_ocr_hotkey)
        # Floating "+": a global mouse hook detects a select gesture; we capture the selection
        # THEN (source app still focused) and show the "+"; clicking it adds the captured note.
        # Always wire the lookup callback and let the config decide visibility, so Settings can
        # toggle the magnifier without rebuilding the overlay.
        self._plus_overlay = ActionOverlay(
            self._on_plus_clicked, on_lookup=self._on_lookup_clicked
        )
        self._plus_overlay.set_lookup_enabled(self._config.lookup_enabled)
        # Lookup: omnia's add-on plugin does the searching/triage; this app renders the answer.
        self._lookup = self._build_lookup_service()
        self._lookup_panel = LookupPanel(
            on_add=self._add_pending_capture,
            on_open_in_anki=self._open_in_anki,
            request_media=self._request_media,
        )
        # Where the "+" was shown, so the panel opens next to the word you were reading.
        self._last_gesture_pos: tuple[int, int] = (0, 0)
        self._mouse_watcher = GlobalMouseWatcher(self._on_select_gesture)
        # The (word, context) captured at gesture time, consumed when the "+" is clicked.
        self._pending_capture: tuple[str, str] | None = None
        # Context capture reads the frontmost app's accessibility tree, and Chromium/Electron
        # apps only expose one after they are asked (and take a moment to build it). Warm each
        # app the first time it comes to the front, so a gesture never races that build-up.
        self._warmer = AccessibilityWarmer()
        self._warm_timer = QTimer(self)
        self._warm_timer.setInterval(1500)
        self._warm_timer.timeout.connect(self._warm_frontmost)

        self._capture_requested.connect(self.capture_and_add)
        self._ocr_requested.connect(self.capture_ocr_and_add)
        self._plus_requested.connect(self._show_plus)
        self._media_ready.connect(lambda cb, data: cb(data))
        self._app.aboutToQuit.connect(self._shutdown)

    def start(self) -> None:
        """Show the tray icon and start the listeners the current config calls for."""
        _warm_macos_trust_cache()  # MUST precede any pynput listener (see the helper's docstring)
        _request_macos_accessibility()  # register + prompt for Accessibility on macOS
        self._tray.show()
        self._warm_timer.start()  # keep the frontmost app's AX tree ready for context capture
        self._sync_listeners()

    def _sync_listeners(self) -> None:
        """Ensure the keyboard hotkeys are running and (re)start/stop the "+" mouse hook per config.

        The keyboard hotkey listeners are started ONCE and never stopped/restarted while the app
        runs. pynput's macOS keyboard listener touches the **main-thread-only** Text Input Source
        manager (``TSMGetInputSourceProperty``) from its own thread; restarting it once the Qt run
        loop is live trips ``dispatch_assert_queue`` and **SIGTRAPs the app** (the crash seen when
        toggling Enabled). Leaving them running is harmless — the capture handlers early-return when
        disabled. The "+" mouse hook has no such constraint, so it starts/stops freely.
        """
        self._hotkey.start()  # idempotent: starts once, no-op afterwards; never stopped here
        self._ocr_hotkey.start()
        if self._config.enabled and self._config.plus_overlay:
            self._mouse_watcher.start()
        else:
            self._mouse_watcher.stop()
            self._plus_overlay.hide()

    def capture_and_add(self) -> None:
        """Capture the selection, resolve its context, confirm, and add the note."""
        if not self._config.enabled:  # master switch off (also covers the tray "Capture now")
            return
        try:
            selection = self._capture.capture()
        except Exception as exc:  # capture must never crash the app (mirror the OCR path)
            # Synthesising the copy keystroke can raise without Accessibility/Input-Monitoring
            # permission (macOS) or under Wayland — toast instead of dying in the queued slot.
            self._tray.show_message(
                f"{_TOAST_TITLE} — capture failed",
                f"Couldn't capture the selection ({exc}). Check accessibility/input permissions.",
            )
            return
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
        if not self._config.enabled:  # master switch off (also covers the tray OCR item)
            return
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
        hotkey_changed = (
            new_config.hotkey != self._config.hotkey
            or new_config.ocr_hotkey != self._config.ocr_hotkey
        )
        lookup_url_changed = new_config.lookup_url != self._config.lookup_url
        self._config = new_config
        if client_changed:
            self._client = self._build_client()
        if lookup_url_changed:
            self._lookup = self._build_lookup_service()
        self._plus_overlay.set_lookup_enabled(new_config.lookup_enabled)
        # NB: we do NOT restart the keyboard hotkey listeners here — that would SIGTRAP (see
        # _sync_listeners). A changed hotkey string is saved and applies on the next app launch.
        self._sync_listeners()
        self._tray.set_enabled(new_config.enabled)  # keep the tray checkbox in sync with Settings
        if hotkey_changed:
            self._tray.show_message(
                _TOAST_TITLE, "Hotkey change saved — reopen the app to apply it."
            )

    def _on_toggle_enabled(self, enabled: bool) -> None:
        """Tray "Enabled" quick-toggle: persist the master switch and start/stop the input hooks."""
        if enabled == self._config.enabled:
            return
        self._config.enabled = enabled
        config_module.save(self._config)
        self._sync_listeners()

    def _on_hotkey(self) -> None:
        """Capture-hotkey callback (worker thread): hop to the Qt main thread."""
        self._capture_requested.emit()

    def _on_ocr_hotkey(self) -> None:
        """OCR-hotkey callback (worker thread): hop to the Qt main thread."""
        self._ocr_requested.emit()

    def _on_select_gesture(self, x: int, y: int) -> None:
        """Mouse-watcher callback (listener thread): hop to the main thread to show the "+".

        pynput reports coordinates as floats; cast to int here because the queued cross-thread
        ``pyqtSignal(int, int)`` marshals a Python float into the C++ int slot as garbage
        (the "+" then lands at an absurd off-screen position).
        """
        self._plus_requested.emit(int(x), int(y))

    def _show_plus(self, x: int, y: int) -> None:
        """Capture the selection NOW, then show the "+" near the cursor (Qt main thread).

        Capturing here — at gesture time, while the SOURCE app is still focused and the text is
        still selected — is essential: clicking the "+" activates our app and the source app
        loses its selection, so a copy taken at click time grabs nothing (the note never adds).
        As a bonus this makes the "+" appear only when text is actually selected, not on every
        double-click. The capture runs on the Qt main thread (``QtClipboard`` is main-thread-only),
        which only delays the "+" by the copy's settle time; the source app (a separate process)
        is not blocked.
        """
        if not self._config.enabled:  # master switch off
            return
        if self._config.skip_in_browsers and is_browser(
            platform_helpers.frontmost_bundle_id()
        ):
            # The web clipper owns browsers: it reads the DOM, so it gets the exact sentence AND
            # the whole paragraph plus the page URL — more than accessibility can give here — and
            # showing both would put two "+" buttons on screen.
            return
        try:
            selection = self._capture.capture()
        except Exception:  # capture backend / permission error: best-effort, show nothing
            return
        if not selection:
            return  # nothing selected -> no "+"
        word = selection.strip()
        # Hand the gesture point to the resolver: the element under it is the paragraph the user
        # was reading, which identifies WHICH occurrence of a repeated word they meant.
        context = self._context.resolve(selection, position=(x, y))
        self._pending_capture = (word, context)
        self._last_gesture_pos = (x, y)
        # A new selection makes any panel on screen stale — hide it before showing the pill.
        self._lookup_panel.hide()
        self._plus_overlay.set_lookup_hint(None, word)  # neutral until the probe answers
        self._plus_overlay.show_at(x, y)
        if self._config.lookup_enabled:
            # Cheap existence probe so the magnifier can say "N cards" / "no card yet" BEFORE
            # it is clicked. Runs off the Qt thread; a failure just leaves the neutral look.
            self._lookup.probe(word)

    def _on_plus_clicked(self) -> None:
        """The floating "+" was clicked: confirm + add the capture taken at gesture time."""
        pending = self._pending_capture
        self._pending_capture = None
        if pending is not None:
            self._confirm_and_add(*pending)

    def _warm_frontmost(self) -> None:
        """Warm the frontmost app's accessibility tree (once per app), off the main thread.

        Resolving the frontmost app is AppKit, so it happens here on the Qt main thread; only the
        pid is handed to the warmer, which does the (possibly slow) AX call on its own thread.
        """
        self._warmer.ensure(platform_helpers.frontmost_pid())

    def _on_lookup_clicked(self) -> None:
        """The magnifier was clicked: open the panel (loading) and run the full lookup."""
        pending = self._pending_capture
        word = pending[0] if pending else ""
        if not word:
            return
        self._lookup_panel.show_loading(word, self._last_gesture_pos)
        self._lookup.lookup(word)

    def _on_lookup_probed(self, word: str, count: int) -> None:
        """Reflect the probe on the magnifier (count badge, or a muted "no card yet")."""
        pending = self._pending_capture
        if not pending or pending[0] != word:
            return  # the user moved on; don't relabel a pill for a different word
        self._plus_overlay.set_lookup_hint(None if count < 0 else count, word)

    def _on_lookup_finished(self, word: str, view: object) -> None:
        self._lookup_panel.show_result(view, self._last_gesture_pos)

    def _on_lookup_failed(self, word: str, message: str) -> None:
        self._lookup_panel.show_error(word, message, self._last_gesture_pos)

    def _add_pending_capture(self) -> None:
        """"Add to Anki" from the lookup panel's not-found state: reuse the capture popup path."""
        pending = self._pending_capture
        self._pending_capture = None
        if pending is not None:
            self._confirm_and_add(*pending)

    def _request_media(self, filename: str, on_ready) -> None:
        """Fetch a media file off the UI thread and hand the bytes back on it.

        The lookup panel needs the bytes to build a QPixmap, which is main-thread-only, so the
        HTTP round-trip runs on a throwaway thread and the result returns through a queued
        signal. A failure delivers ``None``, which the panel renders as "Image unavailable".
        """
        import threading

        def work() -> None:
            try:
                data = self._client.retrieve_media_file(filename)
            except Exception:
                data = None
            self._media_ready.emit(on_ready, data)

        threading.Thread(target=work, name="omnia-media", daemon=True).start()

    def _open_in_anki(self, note_id: int) -> None:
        """Reveal the note in Anki's browser (best-effort; a failure just toasts)."""
        try:
            self._client.gui_browse(f"nid:{note_id}")
        except Exception as exc:
            self._tray.show_message(_TOAST_TITLE, f"Could not open Anki: {exc}")

    def _shutdown(self) -> None:
        """Release the OS hotkey + mouse hooks on quit."""
        self._warm_timer.stop()
        self._hotkey.stop()
        self._ocr_hotkey.stop()
        self._mouse_watcher.stop()

    def _build_lookup_service(self) -> LookupService:
        """Construct the lookup service for the configured URL and wire its signals."""
        service = LookupService(LookupClient(self._config.lookup_url))
        service.finished.connect(self._on_lookup_finished)
        service.failed.connect(self._on_lookup_failed)
        service.probed.connect(self._on_lookup_probed)
        return service

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
        """Return the Omnia clipper icon (blue "+" mark, matching the web clipper)."""
        return plus_icon()
