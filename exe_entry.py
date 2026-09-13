#!/usr/bin/env python3
"""
Executable entry point for Apple Music Playlist Importer.
When double-clicked (no CLI arguments), launches the desktop Web UI automatically.
When run with CLI arguments, delegates to the CLI commands.
"""

import os
import sys
from pathlib import Path

# Ensure stdout and stderr exist even in --noconsole mode
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure correct path
if getattr(sys, "frozen", False):
    app_root = Path(sys._MEIPASS)
else:
    app_root = Path(__file__).parent

sys.path.insert(0, str(app_root))

from applemusic.cli import app, start_web

if __name__ == "__main__":
    if len(sys.argv) <= 1:
        # Double clicked: directly start the desktop GUI app window!
        start_web(host="127.0.0.1", port=8000, open_browser=True, app_mode=True)
    else:
        # CLI invocation: run typer CLI
        app()
