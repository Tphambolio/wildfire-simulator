"""Building units and neighbour graph (docs/structure-spread-spec.md §2)."""

from __future__ import annotations

import gzip
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon, box

from firesim.structures.units import M_PER_DEG_LAT, LocalFrame, build_units

LAT0, LNG0 = 53.5, -113.5
FRAME = LocalFrame(LAT0, LNG0)


def square_lnglat(x0: float, y0: float, side: float) -> Polygon:
    """A side x side square with its south-west corner at local (x0, y0) metres, in (lng, lat)."""
    lat, lng = FRAME.to_latlng(np.array([x0, x0 + side]), np.array([y0, y0 + side]))
    return box(float(lng[0]), float(lat[0]), float(lng[1]), float(lat[1]))


def row(n: int, side: float, gap: float) -> list[Polygon]:
    return [square_lnglat(i * (side + gap), 0.0, side) for i in range(n)]


def test_frame_round_trip():
    lat, lng = FRAME.to_latlng(np.array([1000.0]), np.array([-250.0]))
    x, y = FRAME.to_local(lat, lng)
    assert x[0] == pytest.approx(1000.0, abs=1e-6)
    assert y[0] == pytest.approx(-250.0, abs=1e-6)
    assert FRAME.m_per_deg_lng == pytest.approx(M_PER_DEG_LAT * math.cos(math.radians(LAT0)))


def test_unit_fields_for_a_row_of_squares():
    units = build_units(row(4, 10.0, 8.0), frame=FRAME, neighbour_cutoff_m=30.0)
    assert len(units) == 4
    np.testing.assert_array_equal(units.ids, [0, 1, 2, 3])
    np.testing.assert_allclose(units.area_m2, 100.0, rtol=1e-6)
    np.testing.assert_allclose(units.size_m, 10.0, rtol=1e-6)
    np.testing.assert_allclose(units.x, [5.0, 23.0, 41.0, 59.0], atol=1e-6)
    np.testing.assert_allclose(units.y, 5.0, atol=1e-6)
    lat, lng = FRAME.to_latlng(units.x, units.y)
    np.testing.assert_allclose(units.lat, lat)
    np.testing.assert_allclose(units.lng, lng)
    # Gap 8 m: the next square is a neighbour (8 m), the one after is 26 m away, then 44 m
    nb, sep = units.neighbours(0)
    np.testing.assert_array_equal(nb, [1, 2])
    np.testing.assert_allclose(sep, [8.0, 26.0], atol=1e-6)
    np.testing.assert_allclose(units.nearest_separation_m, 8.0, atol=1e-6)
    assert units.n_edges == 5  # 0-1, 0-2, 1-2, 1-3, 2-3


def test_graph_is_symmetric():
    rng = np.random.default_rng(1)
    polys = [square_lnglat(float(x), float(y), float(s))
             for x, y, s in zip(rng.uniform(0, 300, 80), rng.uniform(0, 300, 80), rng.uniform(6, 20, 80))]
    units = build_units(polys, frame=FRAME, neighbour_cutoff_m=25.0)
    pairs = {}
    for i in range(len(units)):
        nb, sep = units.neighbours(i)
        assert i not in nb
        assert np.all(sep <= 25.0 + 1e-9)
        for j, d in zip(nb, sep):
            pairs[(i, int(j))] = d
    for (i, j), d in pairs.items():
        assert pairs[(j, i)] == pytest.approx(d)
    # Every pair within the cutoff is in the graph (brute force)
    geoms = units.footprints
    for i in range(len(units)):
        d = shapely.distance(geoms[i], geoms)
        expect = {int(j) for j in np.nonzero(d <= 25.0)[0] if j != i}
        assert set(units.neighbours(i)[0].tolist()) == expect


def test_cutoff_and_isolated_unit():
    units = build_units(row(2, 10.0, 40.0), frame=FRAME, neighbour_cutoff_m=30.0)
    assert units.n_edges == 0
    assert np.all(np.isinf(units.nearest_separation_m))
    units = build_units(row(2, 10.0, 40.0), frame=FRAME, neighbour_cutoff_m=45.0)
    assert units.n_edges == 1


def test_touching_footprints_have_zero_separation():
    units = build_units(row(2, 10.0, 0.0), frame=FRAME)
    assert units.neighbours(0)[1][0] == pytest.approx(0.0, abs=1e-6)


def test_bbox_clips_by_centroid():
    polys = row(5, 10.0, 10.0)  # centroids at x = 5, 25, 45, 65, 85
    lat, lng = FRAME.to_latlng(np.array([20.0, 50.0]), np.array([-10.0, 10.0]))
    units = build_units(polys, frame=FRAME, bbox=(lat[0], lat[1], lng[0], lng[1]))
    np.testing.assert_allclose(units.x, [25.0, 45.0], atol=1e-6)


