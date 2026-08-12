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


def is_browser(bundle_id: str) -> bool:
    """Whether ``bundle_id`` names a browser the web clipper covers.

    Args:
        bundle_id: The frontmost app's bundle identifier (empty when unknown, which is NOT a
            browser — an unknown app must keep working normally).
    """
    return bundle_id.strip().lower() in _BROWSER_BUNDLE_IDS
