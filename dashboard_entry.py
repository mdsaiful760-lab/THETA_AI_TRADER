"""Streamlit entrypoint wrapper for the THETA AI TRADER dashboard.

Streamlit auto-detects a ``pages/`` directory sitting next to whichever
script it is launched with and takes over navigation with its own native
multi-page file-list UI. ``dashboard/pages/`` is a plain Python package of
page-render modules for our own ``PAGE_REGISTRY``/sidebar — not a set of
standalone Streamlit multi-page scripts — so launching Streamlit directly
against ``dashboard/app.py`` incorrectly triggers that auto-navigation and
replaces our custom sidebar/topbar with Streamlit's own file-list.

Running Streamlit against this wrapper instead (which lives at the repo
root, with no sibling ``pages/`` directory) avoids the auto-detection
entirely while leaving ``dashboard/app.py`` and the ``dashboard.pages``
package untouched.
"""

from __future__ import annotations

from dashboard.app import main

if __name__ == "__main__":
    main()
