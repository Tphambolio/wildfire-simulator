"""Engine use of M8 (``m8_spot_likelihood``): relative tiers only, downwind of the active edge."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread import embers_m8 as E
from firesim.spread.huygens import FuelGrid

LAT0, LNG0 = 53.5, -113.5


def _grid(ft=FuelType.C2, km=44.0, n=440):
    dlat = km / 110.574
    dlng = km / (111.320 * math.cos(math.radians(LAT0)))
    return FuelGrid(fuel_types=[[ft] * n for _ in range(n)], lat_min=LAT0 - dlat / 2,
                    lat_max=LAT0 + dlat / 2, lng_min=LNG0 - dlng / 2, lng_max=LNG0 + dlng / 2,
                    rows=n, cols=n)


def _square(half_km=1.0):
    dlat = half_km / 110.574
    dlng = half_km / (111.320 * math.cos(math.radians(LAT0)))
    ring = [[LNG0 - dlng, LAT0 - dlat], [LNG0 + dlng, LAT0 - dlat], [LNG0 + dlng, LAT0 + dlat],
            [LNG0 - dlng, LAT0 + dlat], [LNG0 - dlng, LAT0 - dlat]]
    return {"type": "Polygon", "coordinates": [ring]}, dlat, dlng


def _east_edge():
    per, dlat, dlng = _square()
    return per, {"type": "LineString", "coordinates": [[LNG0 + dlng, LAT0 - dlat],
                                                        [LNG0 + dlng, LAT0 + dlat]]}


def _run(wind_from=270.0, wind_kmh=25.0, **kw):
    per, edge = _east_edge()
    zone = E.zone_weather_from_hours([wind_kmh], [wind_from], [92.0], 80.0)
    return E.m8_spot_likelihood(per, edge, _grid(), zone, 12.0, 100.0, 60.0, 50.0, 35.0, **kw)


def test_relative_tiers_only_no_counts_or_probabilities():
    out = _run()

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    found = " ".join(keys(out)).lower()
    for banned in ("score", "probab", "expected", "lambda", "count", "pct", "percent"):
        assert banned not in found, banned
    props = {k for f in out["tiers"]["features"] for k in f["properties"]}
    assert props == {"tier", "distance_km"}
    assert len(json.dumps(out)) < 2_000_000
    assert out["description"] == E.APPROVED_SENTENCE
    assert "not calibrated" in out["caveat"] and "margin intact" in out["caveat"]
    assert set(out["n_by_tier"]) == {"higher", "middle", "lower"}
    assert out["n_by_tier"]["higher"] > 0
    assert {f["properties"]["tier"] for f in out["tiers"]["features"]} == {"higher", "middle"}
    assert out["active_source_cells"] > 0


def test_higher_tier_lies_downwind_of_the_active_edge():
    out = _run(wind_from=270.0)  # wind from the west: embers go east
    hi = [f for f in out["tiers"]["features"] if f["properties"]["tier"] == "higher"]
    lng = np.array([f["geometry"]["coordinates"][0][0][0] for f in hi])
    assert (lng > LNG0).mean() > 0.99
    d = np.array([f["properties"]["distance_km"] for f in hi])
    assert d.min() >= 1.0 and d.max() <= 15.0
    out_s = _run(wind_from=0.0)  # from the north: south of the fire
    hi_s = [f for f in out_s["tiers"]["features"] if f["properties"]["tier"] == "higher"]
    lat = np.array([f["geometry"]["coordinates"][0][0][1] for f in hi_s])
    assert (lat < LAT0).mean() > 0.9


def test_beta_w_zero_wind_speed_changes_no_establishment_term():
    # b_w = 0: the bed-wind factor is 1 at any wind speed (wind still moves the landing field)
    A = np.random.default_rng(1).random((2, 3, 50))
    zb = np.zeros(50, int)
    s1 = E.m8_score(A, np.array([2.0]), np.array([10.0]), zb)
    s2 = E.m8_score(A, np.array([20.0]), np.array([10.0]), zb)
    np.testing.assert_array_equal(s1, s2)


def test_tier_rule_and_ties():
    s = np.array([0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9], float)
    t = E.tiers(s)
    assert t[:3].tolist() == [0, 0, 0]  # never reached: lower
    assert t[-1] == 2
    reached = np.ones(len(s), bool); reached[-1] = False
    assert E.tiers(s, reached)[-1] == 0  # outside every kernel: lower whatever its round-off
    r = E.pct_rank(np.array([1.0, 1.0, 1.0]))
    np.testing.assert_allclose(r, 0.5)


def test_previous_perimeter_sets_observed_spread_rate():
    per, edge = _east_edge()
    prev, _, _ = _square(0.5)
    zone = E.zone_weather_from_hours([25.0], [270.0], [92.0], 80.0)
    out = E.m8_spot_likelihood(per, edge, _grid(), zone, 12.0, 100.0, 60.0, 50.0, 35.0,
                               previous_perimeter_geojson=prev, previous_dt_min=120.0)
    assert out["spread_rate"].startswith("observed")
    assert _run()["spread_rate"].startswith("v8 floor")


def test_off_grid_and_non_fuel_have_no_candidates():
    per, edge = _east_edge()
    zone = E.zone_weather_from_hours([25.0], [270.0], [92.0], 80.0)
    g = _grid(km=10.0, n=100)  # grid ends 5 km out: candidates only where fuel is mapped
    out = E.m8_spot_likelihood(per, edge, g, zone, 12.0, 100.0, 60.0, 50.0, 35.0)
    assert out["fuel_grid_coverage"] < 0.2
    d = [f["properties"]["distance_km"] for f in out["tiers"]["features"]]
    assert max(d) <= 5.5


def test_hotspot_points_use_the_v8_one_km_rule():
    per, dlat, dlng = _square()
    pts = {"type": "MultiPoint", "coordinates": [[LNG0 + dlng + 0.005, LAT0]]}
    zone = E.zone_weather_from_hours([25.0], [270.0], [92.0], 80.0)
    out = E.m8_spot_likelihood(per, pts, _grid(), zone, 12.0, 100.0, 60.0, 50.0, 35.0)
    assert 0 < out["active_source_cells"] < 40


def test_zone_from_schedule_averages_hourly_conditions():
    from firesim.spread.huygens import SpreadConditions
    a = SpreadConditions(wind_speed=36.0, wind_direction=270.0, ffmc=90.0, dmc=40.0, dc=300.0)
    b = SpreadConditions(wind_speed=18.0, wind_direction=270.0, ffmc=92.0, dmc=40.0, dc=300.0)
    z = E.zone_from_schedule([(0.0, a), (120.0, b)], 3.0)  # hours 0,1 -> a; 2,3 -> b
    assert z.u10 == pytest.approx((36 + 36 + 18 + 18) / 4 / 3.6)
    assert z.ffmc == pytest.approx(91.0)
    assert z.wd_from == pytest.approx(270.0)
