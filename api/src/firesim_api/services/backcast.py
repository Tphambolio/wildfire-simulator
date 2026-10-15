"""Pre-fire (back-cast) fuel for dated historical runs: API glue (docs/fuel-grid-backcast.md).

``plan_backcast`` decides, from the request's ``start_time``, whether the Alberta cells of a
provincial window are back-cast, and returns the ``FuelSources`` to read (with the
``Backcast`` attached, which also keys the window cache) and the ``fuel_source.backcast`` info.
The info is the contract with the fuel-vintage warning: ``applied`` true means the Alberta cells
burned since ``as_of`` were restored; false carries the ``reason``.
"""

from __future__ import annotations

import dataclasses
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loaded: dict[str | None, object] = {}


def backcast_data():
    """The installed back-cast data set (``FIRESIM_AB_BACKCAST_DIR``), loaded once, or None."""
    from firesim.data.backcast import BackcastData
    from firesim_api.settings import settings

    root = settings.ab_backcast_dir
    with _lock:
        if root not in _loaded:
            try:
                _loaded[root] = BackcastData.load(root)
            except Exception:  # a broken install must not break runs
                logger.exception("Back-cast data in %s could not be read", root)
                _loaded[root] = None
            if root and _loaded[root] is None:
                logger.warning("No usable back-cast data in %s", root)
        return _loaded[root]


def plan_backcast(sources, kind: str, params):
    """(sources to read, ``fuel_source.backcast`` info or None) for a provincial-default run.

    Only "provincial" windows are back-cast (Alberta cells; the City lattice and uniform /
    national runs are not). Undated runs and scenarios after the grid's vintage year get no
    info (unchanged)."""
    from firesim.data.backcast import not_applied, plan

    start = getattr(params, "start_time", None)
    data = backcast_data()
    bc, info = plan(data, start)
    if info is None:
        return sources, None
    if kind != "provincial":
        why = ("outside Alberta" if kind in ("national", "uniform")
               else "the City of Edmonton grid area is not back-cast (City LiDAR 2025)")
        return sources, not_applied(info["as_of"],
                                    f"back-cast covers Alberta-grid windows only ({why})")
    if bc is None:
        return sources, info
    return dataclasses.replace(sources, backcast=bc), info


def describe_backcast(sources, stats: dict) -> dict:
    """``fuel_source.backcast`` for a window that was back-cast."""
    from firesim.data.backcast import describe

    return describe(sources.backcast, stats)
