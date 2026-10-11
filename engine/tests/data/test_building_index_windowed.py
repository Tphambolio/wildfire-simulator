"""Windowed building index (Alberta FlatGeobuf): equivalence with the former whole-file index,
windowed reads, buildings near an ignition, clipped footprint source, memory bound.

docs/buildings-alberta.md. The real-data checks at the end run only when the Alberta file is
present (``FIRESIM_AB_BUILDINGS_PATH``; not in CI).
"""

from __future__ import annotations

import json
import math
import os
import random
import tracemalloc
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import Point, box, shape

from firesim.data.building_index import (
    EDMONTON_EXTRACT_BOX,
    BuildingIndex,
    _raw_centroid,
    write_buildings_fgb,
)

REPO = Path(__file__).resolve().parents[3]


# ── Reference: the former whole-file index (master before this change), restated ─────────

def _reference_partition(features, nbhd_geoms, nbhd_keys):
    """{key: [(geojson geometry, centroid, id), ...]} as the former BuildingIndex built it."""
    from shapely.strtree import STRtree

    cents = [(g.centroid.y, g.centroid.x) for g in nbhd_geoms]
    tree = STRtree(nbhd_geoms)
    out = {k: [] for k in nbhd_keys}
    for f in features:
        c = _raw_centroid(f["geometry"])
        if c is None:
            continue
        hits = tree.query(Point(c[1], c[0]), predicate="within")
        if len(hits):
            i = int(hits[0])
        else:
            i = min(range(len(cents)), key=lambda j: (math.hypot(c[1] - cents[j][1], c[0] - cents[j][0]), j))
        out[nbhd_keys[i]].append((f["geometry"], c, f["properties"]["id"]))
    return out


def _square(lng, lat, half_m):
    dlat = half_m / 111320.0
    dlng = half_m / (111320.0 * math.cos(math.radians(lat)))
    return [[lng - dlng, lat - dlat], [lng + dlng, lat - dlat], [lng + dlng, lat + dlat],
            [lng - dlng, lat + dlat], [lng - dlng, lat - dlat]]


@pytest.fixture(scope="module")
def town(tmp_path_factory):
    """A synthetic town: 3,000 houses in a 0.08 x 0.12 deg area, 9 neighbourhood polygons
    covering part of it (the rest falls back to the nearest neighbourhood centroid), as a
    GeoJSON and as a FlatGeobuf."""
    d = tmp_path_factory.mktemp("town")
    rng = random.Random(7)
    lat0, lng0 = 53.50, -113.50
    feats = []
    for i in range(3000):
        lat = round(lat0 + rng.uniform(-0.04, 0.04), 6)
        lng = round(lng0 + rng.uniform(-0.06, 0.06), 6)
        ring = [[round(x, 6), round(y, 6)] for x, y in _square(lng, lat, rng.uniform(4, 15))]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"id": i + 1}})
    gj = d / "b.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    fgb = d / "b.fgb"
    write_buildings_fgb(str(fgb), [shape(f["geometry"]) for f in feats], range(1, 3001))
    nb = []
    for r in range(3):
        for c in range(3):
            la, lo = lat0 - 0.03 + 0.02 * r, lng0 - 0.045 + 0.03 * c
            nb.append({"type": "Feature", "properties": {"neighbourhood": f"N{r}{c}"},
                       "geometry": shape(box(lo, la, lo + 0.022, la + 0.016)).__geo_interface__})
    nbp = d / "n.geojson"
    nbp.write_text(json.dumps({"type": "FeatureCollection", "features": nb}))
    return {"features": feats, "geojson": str(gj), "fgb": str(fgb), "nbhd": str(nbp),
            "nb": nb, "lat": lat0, "lng": lng0}


def test_fgb_neighbourhood_selection_equals_former_index(town):
    """Same buildings, centroids and order for every neighbourhood set, GeoJSON and FlatGeobuf."""
    keys = [f["properties"]["neighbourhood"] for f in town["nb"]]
    ref = _reference_partition(town["features"], [shape(f["geometry"]) for f in town["nb"]], keys)
    gj = BuildingIndex(town["geojson"], town["nbhd"])
    fg = BuildingIndex(town["fgb"], town["nbhd"], partition_box=(53.4, 53.6, -113.6, -113.4))
    for lat, lng in [(53.5, -113.5), (53.47, -113.55), (53.53, -113.44), (53.46, -113.45)]:
        nearest = gj.nearest_neighbourhoods(lat, lng, 4)
        assert fg.nearest_neighbourhoods(lat, lng, 4) == nearest
        want = [x for k in nearest for x in ref[k]]
        for idx in (gj, fg):
            assert idx.building_centroids_for(nearest) == [c for _, c, _ in want]
            assert idx.building_ids_for(nearest) == [i for _, _, i in want]
            got = idx.building_geoms_for(nearest)
            assert len(got) == len(want)
            assert all(g.equals_exact(shape(w), 0) for g, (w, _, _) in zip(got, want))


