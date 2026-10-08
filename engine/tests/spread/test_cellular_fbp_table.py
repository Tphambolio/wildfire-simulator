"""The grid model's per-cell FBP table is vectorised; it must not change results.

``_CellParams.evaluate`` used to call ``fbp_for_conditions`` once per distinct cell type
(fuel, slope, aspect, canopy, multipliers) at every weather change. It now evaluates each
fuel / canopy group with ``fbp_ellipse_arrays``. These tests rebuild the table the old way and
check that the parameters and a full hourly run with terrain and mixed fuel are unchanged.
"""

import math

import numpy as np
import pytest

from firesim.fbp.calculator import calculate_acceleration
from firesim.fbp.constants import FuelType
from firesim.spread.cellular import _CellParams, _cell_keys, run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions, TerrainGrid, fbp_for_conditions


def _scalar_evaluate(cls, cell_keys, conditions):
    """The original one-call-per-cell-type table (reference)."""
    fuel, index, keys = cell_keys[:3]
    table = np.zeros((max(len(keys), 1), 10))
    for k, (ft, slope, aspect, cbh, cfl, rm, im) in enumerate(keys):
        f = fbp_for_conditions(conditions, ft, float(slope), float(aspect), cbh, cfl)
        m = rm * conditions.ros_multiplier
        table[k] = (f.ros_final * m, f.back_ros * m, f.flank_ros * m, f.raz, f.sfc, f.cfl,
                    f.rso if math.isfinite(f.rso) else 1e12, im,
                    calculate_acceleration(ft, f.cfb), f.lb)
    vals = np.where(fuel[..., None], table[np.maximum(index, 0)], 0.0)
    head, back, flank, raz_deg, sfc, cfl, rso, imult, alpha, lb = np.moveaxis(vals, -1, 0)
    raz = np.radians(raz_deg)
    return cls(fuel=fuel, head=head, a=(head + back) / 2.0, b=flank, c=(head - back) / 2.0,
               hx=np.sin(raz), hy=np.cos(raz), sfc=sfc, cfl=cfl, rso=rso, imult=imult,
               alpha=np.where(fuel, alpha, 0.115), lb=np.maximum(lb, 1.0))


def _landscape(n=60, cell_m=60.0, lat0=55.0, lng0=-115.0):
    """Mixed fuel with non-fuel patches on a hilly surface (slope 0-60 %, every aspect)."""
    rng = np.random.default_rng(7)
    fuels = [FuelType.C2, FuelType.C3, FuelType.M1, FuelType.D1, FuelType.O1b, FuelType.C6, None]
    codes = rng.integers(0, len(fuels), size=(n // 6, n // 6)).repeat(6, 0).repeat(6, 1)
    fuel_types = [[fuels[codes[r, c]] for c in range(n)] for r in range(n)]
    for r in range(n):  # keep a fuel path across the middle
        fuel_types[r][n // 2] = FuelType.C2
        fuel_types[n // 2][r] = FuelType.C2
    yy, xx = np.mgrid[0:n, 0:n] * cell_m
    elev = 80.0 * np.sin(xx / 900.0) * np.cos(yy / 700.0) + 0.05 * xx
    dz_s, dz_e = np.gradient(elev, cell_m, cell_m)
    slope = np.hypot(dz_s, dz_e) * 100.0
    aspect = np.degrees(np.arctan2(dz_e, -dz_s)) % 360.0
    dlat = n * cell_m / 111320.0
    dlng = n * cell_m / (111320.0 * math.cos(math.radians(lat0)))
    bounds = dict(lat_min=lat0 - dlat / 2, lat_max=lat0 + dlat / 2, lng_min=lng0 - dlng / 2,
                  lng_max=lng0 + dlng / 2, rows=n, cols=n)
    return (FuelGrid(fuel_types=fuel_types, **bounds),
            TerrainGrid(slope=slope.tolist(), aspect=aspect.tolist(), **bounds))


SCHEDULE = [
    (0.0, SpreadConditions(wind_speed=12.0, wind_direction=250.0, ffmc=88.0, dmc=40.0, dc=300.0)),
    (30.0, SpreadConditions(wind_speed=28.0, wind_direction=280.0, ffmc=92.0, dmc=40.0, dc=300.0)),
    (60.0, SpreadConditions(wind_speed=45.0, wind_direction=300.0, ffmc=94.0, dmc=40.0, dc=300.0,
                            ros_multiplier=0.8)),
]


def test_cell_params_equal_scalar_table():
    fuel_grid, terrain = _landscape()
    lat_max, lng_min = fuel_grid.lat_max, fuel_grid.lng_min
    cl = (fuel_grid.lat_max - fuel_grid.lat_min) / fuel_grid.rows
    cg = (fuel_grid.lng_max - fuel_grid.lng_min) / fuel_grid.cols
    keys = _cell_keys(fuel_grid, None, terrain,
                      lambda r, c: (lat_max - (r + 0.5) * cl, lng_min + (c + 0.5) * cg))
    assert len(keys[2]) > 200  # many distinct slope / aspect types
    for _, cond in SCHEDULE:
        new = _CellParams.evaluate(keys, cond)
        ref = _scalar_evaluate(_CellParams, keys, cond)
        for name in ("head", "a", "b", "c", "hx", "hy", "sfc", "cfl", "rso", "imult", "alpha", "lb"):
            np.testing.assert_allclose(getattr(new, name), getattr(ref, name), rtol=1e-12,
                                       atol=1e-12, err_msg=name)


def test_hourly_run_unchanged(monkeypatch):
    fuel_grid, terrain = _landscape()
    cfg = {"ignition_lat": (fuel_grid.lat_min + fuel_grid.lat_max) / 2,
           "ignition_lng": (fuel_grid.lng_min + fuel_grid.lng_max) / 2, "duration_hours": 1.5}

    def run():
        frames = run_cellular_simulation(cfg, fuel_grid, SCHEDULE[0][1], terrain_grid=terrain,
                                         weather_schedule=SCHEDULE, snapshot_interval_minutes=90,
                                         compute_perimeter=False)
        return frames[-1].arrival

    new = run()
    monkeypatch.setattr(_CellParams, "evaluate", classmethod(_scalar_evaluate))
    ref = run()
    assert np.isfinite(new).sum() > 100
    np.testing.assert_array_equal(np.isfinite(new), np.isfinite(ref))
    fin = np.isfinite(ref)
    np.testing.assert_allclose(new[fin], ref[fin], rtol=0, atol=1e-9)
