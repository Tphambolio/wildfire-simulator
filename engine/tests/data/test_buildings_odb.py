"""Municipal footprints (StatCan ODB v3) in the Alberta building file, and the Edmonton box
decision of 2026-10-12 (docs/buildings-alberta.md §5-6).

* ``scripts/build_ab_buildings_odb.py`` on a synthetic town: ODB footprints replace Microsoft
  ones inside the provider's census subdivision only, nothing is counted twice (one zone per
  centroid; seam duplicates dropped), ids follow the documented ranges, and only geometry,
  ``id``, ``area_m2`` and ``source`` are written (no address or other ODB field).
* ``BuildingIndex(within_outside_box=True)`` assigns buildings outside the partition box only
  by a neighbourhood polygon; inside the box the selection is unchanged.
* ``near()`` reports the selected buildings per ``source``.
* With the real files present (not in CI): every Edmonton-extract id is in the new file with
  the same geometry, and no Microsoft and ODB footprint overlap across a seam.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pyogrio
import pyogrio.raw
import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import Polygon, box

from firesim.data.building_index import (
    EDMONTON_EXTRACT_BOX,
    BuildingIndex,
    write_buildings_fgb,
)

REPO = Path(__file__).resolve().parents[3]


def _load_build():
    spec = importlib.util.spec_from_file_location(
        "build_ab_buildings_odb", REPO / "scripts" / "build_ab_buildings_odb.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sq(lng, lat, half_m):
    dlat = half_m / 111320.0
    dlng = half_m / (111320.0 * math.cos(math.radians(lat)))
    return Polygon([(lng - dlng, lat - dlat), (lng + dlng, lat - dlat),
                    (lng + dlng, lat + dlat), (lng - dlng, lat + dlat)])


# Synthetic "Canmore" census subdivision
CSD = box(-115.40, 51.05, -115.30, 51.12)
EDGE_LNG = -115.30   # east edge of the CSD: the seam


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("odb")
    mod = _load_build()
    canmore = mod.PROVIDERS["Town of Canmore"]
    mod.PROVIDERS = {"Town of Canmore": canmore}
    # Microsoft: 30 houses inside the CSD, 20 outside (east), a house straddling the seam
    # (centroid 2 m east of the edge), and 5 Edmonton-extract houses (ids 1-5, in the box)
    ms_geoms, ms_ids = [], []
    for k in range(30):
        ms_geoms.append(_sq(-115.38 + 0.002 * k, 51.08, 6))
        ms_ids.append(1_000_000 + k)
    for k in range(20):
        ms_geoms.append(_sq(-115.29 + 0.002 * k, 51.08, 6))
        ms_ids.append(1_000_100 + k)
    seam_lng = EDGE_LNG + 2 / (111320.0 * math.cos(math.radians(51.10)))
    ms_geoms.append(_sq(seam_lng, 51.10, 6))
    ms_ids.append(1_000_200)
    for k in range(5):
        ms_geoms.append(_sq(-113.5 + 0.001 * k, 53.5, 6))
        ms_ids.append(k + 1)
    ms = d / "ms.fgb"
    write_buildings_fgb(str(ms), ms_geoms, ms_ids)
    # ODB (EPSG:3347): 40 Canmore footprints inside (more, incl. 10 sheds of 3 m), 5 of the
    # same provider outside the CSD, the seam building seen by the town (centroid 2 m west of
    # the edge), and 3 Government of Canada footprints inside (other source: never used)
    to3347 = Transformer.from_crs(4326, 3347, always_xy=True)
    odb_geoms, src = [], []
    for k in range(30):
        odb_geoms.append(_sq(-115.38 + 0.002 * k, 51.08, 6.5))
        src.append("Town of Canmore")
    for k in range(10):
        odb_geoms.append(_sq(-115.38 + 0.002 * k, 51.0805, 1.5))
        src.append("Town of Canmore")
    for k in range(5):
        odb_geoms.append(_sq(-115.25 + 0.002 * k, 51.09, 6))
        src.append("Town of Canmore")
    odb_geoms.append(_sq(EDGE_LNG - 2 / (111320.0 * math.cos(math.radians(51.10))), 51.10, 6))
    src.append("Town of Canmore")
    for k in range(3):
        odb_geoms.append(_sq(-115.36 + 0.002 * k, 51.06, 6))
        src.append("Government of Canada")
    g3347 = [shapely.MultiPolygon([shapely.transform(g, lambda xy: np.column_stack(
        to3347.transform(xy[:, 0], xy[:, 1])))]) for g in odb_geoms]
    gpkg = d / "odb.gpkg"
    pyogrio.raw.write(str(gpkg), np.asarray(shapely.to_wkb(g3347), dtype=object),
                      [np.asarray(src, dtype=object),
                       np.asarray(["1 Secret St"] * len(src), dtype=object)],
                      ["source", "address"], driver="GPKG", geometry_type="MultiPolygon",
                      crs="EPSG:3347", layer="ODB_v3_AB")
    csd = d / "csd.geojson"
    csd.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"CSDUID": "4815023", "CSDNAME": "Canmore",
                                           "CSDTYPE": "T", "PRUID": "48"},
         "geometry": CSD.__geo_interface__}]}))
    out = d / "out.fgb"
    assert mod.main(["--ms-fgb", str(ms), "--odb", str(gpkg), "--csd", str(csd),
                     "--out", str(out)]) == 0
    meta = json.loads(Path(str(out) + ".json").read_text())
    _, _, wkb, fields = pyogrio.raw.read(str(out), columns=["id", "area_m2", "source"])
    return {"out": str(out), "meta": meta, "geoms": shapely.from_wkb(wkb),
            "ids": np.asarray(fields[0]), "area": np.asarray(fields[1]),
            "source": np.asarray(fields[2], dtype=object), "ms_ids": ms_ids}


def test_only_id_area_source_are_written(built):
    assert list(pyogrio.read_info(built["out"])["fields"]) == ["id", "area_m2", "source"]


def test_odb_replaces_microsoft_inside_the_csd_only(built):
    ids, src = built["ids"], built["source"]
    odb = src == "odb:canmore"
    # 30 + 10 sheds inside; the 5 outside the CSD and the Government of Canada ones are not used
    assert odb.sum() == 40
    assert set(src) == {"ms2019", "odb:canmore"}
    ms_kept = set(ids[~odb].tolist())
    assert not any(1_000_000 <= i < 1_000_030 for i in ms_kept)          # replaced
    assert all(1_000_100 + k in ms_kept for k in range(20))             # outside: kept
    assert all(k + 1 in ms_kept for k in range(5))                      # Edmonton ids kept
    assert (ids[odb] > 10_000_000).all() and (ids[odb] <= 10_000_046).all()
    assert len(set(ids.tolist())) == len(ids)
    m = built["meta"]["per_municipality"]["Town of Canmore"]
    assert m["odb_kept"] == 40 and m["ms_replaced"] == 30 and m["odb_lt_20m2"] == 10


def test_no_double_counting_at_the_seam(built):
    g, src, ids = built["geoms"], built["source"], built["ids"]
    # the seam building stays once: the Microsoft footprint (east of the edge) is kept, the
    # town's (west of the edge) is dropped as its duplicate
    assert 1_000_200 in ids.tolist()
    assert built["meta"]["per_municipality"]["Town of Canmore"]["odb_seam_duplicates_dropped"] == 1
    odb, ms = g[src != "ms2019"], g[src == "ms2019"]
    tree = shapely.STRtree(ms)
    pi, ti = tree.query(odb, predicate="intersects")
    assert len(pi) == 0
    # every centroid in exactly one source zone
    mod = _load_build()
    lat, lng = mod.ring_centroids(g)
    inside = shapely.contains_xy(CSD, lng, lat)
    assert (src[inside] == "odb:canmore").all() and (src[~inside] == "ms2019").all()


def test_areas_geodesic(built):
    a = built["area"][built["source"] == "odb:canmore"]
    assert np.isclose(np.sort(a)[-1], 13.0 ** 2, rtol=0.02)   # 6.5 m half-side
    assert np.isclose(np.sort(a)[0], 3.0 ** 2, rtol=0.05)


# ── Edmonton box decision: assignment outside the box by polygon only ──────────────────────

@pytest.fixture(scope="module")
def edge_town(tmp_path_factory):
    d = tmp_path_factory.mktemp("edge")
    lat_edge = EDMONTON_EXTRACT_BOX[0]
    geoms, ids = [], []
    for k in range(40):            # rows from 0.004 deg south to 0.004 deg north of the edge
        lat = lat_edge - 0.0039 + 0.0002 * k
        geoms.append(_sq(-113.50, lat, 5))
        ids.append(k + 1)
    for k in range(5):             # south, in no neighbourhood polygon (like Nisku)
        geoms.append(_sq(-113.40, lat_edge - 0.003 + 0.0005 * k, 5))
        ids.append(100 + k)
    fgb = d / "b.fgb"
    write_buildings_fgb(str(fgb), geoms, ids)
    nb = d / "n.geojson"
    nb.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"neighbourhood": "NORTH"},
         "geometry": box(-113.52, lat_edge, -113.48, lat_edge + 0.01).__geo_interface__},
        {"type": "Feature", "properties": {"neighbourhood": "SOUTH"},
         "geometry": box(-113.52, lat_edge - 0.01, -113.48, lat_edge).__geo_interface__}]}))
    return {"fgb": str(fgb), "nbhd": str(nb), "lat": lat_edge}


def test_within_outside_box_assigns_by_polygon_only(edge_town):
    legacy = BuildingIndex(edge_town["fgb"], edge_town["nbhd"], partition_box=EDMONTON_EXTRACT_BOX)
    new = BuildingIndex(edge_town["fgb"], edge_town["nbhd"], partition_box=EDMONTON_EXTRACT_BOX,
                        within_outside_box=True)
    keys = ["NORTH", "SOUTH"]
    old_ids = legacy.building_ids_for(keys)
    new_ids = new.building_ids_for(keys)
    lat = edge_town["lat"]
    south_rows = [k + 1 for k in range(40) if lat - 0.0039 + 0.0002 * k < lat]
    assert set(old_ids).isdisjoint(south_rows)
    assert set(new_ids) == set(old_ids) | set(south_rows)
    # inside the box: identical buildings, order and centroids
    assert [i for i in new_ids if i not in south_rows] == old_ids
    assert not any(i >= 100 for i in new_ids)     # outside every polygon: never assigned
    assert new.building_ids_for(["NORTH"]) == legacy.building_ids_for(["NORTH"])


def test_near_reports_sources(tmp_path):
    geoms = [_sq(-115.35 + 0.001 * k, 51.09, 5) for k in range(6)]
    p = tmp_path / "s.fgb"
    pyogrio.raw.write(str(p), np.asarray(shapely.to_wkb(geoms), dtype=object),
                      [np.arange(1, 7, dtype=np.int64), np.zeros(6),
                       np.asarray(["ms2019"] * 2 + ["odb:canmore"] * 4, dtype=object)],
                      ["id", "area_m2", "source"], driver="FlatGeobuf", geometry_type="Polygon",
                      crs="EPSG:4326", layer_options={"SPATIAL_INDEX": "YES"})
    near = BuildingIndex(str(p)).near(51.09, -115.35, (51.0, 51.2, -115.4, -115.3))
    assert near.sources == {"ms2019": 2, "odb:canmore": 4}
    capped = BuildingIndex(str(p)).near(51.09, -115.35, (51.0, 51.2, -115.4, -115.3),
                                        max_buildings=1)
    assert sum(capped.sources.values()) == 1
    # a file without the field reports none
    q = tmp_path / "t.fgb"
    write_buildings_fgb(str(q), geoms, range(1, 7))
    assert BuildingIndex(str(q)).near(51.09, -115.35, (51.0, 51.2, -115.4, -115.3)).sources == {}


# ── Real files (not in CI) ─────────────────────────────────────────────────────────────────

NEW = os.environ.get("FIRESIM_AB_BUILDINGS_ODB_PATH",
                     str(Path.home() / "dev/wildfire/release-ab-buildings-odb/ab_buildings_2026_10.fgb"))
OLD = os.environ.get("FIRESIM_AB_BUILDINGS_PATH",
                     str(Path.home() / "dev/wildfire/buildings-ab/ab_buildings_ms2019.fgb"))


@pytest.mark.skipif(not (os.path.exists(NEW) and os.path.exists(OLD)), reason="real files absent")
def test_real_edmonton_box_identical():
    """The footprints with centroid in the Edmonton box are the same 346,238 in both files
    (none replaced by ODB footprints)."""
    a = BuildingIndex(OLD).clipped(EDMONTON_EXTRACT_BOX)
    b = BuildingIndex(NEW).clipped(EDMONTON_EXTRACT_BOX)
    assert a.count_in_box(EDMONTON_EXTRACT_BOX) == b.count_in_box(EDMONTON_EXTRACT_BOX) == 346_238
