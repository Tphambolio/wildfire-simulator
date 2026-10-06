"""Albini / Chase / Morris maximum spotting distance against the published worked examples."""

import pytest

from firesim.spread.albini import (
    critical_cover_height,
    max_spot_distance,
    surface_fire_spot_distance,
    torching_tree_spot_distance,
    wind_20ft_from_10m,
)

MILE = 1609.344


def test_chase_1981_torching_example():
    """Chase 1981 Fig. 3 (p.12): grand fir, d.b.h. 20 in, 137 ft, cover 130 ft, 20 mi/h,
    one tree: flat-terrain distance 0.34 mi."""
    d = torching_tree_spot_distance(
        "grand_fir", dbh_cm=20 * 2.54, tree_height=137 * 0.3048, cover_height=130 * 0.3048,
        u20_kmh=20 * 1.609344, n_trees=1,
    )
    assert d / MILE == pytest.approx(0.34, abs=0.01)


def test_albini_1983_surface_example():
    """Albini 1983 Example 1 as corrected by Chase 1984 (p.21): NFFL model 1, I = 2000 kW/m,
    U10 = 5 m/s: total 0.45 km (0.30 km flat + 151 m drift)."""
    d = surface_fire_spot_distance(2000.0, 5.0 * 3.6, 0.0, a=545.0, b=-1.21)
    assert d / 1000.0 == pytest.approx(0.45, abs=0.01)


def test_critical_cover_height():
    """Albini 1981 p.8 (metric): h_c = z0^0.337 - 1.22; 2.93 m for H = 68.2 m (Albini 1983 ex.)."""
    assert critical_cover_height(68.2) == pytest.approx(2.93, abs=0.01)


def test_wind_conversion():
    """U(6.1 m) = 0.93 U(10 m) (Albini 1983 p.13)."""
    assert wind_20ft_from_10m(10.0) / 10.0 == pytest.approx(0.93, abs=0.005)


def test_more_trees_loft_higher():
    one = torching_tree_spot_distance("lodgepole_pine", 20.0, 18.0, 18.0, 30.0, 1)
    ten = torching_tree_spot_distance("lodgepole_pine", 20.0, 18.0, 18.0, 30.0, 10)
    assert ten > one > 0


@pytest.mark.parametrize("fuel,hfi,cfb", [("O1a", 5000.0, 0.0), ("C2", 15000.0, 0.5), ("C3", 20000.0, 0.95)])
def test_distance_increases_with_wind(fuel, hfi, cfb):
    distances = [max_spot_distance(fuel, hfi, cfb, u) for u in (10.0, 20.0, 30.0, 50.0)]
    assert all(a < b for a, b in zip(distances, distances[1:]))


def test_surface_distance_increases_with_intensity():
    assert surface_fire_spot_distance(10000.0, 20.0, 0.5) > surface_fire_spot_distance(2000.0, 20.0, 0.5)
