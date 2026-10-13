"""Regression test (2026-10-13): spot fires from FireSim's short-range spotting must spread.

Before the fix a spot that landed outside the bounding box of the burned area (the level set's
computational window) was reset to unburned as an "entering" cell on the next step: its arrival
time was recorded but it never spread (PROJECT_RECORD bug entry 2026-10-13).
"""

import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions


def test_albini_spots_spread_beyond_the_window():
    h, lat0, n = 90.0, 39.7, 90
    cl, cg = h / 111320.0, h / (111320.0 * math.cos(math.radians(lat0)))
    fg = FuelGrid([[FuelType.C4] * n for _ in range(n)], lat0 - n * cl, lat0, -121.5, -121.5 + n * cg, n, n)
    cond = SpreadConditions(wind_speed=50.0, wind_direction=225.0, ffmc=95.0, dmc=120.0, dc=800.0)
    cfg = {"ignition_lat": lat0 - 75.5 * cl, "ignition_lng": -121.5 + 15.5 * cg, "duration_hours": 2.0}
    plain = run_cellular_simulation(cfg, fg, cond, compute_perimeter=False, seed=2, dt_minutes=10)
    spot = run_cellular_simulation(cfg, fg, cond, compute_perimeter=False, seed=2, dt_minutes=10,
                                   enable_spotting=True)
    if sum(len(f.spot_fires or []) for f in spot) == 0:
        pytest.skip("no spot drawn at this seed")
    assert np.isfinite(spot[-1].arrival).sum() > 1.2 * np.isfinite(plain[-1].arrival).sum()