def test_invalid_empty_and_none_footprints():
    bowtie = Polygon([(0, 0), (1e-4, 1e-4), (1e-4, 0), (0, 1e-4)])  # self-intersecting
    polys = [None, Polygon(), square_lnglat(0, 0, 10.0), bowtie]
    units = build_units(polys, frame=FRAME)
    assert len(units) == 2
    assert np.all(units.area_m2 > 0)


def test_empty_input():
    units = build_units([], frame=FRAME)
    assert len(units) == 0 and units.n_edges == 0
    units = build_units(row(2, 10, 5), frame=FRAME, bbox=(0.0, 1.0, 0.0, 1.0))
    assert len(units) == 0


def test_default_frame_is_centred():
    units = build_units(row(3, 10.0, 10.0))
    assert abs(units.x.mean()) < 1.0 and abs(units.y.mean()) < 1.0


def test_negative_cutoff_rejected():
    with pytest.raises(ValueError):
        build_units(row(2, 10, 5), neighbour_cutoff_m=-1)


def test_building_index_bbox_feeds_units(tmp_path):
    """BuildingIndex.building_geoms_in_bbox returns every footprint in the run area."""
    from shapely.geometry import mapping

    from firesim.data.building_index import BuildingIndex

    polys = row(6, 10.0, 10.0)
    nbhds = [square_lnglat(-50, -50, 70), square_lnglat(20, -50, 120)]

    def fc(geoms, props=None):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": mapping(g), "properties": (props[k] if props else {})}
            for k, g in enumerate(geoms)]}

    bpath, npath = tmp_path / "b.geojson", tmp_path / "n.geojson"
    bpath.write_text(json.dumps(fc(polys)))
    npath.write_text(json.dumps(fc(nbhds, [{"neighbourhood": "A"}, {"neighbourhood": "B"}])))
    idx = BuildingIndex(str(bpath), str(npath))
    lat, lng = FRAME.to_latlng(np.array([0.0, 80.0]), np.array([0.0, 10.0]))
    geoms = idx.building_geoms_in_bbox(lat[0], lat[1], lng[0], lng[1])
    assert len(geoms) == 4  # centroids x = 5, 25, 45, 65 (85 and 105 outside)
    units = build_units(geoms, frame=FRAME)
    np.testing.assert_allclose(sorted(units.x), [5.0, 25.0, 45.0, 65.0], atol=1e-3)


def test_speed_synthetic_suburb():
    """A 100 x 100 block of 12 m houses 4 m apart (10,000 units) builds quickly."""
    polys = [square_lnglat(i * 16.0, j * 16.0, 12.0) for i in range(100) for j in range(100)]
    t0 = time.perf_counter()
    units = build_units(polys, frame=FRAME, neighbour_cutoff_m=30.0)
    elapsed = time.perf_counter() - t0
    assert len(units) == 10_000
    # interior unit: 4 m to 4 sides, sqrt(32) ~ 5.66 m diagonals, 20 m and 2-away diagonals...
    interior = 50 * 100 + 50
    assert units.nearest_separation_m[interior] == pytest.approx(4.0, abs=1e-6)
    assert elapsed < 20.0


BUILDINGS = Path(__file__).resolve().parents[3] / "data" / "edmonton_buildings.geojson.gz"


@pytest.mark.skipif(not BUILDINGS.exists(), reason="Edmonton footprints not present")
@pytest.mark.skipif(os.environ.get("FIRESIM_SKIP_SLOW") == "1", reason="slow test skipped")
def test_edmonton_footprints_run_area():
    """Real footprints clipped to a 5 km x 5 km run area build in seconds."""
    with gzip.open(BUILDINGS, "rt", encoding="utf-8") as f:
        feats = json.load(f)["features"]
    geoms = shapely.from_geojson([json.dumps(ft["geometry"]) for ft in feats[:60000]])
    cent = shapely.centroid(geoms)
    clat, clng = np.median(shapely.get_y(cent)), np.median(shapely.get_x(cent))
    dlat = 2500.0 / M_PER_DEG_LAT
    dlng = 2500.0 / (M_PER_DEG_LAT * math.cos(math.radians(clat)))
    t0 = time.perf_counter()
    units = build_units(geoms, bbox=(clat - dlat, clat + dlat, clng - dlng, clng + dlng))
    elapsed = time.perf_counter() - t0
    assert len(units) > 100
    assert np.all(units.area_m2 > 0)
    assert elapsed < 30.0
