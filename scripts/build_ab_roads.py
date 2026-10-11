#!/usr/bin/env python3
"""Build the Alberta major-roads file (FlatGeobuf, served by area by the API).

    python3 scripts/build_ab_roads.py OUT_DIR --pbf alberta-latest.osm.pbf   # used 2026-10-11
    python3 scripts/build_ab_roads.py OUT_DIR [--cache-dir DIR] [--offline]   # Overpass tiles

OpenStreetMap ways in Alberta with highway = motorway, trunk, primary or secondary and their
_link roads, as for Edmonton (``scripts/build_edmonton_roads.py``); (c) OpenStreetMap
contributors, ODbL 1.0 (this derived file stays under the ODbL). Alberta's provincial road
network (Alberta Transportation) was not used: its open releases are not offered as one
province-wide download under OGL - Alberta that we could confirm, and OSM carries the same
numbered highways with their ``ref`` (see docs/data-sources.md).

Read from the Geofabrik Alberta extract (``--pbf``, the province with a small margin; GDAL's
OSM driver via ``ogr2ogr``, streamed one way per line), or fetched from the Overpass API in
2 x 2 degree tiles clipped to the province, de-duplicated by way id (slow when Overpass is
busy: on 2026-10-11 it answered 9 of 30 tiles in 20 minutes, so the release file was built
from the extract). Ways of one road (same name, ref and class) are
joined end to end, simplified with Douglas-Peucker (SIMPLIFY_M) and cut into pieces of at most
PIECE_KM, so a box query returns local geometry, not a whole highway. Unnamed ramps are named
after the named roads at their ends, as for Edmonton. Coordinates rounded to 1e-5 degrees.

Fields: name, highway, ref, link (1 for ramps). Writes ``<out>.json`` (counts, OSM date,
SHA-256).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_edmonton_assets import cached, overpass  # noqa: E402
from build_edmonton_roads import join_lines  # noqa: E402

NAME = "ab_major_roads_osm.fgb"
CLASSES = ["motorway", "trunk", "primary", "secondary"]
SIMPLIFY_M = 5.0
PIECE_KM = 5.0
DECIMALS = 5
TILE_DEG = 2
AREA = 'area["ISO3166-2"="CA-AB"]["admin_level"="4"]->.ab;'
ATTRIBUTION = "(c) OpenStreetMap contributors, ODbL 1.0"


def tiles() -> list[tuple[int, int]]:
    return [(lat, lng) for lat in range(48, 60, TILE_DEG) for lng in range(-120, -110, TILE_DEG)]


def query(lat: int, lng: int) -> str:
    rx = "^(" + "|".join(CLASSES) + ")(_link)?$"
    b = f"({lat},{lng},{lat + TILE_DEG},{lng + TILE_DEG})"
    return f'[out:json][timeout:300];\n{AREA}\nway["highway"~"{rx}"]{b}(area.ab);\nout body geom;'


def _xy(p, lat0):
    return p[0] * 111_320 * math.cos(math.radians(lat0)), p[1] * 111_320


def _seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def simplify(line: list[list[float]], tol_m: float = SIMPLIFY_M) -> list[list[float]]:
    """Douglas-Peucker in a local metric frame at the line's own latitude (iterative)."""
    if len(line) < 3:
        return line
    lat0 = line[0][1]
    xy = [_xy(p, lat0) for p in line]
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        i, j = stack.pop()
        best, k = -1.0, -1
        for m in range(i + 1, j):
            d = _seg_dist(xy[m], xy[i], xy[j])
            if d > best:
                best, k = d, m
        if best > tol_m:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, f in zip(line, keep) if f]