def test_fgb_footprint_source_equals_list_source(town):
    """count_in_box / count_intersecting / ids_intersecting / footprints: FlatGeobuf windowed
    reads give exactly the whole-file answers."""
    fg = BuildingIndex(town["fgb"])
    gj = BuildingIndex(town["geojson"])
    geoms = [shape(f["geometry"]) for f in town["features"]]
    b = np.array([g.bounds for g in geoms])
    cen = np.array([_raw_centroid(f["geometry"]) for f in town["features"]])
    for bbox in [(53.49, 53.51, -113.52, -113.48), (53.46, 53.5, -113.56, -113.5),
                 (53.0, 54.0, -114.0, -113.0)]:
        lat_min, lat_max, lng_min, lng_max = bbox
        hit = (b[:, 0] <= lng_max) & (b[:, 2] >= lng_min) & (b[:, 1] <= lat_max) & (b[:, 3] >= lat_min)
        inside = ((cen[:, 0] >= lat_min) & (cen[:, 0] <= lat_max)
                  & (cen[:, 1] >= lng_min) & (cen[:, 1] <= lng_max))
        for idx in (fg, gj):
            assert idx.count_intersecting(bbox) == int(hit.sum())
            assert idx.count_in_box(bbox) == int(inside.sum())
            ids = idx.ids_intersecting(bbox)
            assert sorted(ids.tolist()) == sorted((np.nonzero(hit)[0] + 1).tolist())
            fps = idx.footprints_intersecting(bbox)
            assert len(fps) == len(ids)
            assert all(fp.equals_exact(geoms[i - 1], 0) for fp, i in zip(fps, ids))


def test_clipped_source_keeps_only_buildings_with_centroid_in_box(town):
    fg = BuildingIndex(town["fgb"])
    clip = (53.48, 53.52, -113.53, -113.47)
    src = fg.clipped(clip)
    cen = np.array([_raw_centroid(f["geometry"]) for f in town["features"]])
    keep = ((cen[:, 0] >= clip[0]) & (cen[:, 0] <= clip[1]) & (cen[:, 1] >= clip[2])
            & (cen[:, 1] <= clip[3]))
    bbox = (53.47, 53.51, -113.55, -113.49)
    geoms = [shape(f["geometry"]) for f in town["features"]]
    b = np.array([g.bounds for g in geoms])
    hit = (b[:, 0] <= bbox[3]) & (b[:, 2] >= bbox[2]) & (b[:, 1] <= bbox[1]) & (b[:, 3] >= bbox[0])
    assert src.count_intersecting(bbox) == int((hit & keep).sum())
    assert sorted(src.ids_intersecting(bbox).tolist()) == sorted((np.nonzero(hit & keep)[0] + 1).tolist())
    assert len(src.footprints_intersecting(bbox)) == int((hit & keep).sum())
    inside = ((cen[:, 0] >= max(bbox[0], clip[0])) & (cen[:, 0] <= min(bbox[1], clip[1]))
              & (cen[:, 1] >= max(bbox[2], clip[2])) & (cen[:, 1] <= min(bbox[3], clip[3])))
    assert src.count_in_box(bbox) == int(inside.sum())


