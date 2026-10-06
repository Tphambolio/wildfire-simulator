"""FireSim against WISE, an independent Canadian fire growth model.

``data/wise_reference.json`` holds WISE 1.0.6-beta.6 results for uniform C-2 and O-1a fuel
(wind 0/20/30 km/h, FFMC 92, BUI 60, 1 h and 2 h). WISE and FireSim share the FBP System but
not code, so agreement here checks the spread geometry (Huygens wavelets, acceleration,
level-set grid) independently of FireSim's own FBP-ellipse tests.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FuelGrid
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput

REF = json.loads((Path(__file__).parent / "data" / "wise_reference.json").read_text())
ROWS = {(r["job"], int(r["hours"])): r for r in REF["rows"]}
LAT0, LNG0 = 53.5, -113.5
FUELS = {"c2": (FuelType.C2, 60.0), "o1a": (FuelType.O1a, 100.0)}


def _run(job: str, grid_m: float | None, acceleration: bool = True):
    fuel_key, ws = job.split("_ws")
    ws = float(ws.split("_")[0])
    fuel, cure = FUELS[fuel_key]
    cfg = SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 30.0, ws, 270.0, 0.0), 2.0,
                           snapshot_interval_minutes=60.0, ffmc=92.0, dmc=40.0, dc=300.0,
                           grass_cure=cure, fmc=100.0)
    fg = None
    if grid_m:
        n = int(16000 / grid_m)
        dlat = n * grid_m / 111320.0
        dlng = n * grid_m / (111320.0 * math.cos(math.radians(LAT0)))
        # ignition 30 % from the west edge so a 9 km head run stays on the grid
        fg = FuelGrid([[fuel] * n for _ in range(n)], LAT0 - dlat / 2, LAT0 + dlat / 2,
                      LNG0 - 0.30 * dlng, LNG0 + 0.70 * dlng, n, n)
    frames = list(Simulator(cfg, fuel_grid=fg, default_fuel=fuel, acceleration=acceleration).run())
    out = {}
    for f in frames:
        h = round(f.time_hours)
        if h in (1, 2) and abs(f.time_hours - h) < 1e-6:
            pts = [(c["lat"], c["lng"]) for c in f.burned_cells] if grid_m else f.perimeter
            x = (np.array([p[1] for p in pts]) - LNG0) * 111320.0 * math.cos(math.radians(LAT0))
            out[h] = (f.area_ha, x.max())
    return out


@pytest.mark.parametrize("job", ["c2_ws0", "c2_ws20", "c2_ws30", "o1a_ws0", "o1a_ws20", "o1a_ws30"])
def test_huygens_matches_wise(job):
    res = _run(job, None)
    for h in (1, 2):
        ref = ROWS[(job, h)]
        area, head = res[h]
        assert area == pytest.approx(ref["area_ha"], rel=0.07), f"{job} {h}h area"
        assert head == pytest.approx(ref["head_m"], rel=0.03), f"{job} {h}h head"


@pytest.mark.parametrize("job", ["c2_ws20_noaccel_poly1m", "o1a_ws20_noaccel_poly1m"])
def test_huygens_equilibrium_matches_wise(job):
    res = _run(job.replace("_noaccel_poly1m", ""), None, acceleration=False)
    for h in (1, 2):
        ref = ROWS[(job, h)]
        assert res[h][0] == pytest.approx(ref["area_ha"], rel=0.05)
        assert res[h][1] == pytest.approx(ref["head_m"], rel=0.03)


@pytest.mark.parametrize("job", ["c2_ws20", "o1a_ws30"])
def test_grid_matches_wise(job):
    res = _run(job, 50.0)
    ref = ROWS[(job, 2)]
    area, head = res[2]
    assert area == pytest.approx(ref["area_ha"], rel=0.08)
    assert head == pytest.approx(ref["head_m"], rel=0.04)
