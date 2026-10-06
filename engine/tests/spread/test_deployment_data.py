"""Grid-run data for resource deployment: per-cell speed and head/flank/back, head summary,
arrival raster.

Checks against the FBP fire ellipse (ST-X-3 eqs 79-89 via cffdrs-verified calculate_fbp):
on uniform fuel at equilibrium the fastest head cell spreads at the FBP head ROS and the
fastest back cell at BROS; the head direction follows RAZ, including on slope.
"""

import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions, TerrainGrid, fbp_for_conditions
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput

LAT0, LNG0 = 53.5, -113.5
COND = SpreadConditions(wind_speed=20, wind_direction=270, ffmc=92, dmc=40, dc=300, grass_cure=100)


def _grid(fuel, n=160, cell=25.0):
    dlat = n * cell / 111320
    dlng = n * cell / (111320 * math.cos(math.radians(LAT0)))
    box = (LAT0 - dlat / 2, LAT0 + dlat / 2, LNG0 - dlng / 2, LNG0 + dlng / 2)
    return FuelGrid([[fuel] * n for _ in range(n)], *box, n, n), box


def _run(fuel, terrain=None, cond=COND, hours=1.0):
    g, _ = _grid(fuel)
    return run_cellular_simulation(
        dict(ignition_lat=LAT0, ignition_lng=LNG0 - 0.01, duration_hours=hours), g, cond,
        terrain_grid=terrain, acceleration=False, snapshot_interval_minutes=20,
    )


@pytest.mark.parametrize("fuel", [FuelType.C2, FuelType.O1a])
def test_head_and_back_speeds_match_fbp(fuel):
    frames = _run(fuel)
    fbp = fbp_for_conditions(COND, fuel)
    cells = [c for c in frames[-1].burned_cells if c.arrival_min > 15]
    by = {p: [c.ros for c in cells if c.part == p] for p in ("head", "flank", "back")}
    assert all(by.values()), {k: len(v) for k, v in by.items()}
    assert max(by["head"]) == pytest.approx(fbp.ros_final, rel=0.05)
    # the rearmost back cell (on the fire axis) backs at BROS
    rear = min((c for c in cells if c.part == "back"), key=lambda c: c.lng)
    assert rear.ros == pytest.approx(fbp.back_ros, rel=0.10)
    assert np.median(by["head"]) > np.median(by["flank"]) > np.median(by["back"])


@pytest.mark.parametrize("fuel", [FuelType.C2, FuelType.O1a])
def test_head_summary(fuel):
    frames = _run(fuel)
    fbp = fbp_for_conditions(COND, fuel)
    heads = [f.head for f in frames if f.head]
    assert heads
    for h in heads:
        assert h["ros"] == pytest.approx(fbp.ros_final, rel=0.05)
        assert abs((h["raz"] - fbp.raz + 180) % 360 - 180) < 2.0
        assert h["lng"] >= LNG0 - 0.0101  # at or downwind (east) of the ignition
    assert frames[-1].mean_ros == pytest.approx(fbp.ros_final, rel=0.05)


def test_head_direction_follows_raz_on_slope():
    # 40 % slope rising to the north (aspect = upslope azimuth 0, as in fbp_for_conditions),
    # wind from the west: the head runs between north and east
    g, (s, n_, w, e) = _grid(FuelType.C2)
    terrain = TerrainGrid([[40.0] * g.cols for _ in range(g.rows)],
                          [[0.0] * g.cols for _ in range(g.rows)], s, n_, w, e, g.rows, g.cols)
    frames = run_cellular_simulation(
        dict(ignition_lat=LAT0, ignition_lng=LNG0, duration_hours=1.0), g, COND,
        terrain_grid=terrain, acceleration=False, snapshot_interval_minutes=20,
    )
    fbp = fbp_for_conditions(COND, FuelType.C2, slope_pct=40.0, aspect_deg=0.0)
    assert 0.0 < fbp.raz < 90.0  # between upslope (N) and downwind (E)
    heads = [f.head for f in frames if f.head]
    assert heads
    for h in heads:
        assert abs((h["raz"] - fbp.raz + 180) % 360 - 180) < 2.0


def test_arrival_raster_matches_burned_cells():
    g, _ = _grid(FuelType.C2, n=80)
    config = SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0), 0.5,
                              snapshot_interval_minutes=15.0, ffmc=92.0, dmc=40.0, dc=300.0)
    frames = list(Simulator(config, fuel_grid=g).run())
    assert all(f.arrival_raster is None for f in frames[:-1])
    raster = frames[-1].arrival_raster
    minutes = raster["minutes"]
    assert minutes.shape == (g.rows, g.cols) and minutes.dtype == np.int16
    assert int((minutes >= 0).sum()) == len(frames[-1].burned_cells)
    assert minutes.max() <= 30
    # each cell's rounded arrival matches its 't'
    cell_lat = (g.lat_max - g.lat_min) / g.rows
    cell_lng = (g.lng_max - g.lng_min) / g.cols
    for c in frames[-1].burned_cells[::25]:
        r = int((g.lat_max - c["lat"]) / cell_lat)
        k = int((c["lng"] - g.lng_min) / cell_lng)
        assert abs(int(minutes[r, k]) - c["t"]) <= 1
    assert {"ros", "part"} <= frames[-1].burned_cells[0].keys()
    assert frames[-1].head is None or frames[-1].head["max_spot_distance_m"] > 0
