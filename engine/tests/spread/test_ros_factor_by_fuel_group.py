"""Opt-in ROS factor by FBP fuel group (``run_cellular_simulation(ros_factor_by_fuel_group=...)``).

PREREG_FRONT1 arm B ([R60]): a per-fuel-group rate-of-spread factor, applied through the same
per-cell multiplier as the WUI modifiers. Off by default: no factor, factor 1 and a factor for an
absent fuel group must give bit-identical arrival times; a factor < 1 on the burning group must
slow the fire roughly in proportion, and only on that group's cells. Also checks the harness
option and the ``--ros-factors`` parser of ``scripts/validate.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions

REPO = Path(__file__).resolve().parents[3]
CELL = 0.0005  # degrees (~55 m north-south)


def _grid(fuels: list[list[FuelType]]) -> FuelGrid:
    rows, cols = len(fuels), len(fuels[0])
    return FuelGrid(fuel_types=fuels, lat_min=53.5, lat_max=53.5 + rows * CELL,
                    lng_min=-113.5, lng_max=-113.5 + cols * CELL, rows=rows, cols=cols)


COND = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=90.0, dmc=45.0, dc=300.0)


def _arrival(grid: FuelGrid, hours: float = 1.0, **kw) -> np.ndarray:
    lat = (grid.lat_min + grid.lat_max) / 2
    lng = grid.lng_min + 6 * CELL
    frames = run_cellular_simulation(
        config={"ignition_lat": lat, "ignition_lng": lng, "duration_hours": hours},
        fuel_grid=grid, conditions=COND, snapshot_interval_minutes=hours * 60.0,
        compute_perimeter=False, **kw)
    return frames[-1].arrival


@pytest.fixture(scope="module")
def conifer():
    return _grid([[FuelType.C2] * 60 for _ in range(30)])


def test_default_none_and_unit_factors_are_identical(conifer):
    base = _arrival(conifer)
    assert np.array_equal(base, _arrival(conifer, ros_factor_by_fuel_group=None))
    assert np.array_equal(base, _arrival(conifer, ros_factor_by_fuel_group={}))
    assert np.array_equal(base, _arrival(conifer, ros_factor_by_fuel_group={"C": 1.0, "M": 1.0}))


def test_factor_for_absent_group_has_no_effect(conifer):
    base = _arrival(conifer)
    assert np.array_equal(base, _arrival(conifer, ros_factor_by_fuel_group={"O": 0.5, "M": 0.3}))


def test_factor_slows_the_burning_group_in_proportion(conifer):
    base = _arrival(conifer)
    slow = _arrival(conifer, ros_factor_by_fuel_group={"C": 0.5})
    burned_b, burned_s = np.isfinite(base), np.isfinite(slow)
    assert burned_s.sum() < burned_b.sum()
    assert not (burned_s & ~burned_b).any()  # never burns a cell the baseline did not
    # Head run (cells east of the ignition along its row): half the rate, about half the distance
    row = conifer.rows // 2
    reach_b = np.nonzero(burned_b[row])[0].max()
    reach_s = np.nonzero(burned_s[row])[0].max()
    assert 0.35 <= (reach_s - 6) / (reach_b - 6) <= 0.65


def test_factor_acts_only_on_its_group():
    # West half conifer (ignition there), east half grass: a grass factor must not change the
    # conifer arrivals before the fire reaches grass (to the level-set time-step tolerance: the
    # Courant step depends on the fastest cell in the window, which can be a grass cell), and must
    # slow the run through the grass.
    fuels = [[FuelType.C2] * 30 + [FuelType.O1b] * 30 for _ in range(30)]
    g = _grid(fuels)
    base = _arrival(g, hours=2.0)
    gr = _arrival(g, hours=2.0, ros_factor_by_fuel_group={"O": 0.5})
    assert np.isfinite(base[:, 30:]).any()  # the fire reaches the grass
    assert np.isfinite(gr[:, 30:]).sum() < np.isfinite(base[:, 30:]).sum()
    first_grass = np.nanmin(np.where(np.isfinite(base[:, 30:]), base[:, 30:], np.nan))
    early = np.isfinite(base[:, :30]) & (base[:, :30] < first_grass)
    assert early.any()
    assert np.allclose(base[:, :30][early], gr[:, :30][early], rtol=0.02)


def test_harness_option_default_off():
    from firesim.validation.harness import RunOptions

    assert RunOptions().ros_factor_by_fuel_group is None


def test_validate_parse_ros_factors():
    spec = importlib.util.spec_from_file_location("validate_rosf_test", REPO / "scripts/validate.py")
    V = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(V)
    assert V.parse_ros_factors(None) is None
    assert V.parse_ros_factors("") is None
    assert V.parse_ros_factors("C=0.55,m=0.55,O=0.5") == {"C": 0.55, "M": 0.55, "O": 0.5}
    with pytest.raises(ValueError):
        V.parse_ros_factors("X=0.5")
    with pytest.raises(ValueError):
        V.parse_ros_factors("C=0")
