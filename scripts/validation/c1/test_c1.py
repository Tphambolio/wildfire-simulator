"""Unit checks of the C1 comparator helpers (run: python3 -m pytest scripts/validation/c1)."""
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "engine/src")]

import analyze_c1 as A  # noqa: E402
import communities as C  # noqa: E402
import wise_job as W  # noqa: E402
from firesim.fbp.constants import FuelType  # noqa: E402


def test_fuel_codes():
    assert W.fuel_code(None, None) == W.NON_FUEL
    assert W.fuel_code(FuelType.C2, None) == 102
    assert W.fuel_code(FuelType.M1, None) == 450
    assert W.fuel_code(FuelType.M2, 72.0) == 570
    assert W.fuel_code(FuelType.M1, 0.0) == 405 and W.fuel_code(FuelType.M2, 100.0) == 595
    assert W.fuel_code(FuelType.M3, None) == 735


def test_grid_convergence_sign():
    # East of the 115 W central meridian, true north lies west of grid north (README: wind
    # from 270 grid at 113.5 W 53.5 N drives the fire to 91.2 deg true)
    g = W.grid_convergence_deg(53.5, -113.5)
    assert -1.4 < g < -1.0
    assert W.grid_convergence_deg(53.5, -116.5) > 0


def test_arrival_errors_false_arrival_and_censoring():
    m = np.zeros((5, 5), bool)
    m[0, 0] = True
    comms = [{"name": "a", "mask": m, "reached": False, "obs_min": math.inf}]
    arr = np.full((5, 5), np.inf)
    arr[0, 0] = 600.0  # model reaches at 10 h, the fire never did: 7 h early
    r = C.arrival_errors(comms, arr, np.zeros((5, 5), bool))
    assert r["n_false_arrival"] == 1 and abs(r["mae_h"] - 7.0) < 1e-9
    r = C.arrival_errors(comms, np.full((5, 5), np.inf), np.zeros((5, 5), bool))
    assert r["mae_h"] == 0.0


def test_inside_and_bootstrap():
    assert A.inside_p10_p90([10, 20, 30, 40, 50], 30) == 1.0
    assert A.inside_p10_p90([10, 20, 30], 1000) == 0.0
    d = {("f1", 1): 0.1, ("f1", 2): 0.1, ("f2", 1): 0.1}
    b = A.boot_fire(d)
    assert abs(b["mean"] - 0.1) < 1e-12 and abs(b["lo"] - 0.1) < 1e-12
    v = {("f1", 1): {1: 1.0, 2: 0.0}}
    assert A.boot_fire_seed(v)["mean"] == 0.5
