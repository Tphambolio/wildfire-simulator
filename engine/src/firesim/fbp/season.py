"""Leaf-on / leaf-off choice for the seasonal FBP fuel pairs (D-1/D-2, M-1/M-2, M-3/M-4).

Provincial and national fuel grids carry one code for each seasonal pair (Alberta Wildfire
13 "D-1/D-2" and 6xx "M-1/M-2"; CIFFC 13, 6xx, 9xx; the 2014b grid's D-1 and M-1 labels). The
loaders map them to the leafless type (D-1, M-1, M-3) and the run picks the green type
(D-2, M-2, M-4) between green-up and leaf-off.

The dates are the ones the validation harness has used for Alberta's boreal since 2026-10-06
(``firesim.validation.harness.RunOptions``): green-up day of year 150 (30 May; the end of
FireSim's pre-green-up grass-curing window, ``firesim.fbp.curing``) and leaf-off day 258
(15 September). They are a fixed regional default, not an observation: early or late springs move
green-up by weeks (Pickell et al. 2017). Grids that already state the season per cell (the City
of Edmonton LiDAR grid's D-2 / M-2, or explicit D-1/D-2 codes in a user grid) are not touched,
because only the leafless labels are swapped.

With no date the leafless type is kept: D-1 and M-1 spread faster than D-2 and M-2 (D-2 does not
carry fire below BUI 80), so an undated run errs toward more spread.
"""

from __future__ import annotations

import dataclasses

from firesim.fbp.constants import FuelType

GREENUP_DOY = 150  # 30 May (non-leap year)
LEAFOFF_DOY = 258  # 15 September

_GREEN = {FuelType.D1: FuelType.D2, FuelType.M1: FuelType.M2, FuelType.M3: FuelType.M4}


def is_leaf_on(day_of_year: int | None, greenup_doy: int = GREENUP_DOY,
               leafoff_doy: int = LEAFOFF_DOY) -> bool:
    """True between green-up (inclusive) and leaf-off (exclusive); False without a date."""
    if day_of_year is None:
        return False
    return greenup_doy <= int(day_of_year) < leafoff_doy


def seasonal_fuel(ft: FuelType | None, day_of_year: int | None,
                  greenup_doy: int = GREENUP_DOY, leafoff_doy: int = LEAFOFF_DOY) -> FuelType | None:
    """The green type of a leafless label (D-1 -> D-2, M-1 -> M-2, M-3 -> M-4) when leaves are
    on; any other type unchanged."""
    if ft in _GREEN and is_leaf_on(day_of_year, greenup_doy, leafoff_doy):
        return _GREEN[ft]
    return ft


def season_label(day_of_year: int | None) -> str:
    """"leaf-on", "leaf-off" or "leaf-off (no date)" for reports."""
    if day_of_year is None:
        return "leaf-off (no date)"
    return "leaf-on" if is_leaf_on(day_of_year) else "leaf-off"


def apply_season(grid, day_of_year: int | None):
    """A copy of a ``FuelGrid`` with the leafless labels swapped for the green types when leaves
    are on. Returns ``grid`` itself when nothing changes (never mutates the cached grid)."""
    if not is_leaf_on(day_of_year):
        return grid
    swap = False
    for row in grid.fuel_types:
        if any(ft in _GREEN for ft in row):
            swap = True
            break
    if not swap:
        return grid
    fuel_types = [[_GREEN.get(ft, ft) if ft is not None else None for ft in row]
                  for row in grid.fuel_types]
    return dataclasses.replace(grid, fuel_types=fuel_types)