def pieces(line: list[list[float]], max_m: float = PIECE_KM * 1000) -> list[list[list[float]]]:
    """Cut a line at vertices into pieces of at most ``max_m`` (a longer single segment stays)."""
    out, cur, acc = [], [line[0]], 0.0
    for a, b in zip(line, line[1:]):
        kx = 111_320 * math.cos(math.radians(a[1]))
        d = math.hypot((b[0] - a[0]) * kx, (b[1] - a[1]) * 111_320)
        if acc + d > max_m and len(cur) >= 2:
            out.append(cur)
            cur, acc = [a], 0.0
        cur.append(b)
        acc += d
    if len(cur) >= 2:
        out.append(cur)
    return out


def load_ways(cache: Path | None, offline: bool) -> tuple[dict, str | None]:
    ways: dict[int, dict] = {}
    date = None
    for lat, lng in tiles():
        raw = cached(cache, f"overpass_roads_{lat}_{lng}.json", offline,
                     lambda: overpass(query(lat, lng)))
        data = json.loads(raw)
        date = date or (data.get("osm3s", {}).get("timestamp_osm_base") or "")[:10] or None
        for e in data.get("elements", []):
            if e["type"] != "way" or not e.get("geometry") or e["id"] in ways:
                continue
            t = e.get("tags", {})
            ways[e["id"]] = {"id": e["id"], "nodes": e["nodes"],
                             "tags": {k: t[k] for k in ("highway", "name", "ref") if k in t},
                             "coords": [[p["lon"], p["lat"]] for p in e["geometry"]]}
        del data, raw
        print(f"tile {lat},{lng}: {len(ways)} ways so far", file=sys.stderr, flush=True)
    return ways, date


def _pt_key(p) -> tuple[int, int]:
    """A vertex as a node key (the extract has no node ids): coordinates at 1e-7 degrees."""
    return round(p[0] * 1e7), round(p[1] * 1e7)


def load_ways_pbf(pbf: Path) -> tuple[dict, str | None]:
    """Major-road ways from an OSM PBF extract (GDAL OSM driver, 'lines' layer)."""
    import re
    import subprocess
    import tempfile

    rx = re.compile(r'"ref"=>"((?:[^"\\]|\\.)*)"')
    classes = ",".join(f"'{c}','{c}_link'" for c in CLASSES)
    ways: dict[int, dict] = {}
    with tempfile.TemporaryDirectory() as td:
        seq = Path(td) / "roads.geojsonl"
        subprocess.run(["ogr2ogr", "-f", "GeoJSONSeq", str(seq), str(pbf), "lines",
                        "-where", f"highway IN ({classes})",
                        "-select", "osm_id,name,highway,other_tags"], check=True)
        with open(seq) as f:
            for line in f:
                ft = json.loads(line.strip().strip("\x1e"))
                pr, g = ft["properties"], ft["geometry"]
                if not g or g["type"] != "LineString":
                    continue
                t = {"highway": pr["highway"]}
                if pr.get("name"):
                    t["name"] = pr["name"]
                m = rx.search(pr.get("other_tags") or "")
                if m:
                    t["ref"] = m.group(1)
                coords = g["coordinates"]
                wid = int(pr["osm_id"])
                ways[wid] = {"id": wid, "nodes": [_pt_key(c) for c in coords], "tags": t,
                             "coords": coords}
    # Geofabrik names the extract by date: alberta-YYMMDD.osm.pbf
    m = re.search(r"(\d{6})", pbf.name)
    date = f"20{m.group(1)[:2]}-{m.group(1)[2:4]}-{m.group(1)[4:]}" if m else None
    print(f"{pbf.name}: {len(ways)} ways", file=sys.stderr)
    return ways, date


