"""Recognise browsers, so the desktop "+" can stand aside where the web clipper is better.

Inside a browser the Omnia WEB clipper reads the page's DOM: it gets the exact sentence AND the
containing paragraph, plus the page title and URL. This app can only ask the accessibility layer,
which is strictly less: it sees one AX node, and a sentence broken across nodes (a ``<br>`` mid
sentence is enough) comes back truncated. Running both also shows the user two "+" buttons.

So the two clippers split the machine by app rather than competing over it.
"""

from __future__ import annotations

# Bundle ids of the browsers the web clipper can run in (Chromium family + Safari + Firefox).
_BROWSER_BUNDLE_IDS = frozenset(
    {
        "com.google.chrome",
        "com.google.chrome.beta",
        "com.google.chrome.canary",
        "com.apple.safari",
        "com.apple.safaritechnologypreview",
        "org.mozilla.firefox",
        "org.mozilla.firefoxdeveloperedition",
        "com.microsoft.edgemac",
        "com.brave.browser",
        "com.operasoftware.opera",
        "com.vivaldi.vivaldi",
        "company.thebrowser.browser",  # Arc
        "ai.perplexity.comet",
    }
)


# Executable names of the same browsers on Windows. A bundle id is a macOS concept; Windows has
# no equivalent, so the frontmost app is identified by the image name of its process instead.
# Without this set every Windows app looked "not a browser", the desktop "+" never stood aside,
# and a double-click in Chrome raised TWO "+" buttons — the exact collision the split prevents.
_BROWSER_PROCESS_NAMES = frozenset(
    {
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
        "opera_gx.exe",
        "vivaldi.exe",
        "arc.exe",
        "comet.exe",
    }
)


def is_browser(app_id: str) -> bool:
    """Whether ``app_id`` names a browser the web clipper covers.

    Accepts either platform's identifier — a macOS bundle id (``com.google.chrome``) or a
    Windows process image name (``chrome.exe``) — because the two operating systems have no
    common way to name a running application, and the caller has whichever its OS could give.

    Args:
        app_id: The frontmost app's identifier (empty when unknown, which is NOT a browser —
            an unknown app must keep working normally).
    """
    normalised = app_id.strip().lower()
    return normalised in _BROWSER_BUNDLE_IDS or normalised in _BROWSER_PROCESS_NAMES
