"""Alberta-wide critical assets, major roads and terrain (docs/terrain-alberta.md,
docs/data-sources.md "Alberta assets and roads").

* data mapping of the build scripts (OSM tags -> ICS categories, ODHF merge, no personal data);
* FlatGeobuf box reads (``firesim.data.vector_layers``): only the box, schema of the Edmonton
  layers, truncation flag;
* terrain windows (``firesim.data.terrain``): slope and aspect of a known plane, windowed read,
  no data outside the file.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from firesim.data.dem_loader import slope_aspect
from firesim.data.provincial import Lattice
from firesim.data.terrain import elevation_on_lattice, terrain_covers, terrain_on_lattice
from firesim.data.vector_layers import read_assets, read_roads

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_ab_assets as A  # noqa: E402
import build_ab_roads as R  # noqa: E402


# ── Data mapping (scripts/build_ab_assets.py) ────────────────────────────────


@pytest.mark.parametrize("tags, expected", [
    ({"amenity": "hospital", "name": "Hinton Healthcare Centre"}, "hospital"),
    ({"healthcare": "hospice", "name": "X"}, "hospital"),
    ({"social_facility": "assisted_living", "amenity": "social_facility", "name": "X"}, "hospital"),
    ({"amenity": "fire_station", "name": "Canmore Fire Hall"}, "fire_station"),
    ({"amenity": "police", "name": "Slave Lake RCMP"}, "police"),
    ({"emergency": "ambulance_station", "name": "Hinton EMS"}, "ems"),
    ({"amenity": "school", "name": "Harry Collinge High School"}, "school"),
    ({"amenity": "community_centre", "name": "Harvie Heights Community Hall"}, "reception"),
    ({"leisure": "sports_centre", "name": "Canmore Recreation Centre"}, "reception"),
    ({"amenity": "community_centre", "name": "Hinton Seniors Centre"}, "seniors"),
    ({"amenity": "social_facility", "social_facility": "outreach",
      "social_facility:for": "senior", "name": "Drop-in"}, "seniors"),
    ({"railway": "station", "station": "light_rail", "name": "Brentwood"}, "transit"),
])
def test_osm_tags_map_to_ics_categories(tags, expected):
    assert A.classify(tags)[0] == expected


@pytest.mark.parametrize("tags", [
    {"leisure": "sports_centre", "name": "Anytime Fitness"},  # private gym, not recreation
    {"leisure": "sports_centre", "name": "Town Arena", "access": "private"},
    {"amenity": "social_facility", "social_facility": "group_home", "name": "Seniors home"},
    {"amenity": "social_centre", "name": "Legion"},
])
def test_non_assets_are_left_out(tags):
    assert A.classify(tags) is None


def _el(i, tags, lat=53.4, lng=-117.56, kind="node"):
    if kind == "node":
        return {"type": "node", "id": i, "lat": lat, "lon": lng, "tags": tags}
    ring = [{"lat": lat, "lon": lng}, {"lat": lat + 0.001, "lon": lng},
            {"lat": lat + 0.001, "lon": lng + 0.001}, {"lat": lat, "lon": lng}]
    return {"type": "way", "id": i, "geometry": ring, "tags": tags}


def test_osm_infrastructure_rules():
    data = {"elements": [
        _el(1, {"man_made": "wastewater_plant"}, kind="way"),  # unnamed lagoon: kept, by kind
        _el(2, {"man_made": "wastewater_plant", "name": "Storm pond"}, kind="way"),
        _el(3, {"power": "substation", "voltage": "25000"}),  # distribution: left out
        _el(4, {"power": "substation", "voltage": "240000;138000", "operator": "AltaLink"}),
        _el(5, {"power": "plant", "plant:source": "solar", "plant:output:electricity": "1 MW"}),
        _el(6, {"power": "plant", "plant:source": "gas", "plant:output:electricity": "90 MW"}),
        _el(7, {"amenity": "school"}),  # unnamed school: left out
        _el(8, {"amenity": "school", "name": "Crestwood School", "addr:street": "Main St",
                "addr:housenumber": "1", "phone": "+1 780 000 0000"}),
    ]}
    out = A.osm_assets(data)
    names = {o["name"]: o for o in out}
    assert set(names) == {"Wastewater treatment plant (unnamed)", "Substation (AltaLink)",
                          "Power plant (gas)", "Crestwood School"}
    assert names["Wastewater treatment plant (unnamed)"]["outline"]["type"] == "Polygon"
    assert names["Substation (AltaLink)"]["detail"].startswith("Substation, 240 kV")
    # No personal data: addresses and phone numbers never reach the record
    assert "Main" not in json.dumps(out) and "780" not in json.dumps(out)


class _Csd:
    def __init__(self, name):
        self.name = name

    def __call__(self, xy):
        return self.name


def test_odhf_merge_and_flags():
    odhf = [
        {"name": "Hinton Healthcare Centre", "xy": (-117.585, 53.398), "csd": "hinton",
         "kind": "Hospital", "source_id": "odhf#1"},
        {"name": "Hinton Continuing Care", "xy": None, "csd": "hinton",
         "kind": "Nursing and residential care", "source_id": "odhf#2"},
        {"name": "Nowhere Lodge", "xy": None, "csd": "hinton", "kind": "Nursing", "source_id": "odhf#3"},
        {"name": "Old Clinic Hospital", "xy": (-117.0, 53.0), "csd": "x", "kind": "Hospital",
         "source_id": "odhf#4"},
    ]
    osm = [
        {"name": "Hinton Healthcare Centre", "category": "hospital", "detail": "Hospital",
         "xy": (-117.5852, 53.3982), "outline": None, "sources": [{"source": A.SRC_OSM, "source_id": "osm:node/1"}]},
        {"name": "Hinton Continuing Care Centre", "category": "hospital", "detail": "Nursing home",
         "xy": (-117.57, 53.40), "outline": None, "sources": [{"source": A.SRC_OSM, "source_id": "osm:node/2"}]},
        {"name": "Mountain View Assisted Living", "category": "hospital", "detail": "Assisted living",
         "xy": (-117.56, 53.41), "outline": None, "sources": [{"source": A.SRC_OSM, "source_id": "osm:node/3"}]},
    ]
    log: list[str] = []
    care, stats = A.merge_care(odhf, osm, _Csd("hinton"), log)
    by = {c["name"]: c for c in care}
    assert stats["odhf_placed_by_osm"] == 1 and stats["odhf_left_out_no_xy"] == 1
    assert [s["source"] for s in by["Hinton Healthcare Centre"]["sources"]] == [A.SRC_ODHF, A.SRC_OSM]
    assert "verify" not in by["Hinton Healthcare Centre"]
    assert by["Hinton Continuing Care"]["xy"] == (-117.57, 53.40)  # placed by the OSM record
    assert by["Old Clinic Hospital"]["verify"] == "ODHF (2020) only, verify"
    assert by["Mountain View Assisted Living"]["verify"] == "OpenStreetMap only, verify"
    assert "Nowhere Lodge" not in by and any("Nowhere Lodge" in x for x in log)


# ── FlatGeobuf box reads ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def layer_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("layers")
    feats = []
    for i in range(30):  # a line of schools 1 km apart, west to east
        feats.append({"name": f"School {i}", "category": "school", "detail": "School",
                      "xy": (-117.6 + i * 0.015, 53.40), "outline": None,
                      "sources": [{"source": A.SRC_OSM, "source_id": f"osm:node/{i}"}]})
    ring = [[-117.40, 53.30], [-117.39, 53.30], [-117.39, 53.31], [-117.40, 53.30]]
    feats.append({"name": "Lagoon", "category": "water", "detail": "Wastewater treatment",
                  "xy": (-117.395, 53.305), "outline": {"type": "Polygon", "coordinates": [ring]},
                  "sources": [{"source": A.SRC_OSM, "source_id": "osm:way/9"}],
                  "verify": None})
    assets = d / "assets.fgb"
    A.write_fgb(feats, assets)

    import pyogrio
    import shapely

    lines = [shapely.linestrings([[-117.6 + i * 0.02, 53.39], [-117.6 + i * 0.02 + 0.019, 53.39]])
             for i in range(20)]
    roads = d / "roads.fgb"
    pyogrio.raw.write(str(roads), geometry=shapely.to_wkb(np.array(lines, dtype=object)),
                      field_data=[np.array(["Highway 16"] * 20, dtype=object),
                                  np.array(["trunk"] * 20, dtype=object),
                                  np.array(["16"] * 20, dtype=object),
                                  np.zeros(20, dtype=np.int32)],
                      fields=["name", "highway", "ref", "link"], driver="FlatGeobuf",
                      geometry_type="LineString", crs="EPSG:4326",
                      layer_options={"SPATIAL_INDEX": "YES"})
    return {"assets": str(assets), "roads": str(roads)}


def test_assets_box_read_returns_only_the_box(layer_files):
    feats, truncated = read_assets(layer_files["assets"], (-117.61, 53.39, -117.52, 53.41))
    names = sorted(f["properties"]["name"] for f in feats)
    assert names == [f"School {i}" for i in range(6)] and not truncated
    p = feats[0]["properties"]
    assert set(p) >= {"name", "category", "source", "source_id", "licence", "sources"}
    assert p["sources"][0]["source"] == "OpenStreetMap" and "ODbL" in p["licence"]
    assert feats[0]["geometry"]["type"] == "Point"


def test_asset_outline_and_anchor(layer_files):
    feats, _ = read_assets(layer_files["assets"], (-117.41, 53.29, -117.38, 53.32))
    lagoon = next(f for f in feats if f["properties"]["name"] == "Lagoon")
    assert lagoon["geometry"]["type"] == "Polygon"
    assert lagoon["properties"]["anchor"] == [-117.395, 53.305]


def test_assets_limit_sets_truncated(layer_files):
    feats, truncated = read_assets(layer_files["assets"], (-118, 53, -117, 54), limit=5)
    assert len(feats) == 5 and truncated


def test_roads_box_read(layer_files):
    feats, truncated = read_roads(layer_files["roads"], (-117.61, 53.38, -117.55, 53.40))
    assert 3 <= len(feats) <= 4 and not truncated
    p = feats[0]["properties"]
    assert p == {"highway": "trunk", "ref": "16", "name": "Highway 16"}
    assert feats[0]["geometry"]["type"] == "LineString"


def test_road_pieces_are_short():
    line = [[-117.0 + i * 0.01, 53.4] for i in range(200)]  # ~132 km
    parts = R.pieces(line, max_m=5000)
    for p in parts:
        kx = 111_320 * math.cos(math.radians(53.4))
        length = sum(math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * 111_320) for a, b in zip(p, p[1:]))
        assert length <= 5000
    assert parts[0][0] == line[0] and parts[-1][-1] == line[-1]
    # consecutive pieces share their joint (no gap)
    assert all(a[-1] == b[0] for a, b in zip(parts, parts[1:]))


# ── Terrain windows ──────────────────────────────────────────────────────────

DLAT, DLNG = 0.0009, 0.0015


@pytest.fixture(scope="module")
def plane_dem(tmp_path_factory):
    """Elevation rising 10 % to the east (uint16 decimetres, SCALE tag), 0.6 x 1.0 degrees."""
    d = tmp_path_factory.mktemp("terrain")
    lat_max, lng_min = 53.7, -118.0
    rows, cols = int(0.6 / DLAT), int(1.0 / DLNG)
    lat_c = lat_max - rows * DLAT / 2
    x_m = (np.arange(cols) + 0.5) * DLNG * 111_320 * math.cos(math.radians(lat_c))
    elev = 1000.0 + 0.10 * x_m
    dm = np.tile(np.rint(elev * 10).astype(np.uint16), (rows, 1))
    path = d / "ab_terrain.tif"
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1,
                       dtype="uint16", nodata=0, crs="EPSG:4326", tiled=True,
                       blockxsize=256, blockysize=256,
                       transform=from_origin(lng_min, lat_max, DLNG, DLAT)) as dst:
        dst.write(dm, 1)
        dst.update_tags(SCALE="0.1")
    return str(path)


def test_plane_slope_and_aspect(plane_dem):
    lat = Lattice.around(53.4, -117.5, 10.0, 50.0)
    t = terrain_on_lattice(plane_dem, lat)
    s = np.array([list(r) for r in t.slope])
    a = np.array([list(r) for r in t.aspect])
    assert (t.rows, t.cols) == (lat.rows, lat.cols)
    # 10 % up to the east; the cos(latitude) of the lattice vs the file differs by < 1 %
    assert np.median(s) == pytest.approx(10.0, abs=0.3)
    assert np.median(a) == pytest.approx(90.0, abs=1.0)  # upslope faces east (true north ref)
    assert t.get_slope_aspect(53.4, -117.5)[0] == pytest.approx(10.0, abs=0.5)


def test_window_read_is_small(plane_dem):
    lat = Lattice.around(53.4, -117.5, 10.0, 50.0)
    e = elevation_on_lattice(plane_dem, lat, margin=1)
    assert e.shape == (lat.rows + 2, lat.cols + 2) and np.isfinite(e).all()
    assert e.nbytes < 1_000_000  # 10 km at 50 m: 202 x 202 doubles


def test_outside_the_file(plane_dem):
    assert terrain_on_lattice(plane_dem, Lattice.around(51.1, -115.4, 10.0, 50.0)) is None
    assert not terrain_covers(plane_dem, 51.1, -115.4)
    assert terrain_covers(plane_dem, 53.4, -117.5)


def test_slope_aspect_matches_dem_loader_convention():
    # 5 % rising to the north: aspect 0
    e = np.tile((np.arange(10)[::-1] * 5.0)[:, None], (1, 10))  # row 0 north, 5 m per 100 m
    s, a = slope_aspect(e, 100.0, 100.0)
    assert np.allclose(s, 5.0) and np.allclose(a, 0.0)


def test_odhf_reader_fixes_sign_and_keeps_no_address():
    import io
    import zipfile

    cols = ["index", "facility_name", "source_facility_type", "odhf_facility_type", "provider",
            "unit", "street_no", "street_name", "postal_code", "city", "province",
            "source_format_str_address", "CSDname", "CSDuid", "Pruid", "latitude", "longitude"]
    rows = [
        # sign error in the source (positive longitude)
        ["1", "PROVOST HEALTH CENTRE", "", "Hospitals", "", "", "5402", "47 St", "T0B3S0",
         "provost", "ab", "", "Provost", "", "48", "52.357969", "110.26484"],
        ["2", "Some Day Surgery Clinic", "", "Hospitals", "", "", "", "", "", "", "ab", "", "",
         "", "48", "53.5", "-113.5"],  # outpatient clinic: left out
        ["3", "Covenant Health Mineral Springs Hospital", "", "Hospitals", "", "", "", "", "",
         "", "ab", "", "Banff", "", "48", "", ""],
        ["4", "Elsewhere Hospital", "", "Hospitals", "", "", "", "", "", "", "bc", "", "", "",
         "59", "49.0", "-123.0"],
    ]
    text = "\n".join(",".join(r) for r in [cols, *rows]) + "\n"
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("odhf_v1.csv", text)
    out = A.odhf_alberta(z.getvalue())
    by = {o["name"]: o for o in out}
    assert set(by) == {"Provost Health Centre", "Mineral Springs Hospital"}
    assert by["Provost Health Centre"]["xy"] == (-110.26484, 52.357969)
    assert by["Mineral Springs Hospital"]["xy"] is None
    assert by["Mineral Springs Hospital"]["csd"] == "banff"
    assert "47 St" not in json.dumps(out) and "T0B3S0" not in json.dumps(out)
