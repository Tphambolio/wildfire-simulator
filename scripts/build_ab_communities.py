#!/usr/bin/env python3
"""Build the Alberta community units for the Neighbourhoods card outside Edmonton.

    python scripts/build_ab_communities.py [--src lcsd_ab.geojson] \
        --out frontend/public/alberta/census_subdivisions.geojson

Source: Statistics Canada, 2021 Census cartographic boundary file, census subdivisions
(``lcsd000b21a``), Alberta (PRUID 48), Statistics Canada Open Licence. Fetched from the
StatCan geography REST service (layer 9 of ``2021/Cartographic_boundary_files``) when no
``--src`` is given. The City of Edmonton census subdivision (4811061) is left out: Edmonton
keeps its own neighbourhoods (``public/edmonton/neighbourhoods.geojson``).

Each feature: ``name`` (CSD name; "Name (type)" where a name repeats), ``csduid``,
``csdtype`` and ``source`` = "Statistics Canada census subdivision (2021)". Geometry simplified
to ~30 m (topology preserved) and rounded to 5 decimals so the file stays small; it is used for
"fire within 500 m" and outlines only.
"""

from __future__ import annotations

import argparse
import collections
import json
import urllib.request

import shapely
from shapely.geometry import mapping, shape

URL = ("https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/"
       "MapServer/9/query?where=PRUID%3D%2748%27&outFields=CSDUID,CSDNAME,CSDTYPE,PRUID"
       "&outSR=4326&f=geojson&returnGeometry=true")
SOURCE = "Statistics Canada census subdivision (2021)"
EDMONTON_CSDUID = "4811061"
TOLERANCE_DEG = 0.0003


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", help="GeoJSON of Alberta CSDs (default: fetch from StatCan)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.src:
        with open(args.src, encoding="utf-8") as f:
            data = json.load(f)
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.load(r)
    feats = [f for f in data["features"] if f["properties"]["CSDUID"] != EDMONTON_CSDUID]
    names = collections.Counter(f["properties"]["CSDNAME"] for f in feats)
    out = []
    for f in sorted(feats, key=lambda f: f["properties"]["CSDUID"]):
        p = f["properties"]
        g = shape(f["geometry"]).simplify(TOLERANCE_DEG, preserve_topology=True)
        g = shapely.set_precision(g, 1e-5)
        if g.is_empty:
            continue
        name = p["CSDNAME"] if names[p["CSDNAME"]] == 1 else f'{p["CSDNAME"]} ({p["CSDTYPE"]})'
        out.append({"type": "Feature", "geometry": mapping(g),
                    "properties": {"name": name, "csduid": p["CSDUID"], "csdtype": p["CSDTYPE"],
                                   "source": SOURCE}})
    fc = {"type": "FeatureCollection",
          "metadata": {"source": "Statistics Canada, 2021 Census cartographic boundary file "
                                 "(census subdivisions, lcsd000b21a), Alberta",
                       "licence": "Statistics Canada Open Licence",
                       "simplified_deg": TOLERANCE_DEG, "excluded": "Edmonton (4811061)"},
          "features": out}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"{len(out)} census subdivisions -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
