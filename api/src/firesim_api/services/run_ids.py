"""Run IDs: the one place new simulation IDs are made.

A run ID is the only key to ``GET /api/v1/simulations/{id}`` (and ``/arrival``,
``/ensemble`` and the WebSocket), which stay open so EOC operational-period links and the app
can reload a run without the saved-runs passphrase. New IDs are therefore 128 random bits from
``secrets`` (32 lowercase hex characters), so a run cannot practically be guessed.

IDs made before 2026-10-13 are 8 hex characters (32 bits of ``uuid4``). They stay valid
everywhere: the API, the run archive (``services/archive.py`` accepts any
``[A-Za-z0-9_-]{1,64}``) and the copies saved in browsers or exported incident and scenario
files treat an ID as an opaque string. Enumeration of the short IDs is slowed by
``middleware/run_probe_limit.py``. See docs/run-archive.md ("Run IDs").

Hex rather than ``token_urlsafe``: one case, no ``-`` or ``_``, so an ID is safe as a URL path
segment, an object-store key prefix, a file name on a case-insensitive file system and a
command-line argument, and it reads like the old IDs.
"""

from __future__ import annotations

import re
import secrets

#: Random bytes in a new run ID (128 bits)
RUN_ID_BYTES = 16

#: A run ID made by this API since 2026-10-13
NEW_ID = re.compile(r"^[0-9a-f]{32}$")

#: A run ID made before 2026-10-13 (still valid)
LEGACY_ID = re.compile(r"^[0-9a-f]{8}$")


def new_run_id() -> str:
    """A new run ID: 32 lowercase hex characters (128 bits from ``secrets``)."""
    return secrets.token_hex(RUN_ID_BYTES)
