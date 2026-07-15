# Omnia Desktop Clipper

A small **standalone desktop app** (macOS / Windows / Ubuntu) that lets you capture a
**word (or phrase) + its context** from *any* application — PDF viewers, Word, editors,
terminals, even non-selectable text via **OCR** — and send it straight into your running Anki
as a new note. It is the desktop sibling of the
[Omnia Web Clipper](https://github.com/osirisQdt2810/omnia-web-clipper) browser extension and
speaks the **exact same AnkiConnect contract**, so the
[Omnia](https://github.com/osirisQdt2810/omnia) add-on's **Smart Notes / integration gateway**
auto-generates the card with **no Anki-side change** (the add-on ships a matching
`desktop_clipper` integration).

Three ways to capture, all ending in a confirm popup → Anki:

1. **Floating "+" (double-click / drag-select):** select a word or phrase in any app and a small
   **"+"** appears near the cursor — click it → the popup opens. Like the web clipper's "+", but a
   real OS overlay that works over any app. On by default; toggle in Settings.
2. **Selection (hotkey):** select text in any app, press the global hotkey → a popup shows the
   **Word** + its **Context** (the surrounding sentence) → **Add**.
3. **OCR (hotkey):** press the OCR hotkey, **drag a rectangle** over anything on screen
   (a scanned PDF, an image, a locked app) → the text is recognised on the CPU → same popup → **Add**.

This folder is a **front-end client only**. It talks to Anki over HTTP through the
[AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on — it is not part of the
Python add-on.

---

## How it works

```
 SELECTION path                             OCR path
 ─────────────                              ────────
 select text in any app                     press OCR hotkey (⌘⇧O / Ctrl+Shift+O)
 press hotkey (⌘⇧A / Ctrl+Shift+A)          drag a rectangle over the screen
        │                                          │
        ▼                                          ▼
 ClipboardCapture: save→copy→read→RESTORE    grab region → RapidOCR (CPU) → text
 ContextProvider: enclosing sentence               │
 (macOS Accessibility; else = selection)           │
        └───────────────┬──────────────────────────┘
                        ▼
        Popup near cursor:  Word (editable) + Context (editable)
                        │  Add
                        ▼
        AnkiConnectClient ──POST──► http://127.0.0.1:8765
          createDeck → addNote  (tags: omnia-desktop-clipper, omnia-autogen)
                        ▼
        New note → Omnia's gateway sees the tags and AUTO-GENERATES the card.
```

The captured word becomes Omnia's **base field**; the context goes into the mapped context field.

---

## Install

Requires **Python 3.10+**. From this folder:

```bash
pip install -r requirements.txt
```

- **PyQt6** — GUI (tray, popup, settings, region overlay).
- **pynput** — global hotkeys + copy-keystroke synthesis.
- **rapidocr-onnxruntime + Pillow + numpy** — CPU OCR (ONNX Runtime; no PyTorch; self-contained
  models). Used only for the screen-OCR path.
- **pyobjc-framework-ApplicationServices** (macOS only) — read the enclosing sentence via the
  Accessibility API for the *context*.

## Run

```bash
python -m omnia_desktop_clipper
```

The app lives in the **system tray / menu bar** (no main window). Its menu: a checkable
**Enabled** master switch, **Capture now**, **Capture text from screen (OCR)…**, **Settings…**,
**Quit**. When **Enabled** is off, everything is dormant — the hotkeys and the "+" mouse hook are
stopped and nothing captures — a one-click way to pause the clipper without quitting it (also
toggleable in Settings). Default hotkeys: **⌘⇧A / Ctrl+Shift+A** (selection) and
**⌘⇧O / Ctrl+Shift+O** (OCR) — change them in Settings.

### Build a double-click app (no Python for end users)

PyInstaller freezes it into a native app. **Run on each target OS** (it can't cross-compile).
Do it in a **throwaway virtualenv** so the build deps never touch your system Python (PyInstaller
only *reads* the env and copies packages into the frozen app — it installs nothing; only the
`pip install` below writes to an env, which the venv isolates):

```bash
python -m venv .venv-build
source .venv-build/bin/activate      # Windows: .venv-build\Scripts\activate
pip install -r requirements.txt pyinstaller
python build.py
deactivate                           # system Python untouched
```

Output in `dist/`: `Omnia Desktop Clipper.app` (macOS) · `Omnia Desktop Clipper.exe` (Windows) ·
a Linux binary/folder (wrap into an AppImage separately). `build.py` bundles the RapidOCR / ONNX
Runtime models + native libs (`--collect-all`) so OCR works in the frozen app.

**macOS: the build also installs into `/Applications`** (fallback `~/Applications`) so you can
launch it from Launchpad / Spotlight instead of digging into `dist/`. Pass `python build.py
--no-install` to skip.

**Permissions persist across rebuilds** because `build.py` re-signs the `.app` with a *stable*
designated requirement (`identifier "com.omnia.desktopclipper"`). Without this, PyInstaller's
default cdhash-based signature changes every build and macOS TCC drops the Accessibility / Input
Monitoring grant on each rebuild (the "+" then silently dies). With it, you grant the two
permissions **once** and every later `python build.py` + double-click just works. See *Per-OS
permissions → macOS* for the one-time grant (and the one-time cleanup if you built before this).

Settings are stored as JSON in your OS config directory:

| OS      | Path                                                            |
| ------- | -------------------------------------------------------------- |
| macOS   | `~/Library/Application Support/OmniaDesktopClipper/config.json` |
| Windows | `%APPDATA%\OmniaDesktopClipper\config.json`                     |
| Linux   | `~/.config/omnia-desktop-clipper/config.json`                  |

**Settings** offers the same choices as the web-clipper options: **Deck**, **Note type**, and the
**Word/Context → field** map are **dropdowns populated live from AnkiConnect** (they fall back to
editable text if Anki isn't running), plus the **Enabled** master switch, source tag, both hotkeys, autogen, the **floating "+"**
toggle, and the AnkiConnect URL/key.

---

## Context (accurate wording, like the web clipper)

The web clipper reads the page DOM to grab the sentence around a word. The desktop app does the
same via the OS accessibility layer:

- **macOS** — reads the focused element's text via the Accessibility API and extracts the
  enclosing sentence (needs the Accessibility permission; see below).
- **Windows / Linux, or on any failure** — the context falls back to the selection itself; you can
  always edit both fields in the popup.

## OCR — when to use it

Use the **OCR** path when there is **no selectable text**:

- scanned / image-only PDFs (no text layer),
- images and figures,
- apps that block copy or expose no accessibility.

It runs **RapidOCR on the CPU** (English), orders the recognised boxes into reading order, and
prefills the popup.

---

## AnkiConnect setup

1. Install the **AnkiConnect** add-on in Anki (code `2055492159`) and restart Anki.
2. Keep **Anki running** — the clipper POSTs to `http://127.0.0.1:8765`.
3. **No CORS setup needed.** Unlike the browser extension, a native desktop client is **not**
   subject to the browser's CORS / `webCorsOriginList`. If you set an AnkiConnect `apiKey`, enter
   the same key in **Settings → API key**.

The per-add flow is `createDeck` (idempotent) then `addNote`. If your note type's **first field**
is not filled by the field map, the clipper **auto-fills it with the word** so Anki never rejects
the note with *"cannot create note because it is empty."*

---

## How the note flows into Omnia auto-generation (the tags)

Omnia's add-on gateway recognises a clipped note by its **source tag + `omnia-autogen`**. The Omnia
add-on ships a **`desktop_clipper` integration** whose source tag is **`omnia-desktop-clipper`**, so
a clipped note is tagged:

```
["omnia-desktop-clipper", "omnia-autogen"]
```

Enable auto-generation for it in Anki under **Tools → Omnia** → the **Smart Notes** plugin's
**Configure** → **Integrations** tab (toggle *Omnia Desktop Clipper*). The source tag is
configurable in Settings (e.g. set it to `omnia-web-clipper`
to share the browser clipper's toggle).

---

## Per-OS permissions

Synthesising a copy keystroke, listening for a global hotkey, and reading the focused text are
privileged operations.

### macOS
On first launch the app prompts for **Accessibility** (and registers itself in the list); grant it,
then grant the rest below and **fully quit + reopen** the app. The floating **"+"** needs **BOTH
Accessibility AND Input Monitoring** — with only one it won't appear.

- **Privacy & Security → Accessibility** — the mouse hook behind the floating "+", synthesising
  Cmd+C, receiving the global hotkey, and reading the focused text for *context*. (pynput gates its
  listeners on this.)
- **Privacy & Security → Input Monitoring** — the global hotkey listener and the "+" mouse hook.
- **Privacy & Security → Screen Recording** — required for the **OCR** screen grab.

**One-time cleanup if you built the app BEFORE this signing fix** (your existing grant is pinned to
an old build's cdhash and won't match): in **both** the Accessibility and Input Monitoring lists,
select the old "Omnia Desktop Clipper" entry, press **–** to remove it, then re-add the current
`/Applications/Omnia Desktop Clipper.app` with **+** (or just grant when the app re-prompts). This
records the new **identifier**-based requirement — after which grants **persist across every
rebuild**, so you never need to redo it.

> How to tell it's the stale-grant problem: the app keeps re-prompting for Accessibility even
> though the toggle looks ON, and the "+" never appears. That means macOS granted an *old* build's
> exact binary; removing + re-adding re-records the grant against the stable identifier `build.py`
> now signs with.

> Prefer not to bother with the `.app` at all? Double-click **`run.command`** (in this folder): it
> runs the app from source under Terminal, which keeps its grants across every change — grant
> Terminal once in Privacy & Security. (A Terminal window stays open while the app runs.)

### Windows
No special permission is normally required. Apps running **as administrator** won't receive
synthesised input from a non-elevated process; run the clipper elevated to clip from those.

### Linux (Ubuntu)
Use an **X11** session. **Wayland blocks global input hooks and reading another app's selection**,
so the hotkey and synthesised copy do **not** work under Wayland — at the login screen choose
*"Ubuntu on Xorg"* (or set `WaylandEnable=false`). You may need to be in the `input` group.

### Everywhere
Clipboard capture briefly replaces the clipboard with the selection and then **restores** your
original clipboard. For text you can't select, use the **OCR** path.

---

## Configuration reference (`config.json`)

| Key               | Default                                     | Meaning                                       |
| ----------------- | ------------------------------------------- | --------------------------------------------- |
| `ankiconnect_url` | `http://127.0.0.1:8765`                     | AnkiConnect endpoint.                         |
| `api_key`         | `""`                                        | AnkiConnect `apiKey` (empty when none).       |
| `deck_name`       | `Omnia Capture`                             | Target deck (created if missing).             |
| `model_name`      | `Basic`                                     | Note type.                                    |
| `field_map`       | `{"word":"Front","context":"Back"}`         | Capture key → Anki field name.                |
| `source_tag`      | `omnia-desktop-clipper`                      | Source tag the Omnia gateway keys on.         |
| `autogen`         | `true`                                      | Also tag `omnia-autogen` for auto-generation. |
| `hotkey`          | `<cmd>+<shift>+a` / `<ctrl>+<shift>+a`      | Selection-capture global hotkey.              |
| `ocr_hotkey`      | `<cmd>+<shift>+o` / `<ctrl>+<shift>+o`      | Screen-OCR global hotkey.                     |

---

## Development

The **pure** modules — `config.py`, `anki.py`, `capture/base.py`, `capture/context.py` (the
`sentence_around` helper + the fallback provider), `capture/ocr.py` (`RegionOcrCapture` +
`boxes_to_reading_order`), `capture/gesture.py` (the double-click / drag-select detector),
`mouse_watcher.py`, and the injectable core of `capture/clipboard.py` — import **no
PyQt6/pynput/rapidocr/pyobjc at module load**, so their tests run headless in a plain virtualenv.
The heavy deps are imported lazily inside the backends and only exercised at runtime; the UI /
hotkey / overlay modules import PyQt6 at the top and are not imported during test collection.

```bash
python -m pytest tests -q     # headless: no PyQt6/pynput/rapidocr/pyobjc needed
```

### Structure

```
omnia-desktop-clipper/
├── omnia_desktop_clipper/
│   ├── __main__.py        # entry: QApplication + tray + hotkeys + event loop
│   ├── app.py             # ClipperApp: wires everything (selection + OCR paths)
│   ├── config.py          # Config dataclass + load()/save()  (PURE)
│   ├── anki.py            # AnkiConnectClient: addNote + deck/model/field lists  (PURE)
│   ├── platform.py        # config_dir() (pure) + cursor_pos() (lazy Qt)
│   ├── hotkey.py          # GlobalHotkey (pynput, runtime only)
│   ├── mouse_watcher.py   # GlobalMouseWatcher: mouse hook -> gesture -> "+"  (pynput, runtime)
│   ├── capture/
│   │   ├── base.py        # SelectionCapture ABC  (PURE)
│   │   ├── clipboard.py   # ClipboardCapture (injectable core + Qt/pynput backends)
│   │   ├── context.py     # ContextProvider: macOS Accessibility + fallback  (PURE core)
│   │   ├── gesture.py     # SelectionGestureDetector: double-click / drag-select  (PURE)
│   │   └── ocr.py         # OcrEngine/RapidOcrEngine + RegionOcrCapture  (PURE core)
│   └── ui/
│       ├── tray.py            # QSystemTrayIcon + menu
│       ├── popup.py           # CapturePopup (word + context near the cursor)
│       ├── plus_overlay.py    # PlusOverlay: floating "+" near the cursor
│       ├── settings.py        # SettingsDialog (AnkiConnect-backed dropdowns)
│       └── region_overlay.py  # drag-select overlay + screen-region grab (OCR)
├── tests/                 # headless: config / anki / clipboard / context / ocr
├── requirements.txt
└── pyproject.toml
```

### Roadmap

Done: hotkey + clipboard selection, the floating **"+"** overlay on double-click/drag (global
mouse hook), **context** via macOS Accessibility (fallback elsewhere), **CPU OCR** screen capture,
AnkiConnect-backed settings, the dedicated add-on integration, and **PyInstaller packaging**
(`build.py`). Next: Windows UIA / Linux X11 context backends, and only showing the "+" when text
is actually selected (currently it shows on any double-click / drag).
```
