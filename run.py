#!/usr/bin/env python3
"""Launch synthet-adc straight from a source checkout — no install needed.

    pip install -r requirements.txt
    python run.py

Puts ``src/`` on the import path and starts the app, so this works the same on
macOS, Windows, and Linux without ``pip install -e .``. (Installing the package
gives you the ``synthet-adc`` command instead; either path is fine.)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from synthet.app import main  # noqa: E402  (after the sys.path tweak above)

if __name__ == "__main__":
    raise SystemExit(main())
