"""Validation scorecard: the latest dated ``docs/scorecard/<date>.json``, read once at startup.

The scorecard is a per-release artifact built by ``scripts/make_scorecard.py`` from existing scored
results (``docs/validation-scorecard.md``). The API only serves the file: no number is computed here.
The directory is ``FIRESIM_SCORECARD_DIR`` (set by ``api/Dockerfile`` to ``/app/docs/scorecard``),
else the repository's ``docs/scorecard`` found from this file's location. The newest file by date
(the ``YYYY-MM-DD.json`` name) is cached when the app starts; ``reset_cache()`` re-reads it (tests).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["scorecard"])
log = logging.getLogger(__name__)

_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
_cache: dict[str, Any] | None = None
_loaded = False


def scorecard_dir() -> Path:
    env = os.environ.get("FIRESIM_SCORECARD_DIR")
    if env:
        return Path(env)
    # api/src/firesim_api/routers/scorecard.py -> repository root -> docs/scorecard
    return Path(__file__).resolve().parents[4] / "docs" / "scorecard"


def list_dates(directory: Path | None = None) -> list[str]:
    """Scorecard dates present, oldest first."""
    d = directory or scorecard_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.iterdir() if _DATED.match(p.name))


def load_latest(directory: Path | None = None) -> dict[str, Any] | None:
    d = directory or scorecard_dir()
    dates = list_dates(d)
    if not dates:
        return None
    latest = dates[-1]
    with open(d / f"{latest}.json", encoding="utf-8") as f:
        card = json.load(f)
    card["file"] = f"{latest}.json"
    card["available_dates"] = dates
    return card


def preload() -> None:
    """Read the latest scorecard once (called from the app's lifespan)."""
    global _cache, _loaded
    try:
        _cache = load_latest()
    except Exception:  # noqa: BLE001 - a bad file must not stop the API
        log.exception("Validation scorecard could not be read from %s", scorecard_dir())
        _cache = None
    _loaded = True
    if _cache is None:
        log.warning("No validation scorecard found in %s", scorecard_dir())
    else:
        log.info("Validation scorecard %s (%d metrics)", _cache["file"], len(_cache.get("metrics", [])))


def reset_cache() -> None:
    global _cache, _loaded
    _cache, _loaded = None, False


@router.get("/api/v1/scorecard")
async def get_scorecard() -> dict:
    """The latest dated validation scorecard (docs/scorecard/<date>.json), with `file` and `available_dates`."""
    if not _loaded:
        preload()
    if _cache is None:
        raise HTTPException(status_code=404, detail="No validation scorecard is published on this server")
    return _cache
