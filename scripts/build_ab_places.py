#!/usr/bin/env python3
"""Build the named places finer than a census subdivision (hamlets and localities) in Alberta.

    python scripts/build_ab_places.py --out frontend/public/alberta/places.geojson \
        [--src-dir DIR]   # cached downloads; fetched when missing

Arrival and exposure outputs outside Edmonton name a county or municipal district (a census
subdivision, ``census_subdivisions.geojson``). This file adds the places inside them, so a
fire north of St. Albert reads "Villeneuve (hamlet, Sturgeon County)" rather than "Sturgeon
County". Three sources, in order of authority (see ``docs/data-sources.md``):

1. **Hamlet boundaries** (434 polygons) and **urban service areas** (Sherwood Park, Fort
   McMurray, Lac La Biche, Bremner), Government of Alberta, Municipal Affairs (owner) /
   Provincial Geospatial Centre (custodian), ``geospatial.alberta.ca`` service
   ``boundaries/municipal_communities_public`` layers 7 and 4. Open Government Licence –
   Alberta. Level ``hamlet``.
2. **Designated places** (DPL), Statistics Canada 2021 Census cartographic boundary file
   (``ldpl000b21a``), Alberta, layer 8 of the StatCan geography REST service. Statistics Canada
   Open Licence. Only DPLs that a hamlet / urban service area does not already cover (country
   residential subdivisions, Métis settlements, park townsites, former villages). Level
   ``locality``.
3. **Populated places**, Canadian Geographical Names Database (CGNDB), Natural Resources
   Canada, Alberta CSV (``cgn_ab_csv_eng``). Open Government Licence – Canada. Generic terms
   "Locality" (level ``locality``) and "Hamlet" (level ``hamlet``, for names the provincial
   layer has no polygon for, e.g. Lake Louise, Kananaskis). Points, with the radius rule
   below. Points inside a hamlet / urban service area / DPL polygon, inside a city, town,
   village or summer village (the municipality already names them), or in Edmonton (its
   neighbourhoods name them) are dropped, as are points within 1 km of a polygon of the same
   name (the same place twice).

Radius rule (points): a CGNDB place is a disc of ``POINT_RADIUS_M`` = 250 m around its point,
about the 25th percentile of the equivalent-circle radius of Alberta's designated hamlet
polygons (10th / 25th / 50th: 169 / 241 / 357 m, 2025 layer). A locality has fewer buildings
than a hamlet, so the lower quartile, not the median. With the 500 m arrival buffer, fire
within 750 m of the point counts as "within 500 m" of the locality.

Each feature: ``name`` = "Place (kind, Parent CSD)" (unique; the evacuation status is keyed by
it), ``place``, ``level`` (``hamlet`` | ``locality``), ``kind`` (hamlet, urban service area,
locality, Métis settlement), ``parent`` (the CSD's name in ``census_subdivisions.geojson``),
``source``, and ``radius_m`` on points. Polygons simplified to ~10 m (topology preserved),
5-decimal coordinates. No addresses or personal data: names of places only.
"""

from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import re
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

import shapely
from pyproj import Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform

REPO = Path(__file__).resolve().parents[1]
CSD_PATH = REPO / "frontend" / "public" / "alberta" / "census_subdivisions.geojson"

AB = "https://geospatial.alberta.ca/titan/rest/services/boundaries/municipal_communities_public/FeatureServer"
STATCAN = "https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer"
URLS = {
    "ab_hamlet.geojson": f"{AB}/7/query?where=1%3D1&outFields=GEONAME_HMT,GEOCODE_HMT,DATE_REVISED&outSR=4326&f=geojson",
    "ab_usa.geojson": f"{AB}/4/query?where=1%3D1&outFields=USA_NAME,USA_CODE&outSR=4326&f=geojson",
    "dpl.geojson": f"{STATCAN}/8/query?where=PRUID%3D%2748%27&outFields=DPLUID,DPLNAME,DPLTYPE&outSR=4326&f=geojson&returnGeometry=true",
    "cgn_ab.zip": "https://ftp.cartes.canada.ca/pub/nrcan_rncan/vector/geobase_cgn_toponyme/prov_csv_eng/cgn_ab_csv_eng.zip",
}

