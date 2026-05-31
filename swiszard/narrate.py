"""
narrate.py — real-time stderr narration helper.
Every step of swiszard_do emits a timestamped line to stderr immediately.
"""
from __future__ import annotations

import sys


def narrate(msg: str) -> None:
    """Emit a narration line to stderr immediately (unbuffered)."""
    print(f"[swiszard] {msg}", file=sys.stderr, flush=True)
