"""Huygens spread against the FBP fire ellipse on uniform fuel.

Equilibrium fires should match ST-X-3's ellipse (eqs 82-86); accelerating
point-ignition fires should match the distance and LB at time t (eqs 70-73, 81).
Tolerances allow for the 5 min step and the Huygens envelope of earlier, rounder
wavelets, which make the model slightly larger than the instantaneous ellipse.
"""

import math

import pytest

from firesim.fbp.calculator import (
    calculate_acceleration,
    calculate_distance_at_time,
    calculate_lb_at_time,
)
from firesim.fbp.constants import FuelType
from firesim.spread.ellipse import calculate_ellipse_area
from firesim.spread.huygens import SpreadConditions, fbp_for_conditions
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput


def _area(fuel: FuelType, cure: float, hours: float, acceleration: bool) -> float:
    cfg = SimulationConfig(
        ignition_lat=53.5, ignition_lng=-113.5,
        weather=WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0),
        duration_hours=hours, snapshot_interval_minutes=hours * 60.0,
        ffmc=92.0, dmc=40.0, dc=300.0, grass_cure=cure, fmc=100.0,
    )
    return list(Simulator(cfg, default_fuel=fuel, acceleration=acceleration).run())[-1].area_ha


def _fbp(fuel: FuelType, cure: float):
    return fbp_for_conditions(
        SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0, dc=300.0,
                         grass_cure=cure, fmc=100.0),
        fuel,
    )


@pytest.mark.parametrize("fuel,cure", [(FuelType.C2, 60.0), (FuelType.O1a, 100.0)])
@pytest.mark.parametrize("hours,tol", [(1.0, 0.10), (2.0, 0.05)])
def test_equilibrium_area(fuel, cure, hours, tol):
    f = _fbp(fuel, cure)
    expected = calculate_ellipse_area(f.ros_final, f.back_ros, f.lb, hours)
    assert _area(fuel, cure, hours, acceleration=False) == pytest.approx(expected, rel=tol)


@pytest.mark.parametrize("fuel,cure", [(FuelType.C2, 60.0), (FuelType.O1a, 100.0)])
@pytest.mark.parametrize("hours,tol", [(1.0, 0.10), (2.0, 0.05)])
def test_accelerating_area(fuel, cure, hours, tol):
    f = _fbp(fuel, cure)
    alpha = calculate_acceleration(fuel, f.cfb)
    length = calculate_distance_at_time(f.ros_final + f.back_ros, alpha, hours * 60.0)
    breadth = length / calculate_lb_at_time(f.lb, alpha, hours * 60.0)
    expected = math.pi * length * breadth / 4.0 / 1e4
    assert _area(fuel, cure, hours, acceleration=True) == pytest.approx(expected, rel=tol)


def test_acceleration_slows_early_growth():
    assert _area(FuelType.C2, 60.0, 0.5, True) < 0.8 * _area(FuelType.C2, 60.0, 0.5, False)
