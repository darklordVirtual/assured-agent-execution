# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Terminal output that survives a Windows console.

A Windows console defaults to cp1252, which cannot encode the arrows, box
characters and em dashes this product prints. Without this, a command crashes
with UnicodeEncodeError *after* doing its work — the governance decisions are
recorded, the backup is written, and the operator sees a stack trace instead
of the result.

Fixed once here rather than in each entry point: the CLI got its own copy
first, and `scripts/backup.py` then crashed on exactly the same character.
Every entry point calls this.

Stdlib only, so the scripts that run before the virtualenv exists can use it
by adding ``src`` to ``sys.path``.
"""
from __future__ import annotations

import sys

__all__ = ["force_utf8_output"]


def force_utf8_output() -> None:
    """Print UTF-8 regardless of the console's default codepage.

    ``errors="replace"`` rather than a strict encoder: a character we cannot
    render must degrade to a placeholder, never take down a command whose real
    output is the result it just produced.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass  # a redirected or already-wrapped stream; not fatal