SRC_HAMLET = "Government of Alberta hamlet boundary (Municipal Affairs)"
SRC_USA = "Government of Alberta urban service area (Municipal Affairs)"
SRC_DPL = "Statistics Canada designated place (2021)"
SRC_CGNDB = "Canadian Geographical Names Database (NRCan)"

POINT_RADIUS_M = 250
SAME_NAME_M = 1000
TOLERANCE_DEG = 0.0001
URBAN_CSD = {"CY", "T", "VL", "SV"}  # city, town, village, summer village

TO_M = Transformer.from_crs(4326, 3400, always_xy=True).transform  # NAD83 / Alberta 10-TM


def fetch(src_dir: Path, name: str) -> Path:
    p = src_dir / name
    if not p.exists():
        req = urllib.request.Request(URLS[name], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r:
            p.write_bytes(r.read())
    return p


def fold(s: str) -> str:
    """Name key: accents, case, punctuation and a "Hamlet of" prefix ignored."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--src-dir", default=str(REPO / "data" / "cache" / "ab_places"))
    ap.add_argument("--csd", default=str(CSD_PATH))
    args = ap.parse_args(argv)
    src = Path(args.src_dir)
    src.mkdir(parents=True, exist_ok=True)
    load = lambda n: json.loads(fetch(src, n).read_text(encoding="utf-8"))  # noqa: E731

    csd = [(f["properties"], transform(TO_M, shape(f["geometry"])))
           for f in json.loads(Path(args.csd).read_text(encoding="utf-8"))["features"]]
    csd_tree = shapely.STRtree([g for _, g in csd])

    def parent_of(g):
        """The CSD holding most of `g` (None: Edmonton or outside Alberta)."""
        best, area = None, 0.0
        for i in csd_tree.query(g):
            p, cg = csd[i]
            a = cg.intersection(g).area if g.area > 0 else (1.0 if cg.contains(g) else 0.0)
            if a > area:
                best, area = p, a
        return best

    units = []  # (place, level, kind, source, geom_m, extra)

    # 1. Provincial hamlets and urban service areas
    hamlets = [(f["properties"]["GEONAME_HMT"].strip(), transform(TO_M, shape(f["geometry"])))
               for f in load("ab_hamlet.geojson")["features"]]
    for n, g in hamlets:
        units.append((n, "hamlet", "hamlet", SRC_HAMLET, g.buffer(0)))
    hamlet_names = {fold(n) for n, _ in hamlets}
    for f in load("ab_usa.geojson")["features"]:
        n = f["properties"]["USA_NAME"].strip()
        g = transform(TO_M, shape(f["geometry"])).buffer(0)
        m = re.match(r"^(.*)\((.*)\)$", n)
        if m:  # "Sherwood Park (Bremner)" -> Bremner; "Lac La Biche (Plamondon)": Plamondon is a hamlet
            n = m.group(2).strip()
            if fold(n) in hamlet_names:
                continue
        units.append((n, "hamlet", "urban service area", SRC_USA, g))
    covered = shapely.union_all([u[4] for u in units])

    # 2. Designated places the provincial layers do not cover
    for f in load("dpl.geojson")["features"]:
        p = f["properties"]
        g = transform(TO_M, shape(f["geometry"])).buffer(0)
        if g.intersection(covered).area >= 0.2 * g.area:
            continue
        n = re.sub(r"\s+part\s+([AB])$", r" (part \1)", p["DPLNAME"].strip())
        kind = "Métis settlement" if p["DPLTYPE"] == "MET" else "locality"
        units.append((n, "locality", kind, SRC_DPL, g))
    polys = [u for u in units]
    poly_union = shapely.union_all([u[4] for u in polys])
    poly_by_name = collections.defaultdict(list)
    for u in polys:
        poly_by_name[fold(u[0])].append(u[4])

    # 3. CGNDB populated places (points)
    with zipfile.ZipFile(fetch(src, "cgn_ab.zip")) as z:
        csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
        rows = list(csv.DictReader(io.TextIOWrapper(z.open(csv_name), encoding="utf-8-sig")))
    dropped = collections.Counter()
    for r in rows:
        term = r["Generic Term"]
        if term not in ("Locality", "Hamlet"):
            continue
        pt = Point(TO_M(float(r["Longitude"]), float(r["Latitude"])))
        n = r["Geographical Name"].strip()
        if poly_union.contains(pt):
            dropped["inside a hamlet / DPL polygon"] += 1
            continue
        if any(g.distance(pt) <= SAME_NAME_M for g in poly_by_name.get(fold(n), [])):
            dropped["same name as a polygon within 1 km"] += 1
            continue
        par = parent_of(pt)
        if par is None:
            dropped["Edmonton / outside the CSD layer"] += 1
            continue
        if par["csdtype"] in URBAN_CSD:
            dropped["inside a city, town or village"] += 1
            continue
        level = "hamlet" if term == "Hamlet" else "locality"
        units.append((n, level, level, SRC_CGNDB, pt, {"cgndb_id": r["CGNDB ID"]}))

    # Features
    to_deg = Transformer.from_crs(3400, 4326, always_xy=True).transform
    feats, levels, kinds, no_parent = [], collections.Counter(), collections.Counter(), 0
    for u in units:
        n, level, kind, source, g = u[:5]
        extra = u[5] if len(u) > 5 else {}
        par = parent_of(g)
        if par is None:
            no_parent += 1
            continue
        gd = transform(to_deg, g)
        if g.geom_type == "Point":
            gd = shapely.set_precision(gd, 1e-5)
            props_geom = {"radius_m": POINT_RADIUS_M}
        else:
            gd = shapely.set_precision(gd.simplify(TOLERANCE_DEG, preserve_topology=True), 1e-5)
            if gd.is_empty:
                continue
            props_geom = {}
        feats.append({"type": "Feature", "geometry": mapping(gd), "properties": {
            "name": f"{n} ({kind}, {par['name']})", "place": n, "level": level, "kind": kind,
            "parent": par["name"], "parent_csduid": par["csduid"], "source": source,
            **props_geom, **extra}})
        levels[level] += 1
        kinds[(source, kind)] += 1
    # Unique names (statuses are keyed by name): a repeat inside one CSD gets its source id
    seen = collections.Counter(f["properties"]["name"] for f in feats)
    for f in feats:
        p = f["properties"]
        if seen[p["name"]] > 1:
            p["name"] = f"{p['place']} ({p['kind']} {p.get('cgndb_id', '')}, {p['parent']})".replace(" ,", ",")
    names = [f["properties"]["name"] for f in feats]
    assert len(set(names)) == len(names), collections.Counter(names).most_common(3)
    feats.sort(key=lambda f: (f["properties"]["level"], f["properties"]["name"]))
    fc = {"type": "FeatureCollection",
          "metadata": {
              "sources": {
                  SRC_HAMLET: "geospatial.alberta.ca boundaries/municipal_communities_public layer 7 "
                              "(Hamlet Boundaries, revised 2024-07-12 / 2025-02-04); Open Government Licence – Alberta",
                  SRC_USA: "same service, layer 4 (Urban Service Area); Open Government Licence – Alberta",
                  SRC_DPL: "2021 Census cartographic boundary file, designated places (ldpl000b21a); "
                           "Statistics Canada Open Licence",
                  SRC_CGNDB: "CGNDB Alberta CSV (cgn_ab_csv_eng), generic terms Locality and Hamlet; "
                             "Open Government Licence – Canada",
              },
              "point_radius_m": POINT_RADIUS_M, "simplified_deg": TOLERANCE_DEG,
              "levels": dict(levels)},
          "features": feats}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"), ensure_ascii=False)
    print(f"{len(feats)} places -> {args.out}: {dict(levels)}")
    for k, v in sorted(kinds.items()):
        print(f"  {k[0]} / {k[1]}: {v}")
    print(f"  CGNDB points dropped: {dict(dropped)}; units without a parent CSD: {no_parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
