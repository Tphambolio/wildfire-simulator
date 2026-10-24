"""Active / inactive segment classification of an observed perimeter (backlog A1).

A 4 km square perimeter at 55 N with known detections: the rule (within 1 km of a segment, in
the 12 h up to the observation time) is checked at its edges, for RPAS GeoJSON hotspots and
for a FIRMS VIIRS CSV (low confidence dropped), and the active segments come back as the
``active_edges`` MultiLineString the API accepts.
"""

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from firesim.active_segments import (
    choose_source,
    classify,
    classify_request,
    detections_from_firms_csv,
    hotspots_from_geojson,
    static_share_from_arrival,
)

LAT0, LNG0 = 55.0, -115.0
KX = 111320.0 * math.cos(math.radians(LAT0))
T = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)


def _ll(x_m, y_m):
    return [LNG0 + x_m / KX, LAT0 + y_m / 111320.0]


SQUARE = {"type": "Polygon", "coordinates": [[_ll(-2000, -2000), _ll(2000, -2000),
                                              _ll(2000, 2000), _ll(-2000, 2000),
                                              _ll(-2000, -2000)]]}


def _hotspots(points):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": _ll(x, y)},
         "properties": {"time": t.isoformat()}} for x, y, t in points]}


def test_segments_cover_the_boundary():
    c = classify(SQUARE, [], T, "rpas_hotspots")
    assert len(c.segments) == 64  # 16 km / 250 m
    assert sum(s["length_m"] for s in c.segments) == pytest.approx(16000.0, rel=1e-3)
    assert c.n_active == 0 and c.active_edges() is None


def test_distance_and_time_window():
    pts = [(2900, 0, T - timedelta(hours=1)),  # 900 m east of the east side: active
           (0, -3100, T - timedelta(hours=2)),  # 1,100 m south: too far
           (-2500, 0, T - timedelta(hours=13)),  # 500 m west but 13 h old: too old
           (0, 2200, T + timedelta(minutes=5))]  # after T: not used
    c = classify_request(SQUARE, T, hotspots=_hotspots(pts))
    s = c.summary()
    assert s["source"] == "rpas_hotspots" and s["n_detections_used"] == 1
    active = [seg for seg in c.segments if seg["active"]]
    # every active segment is on the east side, within 1 km of (2900, 0)
    for seg in active:
        xs = [(p[0] - LNG0) * KX for p in seg["coords"]]
        assert min(xs) > 1990
    assert 3 <= len(active) <= 5  # 2 x sqrt(1000^2 - 900^2) = 872 m of the side, 250 m pieces
    edges = c.active_edges()
    assert edges["type"] == "MultiLineString" and len(edges["coordinates"]) == len(active)
    fc = c.feature_collection()
    assert {f["properties"]["status"] for f in fc["features"]} == {"active", "inactive"}


def test_window_edges_are_inclusive():
    for dt in (timedelta(0), timedelta(hours=12)):
        c = classify_request(SQUARE, T, hotspots=_hotspots([(2500, 0, T - dt)]))
        assert c.n_active > 0


def test_firms_csv_viirs_confidence():
    csv = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,"
           "confidence,version,bright_ti5,frp,daynight\n"
           f"{_ll(2500, 0)[1]},{_ll(2500, 0)[0]},330,0.4,0.4,2026-07-15,1930,N,VIIRS,n,2,290,5,D\n"
           f"{_ll(-2500, 0)[1]},{_ll(-2500, 0)[0]},330,0.4,0.4,2026-07-15,1930,N,VIIRS,l,2,290,5,D\n"
           f"{_ll(0, 2500)[1]},{_ll(0, 2500)[0]},330,0.4,0.4,2026-07-15,0130,N,VIIRS,h,2,290,5,N\n")
    dets = detections_from_firms_csv(csv)
    assert len(dets) == 2  # the low-confidence row is dropped
    assert dets[1].time == datetime(2026, 7, 15, 1, 30, tzinfo=timezone.utc)
    c = classify_request(SQUARE, T, firms_csv=csv)
    assert c.source == "viirs_firms"
    east = [s for s in c.segments if s["active"] and min((p[0] - LNG0) * KX for p in s["coords"]) > 1990]
    north = [s for s in c.segments if s["active"] and min((p[1] - LAT0) * 111320 for p in s["coords"]) > 1990]
    west = [s for s in c.segments if s["active"] and max((p[0] - LNG0) * KX for p in s["coords"]) < -1990]
    assert east and not west
    assert not north  # 01:30 UTC is 18.5 h before T
    with pytest.raises(ValueError, match="columns"):
        detections_from_firms_csv("lat,lon\n1,2\n")


def test_hotspot_time_required_and_default():
    gj = {"type": "MultiPoint", "coordinates": [_ll(2500, 0)]}
    assert hotspots_from_geojson(gj) == []
    assert len(hotspots_from_geojson(gj, default_time=T)) == 1
    with pytest.raises(ValueError):
        hotspots_from_geojson({"type": "LineString", "coordinates": []})


def test_source_priority():
    assert choose_source({"type": "Point"}, {"type": "FeatureCollection"}, "csv") == "manual"
    assert choose_source(None, {"type": "FeatureCollection"}, "csv") == "rpas_hotspots"
    assert choose_source(None, None, "csv") == "viirs_firms"
    assert choose_source(None, None, None) is None


def test_static_share_from_arrival():
    m = np.full((60, 60), -1, dtype=np.int16)
    m[20:40, 20:40] = 0
    m[20:40, 40:52] = 30  # grows 12 cells (about 1.2 km at 100 m) east only
    lat_min, lat_max = LAT0, LAT0 + 60 * 100 / 111320.0
    lng_max = LNG0 + 60 * 100 / KX
    d = static_share_from_arrival(m, lat_min, lat_max, LNG0, lng_max)
    assert 0.5 < d["static_share_model"] < 1.0
    assert d["growth_ha"] == pytest.approx(240.0, rel=0.01)
