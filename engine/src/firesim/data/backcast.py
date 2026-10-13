"""Pre-fire (back-cast) fuel for historical scenarios on the Alberta Wildfire 2025 grid.

The 2025 grid shows burns as post-fire fuel (grass O-1, vegetated non-fuel 105, young
deciduous). A scenario dated before a burn would meet that post-fire fuel where the fire found
forest. For a scenario dated ``S`` the back-cast keeps every Alberta-grid cell except those
NBAC records as burned on or after ``S`` (``restore key >= S``: every burn of a later year up
to 2025, and burns of the scenario's own year not recorded as finished before it); those take
the fuel typed from the newest SCANFI v2 epoch **before the scenario's year**, prebuilt by
``scripts/fuelgrid/backcast.py``. See docs/fuel-grid-backcast.md.

What it does not do (heuristic or open, stated in the run's ``fuel_source.backcast``):
burns *before* the scenario are left at their 2025 state (no young-burn rule); harvest since
the scenario is not restored; City LiDAR cells and grids outside Alberta are unchanged; a fire
burning on the scenario date (its NBAC polygon has no end date before it) is restored in full,
including the part already burned (a restart perimeter covers that ground).

The data directory holds ``backcast.json`` (written by the build) and the rasters it names, all
on the Alberta grid's 100 m lattice; reads are windowed (``read_codes_on_lattice``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np

#: Restore keys are YYYYDDD; YYYY366 = burn of year YYYY with no recorded end date
NO_END_DOY = 366

BACKCAST_SOURCE = ("NBAC burned areas 1985-2025 (Natural Resources Canada, 2026-05-13 release); "
                   "pre-fire fuel typed from SCANFI v2 epochs (Natural Resources Canada) with the "
                   "CFS national FBP decision rules")
BACKCAST_ATTRIBUTION = ("Contains information licensed under the Open Government Licence - "
                        "Canada (NBAC, SCANFI v2).")
BACKCAST_HEURISTIC = ("Heuristic: fuel types are typed from 30 m forest-inventory estimates "
                      "(SCANFI) with the CFS national rules, not observed; burns before the "
                      "scenario stay at their 2025 state; harvest since the scenario is not "
                      "restored.")


def scenario_key(d: date | datetime) -> int:
    """``YYYYDDD`` of a date (a datetime's own calendar date)."""
    if isinstance(d, datetime):
        d = d.date()
    return d.year * 1000 + d.timetuple().tm_yday


@dataclass(frozen=True)
class BackcastData:
    """A built back-cast data set (``backcast.json`` and its rasters)."""

    root: str
    burn_key: str
    #: (epoch year, path), oldest first
    epochs: tuple[tuple[int, str], ...]
    #: last burn year the base grid reflects (2025): scenarios in later years are unchanged
    vintage_year: int = 2025

    @classmethod
    def load(cls, root: str | None) -> "BackcastData | None":
        """The data set in ``root``, or None when it is absent or incomplete."""
        if not root:
            return None
        man_path = os.path.join(root, "backcast.json")
        if not os.path.exists(man_path):
            return None
        with open(man_path) as f:
            man = json.load(f)
        burn = os.path.join(root, man["burns"]["file"])
        epochs = tuple(sorted((int(e), os.path.join(root, p)) for e, p in man["epochs"].items()
                              if os.path.exists(os.path.join(root, p))))
        if not os.path.exists(burn) or not epochs:
            return None
        return cls(root=root, burn_key=burn, epochs=epochs,
                   vintage_year=int(man.get("grid_vintage_year", 2025)))

    def epoch_for(self, year: int) -> tuple[int, str] | None:
        """Newest epoch strictly before ``year`` (it cannot show that year's burns), or None."""
        older = [(e, p) for e, p in self.epochs if e < year]
        return older[-1] if older else None


@dataclass(frozen=True)
class Backcast:
    """A back-cast request for a run: the data set and the scenario date (``YYYYDDD``)."""

    data: BackcastData
    scenario: int

    @property
    def year(self) -> int:
        return self.scenario // 1000

    @property
    def epoch(self) -> tuple[int, str] | None:
        return self.data.epoch_for(self.year)


def plan(data: BackcastData | None, start: date | datetime | None
         ) -> tuple[Backcast | None, dict | None]:
    """(back-cast to apply or None, ``fuel_source.backcast`` info or None) for a run.

    No info for undated runs or scenarios after the grid's vintage year (unchanged runs). For a
    scenario in or before the vintage year the info says whether the back-cast applies and why
    not when it does not (no data installed, scenario older than the first epoch)."""
    if start is None:
        return None, None
    skey = scenario_key(start)
    year = skey // 1000
    vintage = data.vintage_year if data is not None else 2025
    if year > vintage:
        return None, None
    as_of = (start.date() if isinstance(start, datetime) else start).isoformat()
    if data is None:
        return None, not_applied(as_of, "pre-fire fuel data are not installed on this server")
    bc = Backcast(data, skey)
    if bc.epoch is None:
        first = data.epochs[0][0]
        return None, not_applied(as_of, f"no pre-fire source before {year} (first SCANFI epoch "
                                        f"{first}; back-cast covers scenarios from {first + 1})")
    return bc, {"applied": True, "as_of": as_of}


def not_applied(as_of: str, reason: str) -> dict:
    """``fuel_source.backcast`` for a historical run that was not back-cast."""
    return {"applied": False, "as_of": as_of, "reason": reason,
            "note": note_not_applied(as_of, reason)}


def compose(base: np.ndarray, key: np.ndarray, prefire: np.ndarray, skey: int
            ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(codes, restored, unrestored) on one lattice.

    ``base``: Alberta codes; ``key``: restore keys (<= 0 = no burn); ``prefire``: epoch codes
    (<= 0 = no value). A cell burned on or after the scenario (``key >= skey``) inside the
    Alberta grid (base code mapped, not no-data) takes the epoch code when there is one
    (``restored``) and keeps the base code otherwise (``unrestored``)."""
    from firesim.data.fuel_loader import AB_WILDFIRE_NODATA

    in_ab = ~np.isin(base, list(AB_WILDFIRE_NODATA))
    burned_after = (key > 0) & (key >= skey) & in_ab
    has = prefire > 0
    restored = burned_after & has
    out = np.where(restored, prefire, base).astype(base.dtype, copy=False)
    return out, restored, burned_after & ~has


def apply_on_lattice(bc: Backcast, lattice, ab_codes: np.ndarray) -> tuple[np.ndarray, dict]:
    """Back-cast Alberta codes already read on ``lattice`` (``provincial.Lattice``).

    Returns (codes, stats) with the shares of the lattice's cells restored / left unrestored
    and the fuel groups before and after in the restored cells."""
    from firesim.data.provincial import read_codes_on_lattice

    epoch, path = bc.epoch
    key = read_codes_on_lattice(bc.data.burn_key, lattice)
    pre = read_codes_on_lattice(path, lattice)
    codes, restored, unrestored = compose(ab_codes, key, pre, bc.scenario)
    n = float(ab_codes.size)
    stats = {"epoch": epoch, "restored_share": round(float(restored.sum()) / n, 4),
             "unrestored_share": round(float(unrestored.sum()) / n, 4),
             "restored_before": _groups(ab_codes[restored]),
             "restored_after": _groups(codes[restored])}
    return codes, stats


def _groups(codes: np.ndarray) -> dict[str, float]:
    """Share of cells per fuel group of Alberta codes (C, D, M, O-1, S, non-fuel)."""
    if codes.size == 0:
        return {}
    c = codes.astype(np.int64)
    groups = {"C": (c >= 1) & (c <= 7), "D": c == 13, "M": (c >= 610) & (c <= 690),
              "O-1": c == 31, "S": (c >= 21) & (c <= 23)}
    out = {k: round(float(m.mean()), 4) for k, m in groups.items() if m.any()}
    nf = 1.0 - sum(float(m.mean()) for m in groups.values())
    if nf > 1e-9:
        out["non-fuel"] = round(nf, 4)
    return out


def note_applied(as_of: str, epoch: int, restored_share: float) -> str:
    """Plain-language line for the fuel-vintage warning when the back-cast was applied."""
    return (f"Pre-fire fuel applied: areas burned since {as_of} ({100 * restored_share:.0f} % of "
            f"the fuel window) were restored to the fuel estimated from {epoch} forest data "
            "(SCANFI). Estimated, not observed.")


def note_not_applied(as_of: str, reason: str) -> str:
    """Plain-language line for the fuel-vintage warning when the back-cast was not applied."""
    return f"Pre-fire fuel not applied for {as_of}: {reason}."


def describe(bc: Backcast, stats: dict) -> dict:
    """The ``fuel_source.backcast`` fields for an applied back-cast."""
    as_of = date.fromordinal(date(bc.year, 1, 1).toordinal() + bc.scenario % 1000 - 1).isoformat()
    epoch = stats["epoch"]
    return {
        "applied": True,
        "as_of": as_of,
        "epoch": epoch,
        "restored_share": stats["restored_share"],
        "unrestored_share": stats["unrestored_share"],
        "restored_before": stats["restored_before"],
        "restored_after": stats["restored_after"],
        "label": (f"back-cast to {as_of}: cells burned since then (NBAC) restored from SCANFI "
                  f"v2 {epoch}"),
        "note": note_applied(as_of, epoch, stats["restored_share"]),
        "source": BACKCAST_SOURCE,
        "attribution": BACKCAST_ATTRIBUTION,
        "heuristic": BACKCAST_HEURISTIC,
    }
