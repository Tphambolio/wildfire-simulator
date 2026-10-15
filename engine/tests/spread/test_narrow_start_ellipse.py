"""Regression (2026-10-15, long-range embers v7): a point ignition under strong wind must not stall.

At high wind the FBP ellipse is long and thin (C-7 at 61 km/h: LB ~ 7). The exact starting ellipse
covered a head run of START_CELLS cells only, so on a coarse grid it was narrower than a cell; with its
axis off the grid lines it held only the ignition cell and the level set could not advance (C-7, 180 m
cells, 61 km/h from 20 deg: 2 cells burned in 2 h; from 0 deg, along a grid line, 66). The starting
ellipse is now extended until its semi-minor axis is at least one cell.
"""

import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions

LAT0 = 38.6


def _burned(h, ws, wd, hours=2.0):
    n = 60
    cl = h / 111320.0
    cg = h / (111320.0 * math.cos(math.radians(LAT0)))
    fg = FuelGrid([[FuelType.C7] * n for _ in range(n)], LAT0 - n * cl, LAT0, -122.6, -122.6 + n * cg, n, n)
    cond = SpreadConditions(wind_speed=ws, wind_direction=wd, ffmc=96.0, dmc=100.0, dc=800.0, fmc=90.0)
    cfg = {"ignition_lat": LAT0 - 15.5 * cl, "ignition_lng": -122.6 + 30.5 * cg, "duration_hours": hours}
    return int(np.isfinite(run_cellular_simulation(cfg, fg, cond, compute_perimeter=False, dt_minutes=10)[-1].arrival).sum())


@pytest.mark.parametrize("h", [90.0, 180.0])
def test_strong_wind_off_axis_point_ignition_spreads(h):
    off_axis = _burned(h, 61.0, 20.0)
    on_axis = _burned(h, 61.0, 0.0)
    assert off_axis > 20
    # the direction of the wind relative to the grid must not change the burned area much
    assert abs(off_axis - on_axis) <= 0.35 * max(on_axis, off_axis)


def test_stronger_wind_not_slower_than_moderate():
    assert _burned(180.0, 61.0, 20.0) >= _burned(180.0, 30.0, 20.0)
