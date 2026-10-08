"""Per-cell crown base height / crown fuel load layers on the fuel grid."""

import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions, fbp_for_conditions

# Weather where C-2 does not crown at its default 3 m base but does at 0.5 m
_COND = SpreadConditions(wind_speed=10.0, wind_direction=270.0, ffmc=84.0, dmc=20.0, dc=150.0)


def _grid(cbh=None, cfl=None, n=20):
    return FuelGrid(
        fuel_types=[[FuelType.C2] * n for _ in range(n)],
        lat_min=53.49, lat_max=53.51, lng_min=-113.52, lng_max=-113.48,
        rows=n, cols=n,
        cbh=None if cbh is None else [[cbh] * n for _ in range(n)],
        cfl=None if cfl is None else [[cfl] * n for _ in range(n)],
    )


def test_default_grid_has_no_canopy_override():
    assert _grid().get_canopy_at(53.5, -113.5) == (None, None)


def test_canopy_lookup_and_outside_grid():
    g = _grid(cbh=0.5, cfl=0.3)
    assert g.get_canopy_at(53.5, -113.5) == (0.5, 0.3)
    assert g.get_canopy_at(54.0, -113.5) == (None, None)


def test_low_cbh_turns_surface_fire_into_crown_fire():
    default = fbp_for_conditions(_COND, FuelType.C2)
    low = fbp_for_conditions(_COND, FuelType.C2, cbh=0.5)
    assert default.cfb == 0.0
    assert low.cfb > 0.0
    assert low.hfi > default.hfi
    # FBP spread rate does not depend on CBH outside C-6
    assert low.ros_final == pytest.approx(default.ros_final)


def test_cfl_scales_crown_consumption():
    a = fbp_for_conditions(_COND, FuelType.C2, cbh=0.5, cfl=0.4)
    b = fbp_for_conditions(_COND, FuelType.C2, cbh=0.5, cfl=0.8)
    assert b.cfc == pytest.approx(2.0 * a.cfc)


@pytest.mark.parametrize("cbh,expect_crown", [(None, False), (0.5, True)])
def test_cellular_uses_cell_cbh(cbh, expect_crown):
    frames = run_cellular_simulation(
        dict(ignition_lat=53.5, ignition_lng=-113.5, duration_hours=0.5),
        _grid(cbh=cbh), _COND,
    )
    types = {c.fire_type for f in frames for c in f.burned_cells}
    assert ("surface" not in types or len(types) > 1) == expect_crown