def test_near_selects_buildings_in_box_nearest_first(town):
    fg = BuildingIndex(town["fgb"])
    bbox = (53.48, 53.52, -113.53, -113.47)
    allin = fg.near(53.5, -113.5, bbox, max_buildings=10_000)
    cen = np.array([_raw_centroid(f["geometry"]) for f in town["features"]])
    inside = ((cen[:, 0] >= bbox[0]) & (cen[:, 0] <= bbox[1]) & (cen[:, 1] >= bbox[2])
              & (cen[:, 1] <= bbox[3]))
    assert not allin.capped and allin.in_box == int(inside.sum()) == len(allin.geoms)
    assert sorted(allin.ids.tolist()) == (np.nonzero(inside)[0] + 1).tolist()
    few = fg.near(53.5, -113.5, bbox, max_buildings=50)
    assert few.capped and len(few.geoms) == 50 and few.in_box == allin.in_box
    # the 50 kept are nearer the ignition than every building left out
    k = math.cos(math.radians(53.5))
    d = {i: ((c[1] + 113.5) * k) ** 2 + (c[0] - 53.5) ** 2 for i, c in zip(allin.ids, allin.centroids)}
    kept = set(few.ids.tolist())
    assert max(d[i] for i in kept) <= min(d[i] for i in d if i not in kept) * 1.01 + 1e-12
    assert fg.near(10.0, 10.0, (9.9, 10.1, 9.9, 10.1)).geoms == []


def test_fgb_rasterized_mask_equals_in_memory_mask(town):
    from firesim.data.environment import load_environment_mask

    bounds = (53.47, 53.53, -113.55, -113.45)
    geoms = [shape(f["geometry"]) for f in town["features"]]
    a = load_environment_mask(bounds, 60, 80, building_geoms=geoms)
    b = load_environment_mask(bounds, 60, 80, buildings_path=town["fgb"])
    assert a.sum() > 0 and np.array_equal(a, b)


def test_windowed_read_memory_is_bounded(tmp_path):
    """A small-box read from a 120,000-footprint file allocates a small fraction of what the
    whole file would; opening the index allocates almost nothing (no per-building state)."""
    rng = np.random.default_rng(3)
    n = 120_000
    lat = 53.0 + rng.uniform(0, 1.0, n)
    lng = -114.0 + rng.uniform(0, 1.5, n)
    d = 5e-5
    geoms = shapely.box(lng - d, lat - d, lng + d, lat + d)
    path = tmp_path / "big.fgb"
    write_buildings_fgb(str(path), geoms, np.arange(1, n + 1))
    del geoms
    tracemalloc.start()
    idx = BuildingIndex(str(path))
    opened = tracemalloc.get_traced_memory()[1]
    tracemalloc.reset_peak()
    fps = idx.footprints_intersecting((53.5, 53.52, -113.3, -113.27))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert 0 < len(fps) < 500
    assert opened < 1_000_000          # < 1 MB to open (the former index: whole file in memory)
    assert peak < 5_000_000            # a few MB for a small box, not the 120k footprints
    assert idx.count_in_box((52.9, 54.1, -114.1, -112.4)) == n


# ── Real data (skipped without the Alberta file) ─────────────────────────────────────────

AB = os.environ.get("FIRESIM_AB_BUILDINGS_PATH", "")
EDM = REPO / "data" / "edmonton_buildings.geojson.gz"
NBHD = REPO / "data" / "edmonton_neighbourhoods.geojson"
real = pytest.mark.skipif(not (AB and os.path.exists(AB) and EDM.exists()),
                          reason="Alberta buildings file not installed (FIRESIM_AB_BUILDINGS_PATH)")


@real
def test_alberta_file_reproduces_edmonton_extract_selection():
    """Edmonton: the 4-nearest-neighbourhood buildings from the Alberta file equal those of the
    Edmonton extract, ignition by ignition (same footprints, centroids, ids, order)."""
    fg = BuildingIndex(AB, str(NBHD), partition_box=EDMONTON_EXTRACT_BOX)
    gj = BuildingIndex(str(EDM), str(NBHD))
    for lat, lng in [(53.5175, -113.4755), (53.45, -113.60), (53.55, -113.26)]:
        nearest = gj.nearest_neighbourhoods(lat, lng, 4)
        assert fg.building_ids_for(nearest) == gj.building_ids_for(nearest)
        assert fg.building_centroids_for(nearest) == gj.building_centroids_for(nearest)
    bbox = (53.336, 53.718, -113.716, -113.270)  # the City fuel grid's box
    assert fg.clipped(EDMONTON_EXTRACT_BOX).count_in_box(bbox) == gj.count_in_box(bbox)


@real
def test_alberta_file_has_buildings_outside_edmonton():
    fg = BuildingIndex(AB)
    near = fg.near(51.087, -115.348, (51.0, 51.2, -115.5, -115.2))  # Canmore
    assert len(near.geoms) > 1000 and (near.ids >= 1_000_000).all()