def build(out_dir: Path, cache: Path | None, offline: bool, pbf: Path | None = None) -> dict:
    import numpy as np
    import pyogrio
    import shapely

    ways, osm_date = load_ways_pbf(pbf) if pbf else load_ways(cache, offline)
    wl = list(ways.values())
    # Ramps: name unnamed _link ways after the named roads at their ends (through chains of links)
    links = [w for w in wl if w["tags"]["highway"].endswith("_link")
             and not (w["tags"].get("name") or w["tags"].get("ref"))]
    link_ends: dict[int, list[dict]] = {}
    for w in links:
        for n in (w["nodes"][0], w["nodes"][-1]):
            link_ends.setdefault(n, []).append(w)
    named_at: dict[int, set[str]] = {}
    for w in wl:
        t = w["tags"]
        label = t.get("name") or t.get("ref")
        if label and not t["highway"].endswith("_link"):
            for n in w["nodes"]:
                if n in link_ends:
                    named_at.setdefault(n, set()).add(label)

    def reach(w: dict) -> list[str]:
        out: list[str] = []
        seen = {w["id"]}
        frontier = [w["nodes"][0], w["nodes"][-1]]
        while frontier:
            n = frontier.pop()
            for r in sorted(named_at.get(n, ())):
                if r not in out:
                    out.append(r)
            for v in link_ends.get(n, []):
                if v["id"] not in seen:
                    seen.add(v["id"])
                    frontier += [v["nodes"][0], v["nodes"][-1]]
        return out

    groups: dict[tuple, list] = {}
    unnamed_ramps = 0
    counts: dict[str, int] = {}
    for w in wl:
        t = w["tags"]
        hw = t["highway"]
        counts[hw] = counts.get(hw, 0) + 1
        link = hw.endswith("_link")
        cls = hw[: -len("_link")] if link else hw
        name, ref = t.get("name") or "", t.get("ref") or ""
        if link and not name and not ref:
            roads = reach(w)
            if roads:
                name = " / ".join(sorted(roads)[:2]) + " ramp"
            else:
                unnamed_ramps += 1
        groups.setdefault((name, ref, cls, link), []).append(w["coords"])
    del ways, wl

    geoms, props = [], {"name": [], "highway": [], "ref": [], "link": []}
    vertices = 0
    for (name, ref, cls, link), lines in sorted(groups.items()):
        for line in join_lines(lines):
            s = simplify(line)
            s = [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in s]
            s = [p for i, p in enumerate(s) if i == 0 or p != s[i - 1]]
            if len(s) < 2:
                continue
            for piece in pieces(s):
                geoms.append(shapely.linestrings(piece))
                vertices += len(piece)
                props["name"].append(name)
                props["highway"].append(cls)
                props["ref"].append(ref)
                props["link"].append(1 if link else 0)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / NAME
    if path.exists():
        path.unlink()
    pyogrio.raw.write(str(path), geometry=shapely.to_wkb(np.array(geoms, dtype=object)),
                      field_data=[np.array(props[k], dtype=object if k != "link" else np.int32)
                                  for k in props],
                      fields=list(props), driver="FlatGeobuf", geometry_type="LineString",
                      crs="EPSG:4326", layer_options={"SPATIAL_INDEX": "YES"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        "file": NAME, "bytes": path.stat().st_size, "sha256": digest,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": "scripts/build_ab_roads.py",
        "source": (f"OpenStreetMap, Geofabrik extract {pbf.name}" if pbf
                   else "OpenStreetMap via the Overpass API"),
        "attribution": ATTRIBUTION, "licence": "ODbL 1.0", "osm_data_date": osm_date,
        "classes": CLASSES, "simplify_m": SIMPLIFY_M, "piece_km": PIECE_KM,
        "osm_ways": sum(counts.values()), "osm_ways_by_class": dict(sorted(counts.items())),
        "features": len(geoms), "vertices": vertices, "unnamed_ramps": unnamed_ramps,
        "named_features": sum(1 for n, r in zip(props["name"], props["ref"]) if n or r),
    }
    (out_dir / (NAME + ".json")).write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--pbf", type=Path, default=None, help="OSM PBF extract (Geofabrik Alberta)")
    a = ap.parse_args()
    print(json.dumps(build(a.out_dir, a.cache_dir, a.offline, a.pbf), indent=2))


if __name__ == "__main__":
    main()
