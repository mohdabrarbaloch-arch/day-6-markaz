"""Vercel serverless entrypoint.

Vercel looks for a module-level `app` (or `handler`) in this file. The only
adjustment needed is making the project root importable, because the function
runs from inside `api/` rather than the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402, F401

# Vercel's Python runtime serves `app` directly.
handler = app
