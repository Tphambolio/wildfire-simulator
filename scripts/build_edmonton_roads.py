#!/usr/bin/env python3
"""Build frontend/public/edmonton/roads.geojson: major roads for the Edmonton fuel grid.

OpenStreetMap ways (Overpass API) inside the fuel-grid bounds with highway = motorway, trunk,
primary or secondary, and their _link roads. (c) OpenStreetMap contributors, ODbL 1.0: keep the
attribution, and this derived file is shared under the same licence.

Per feature: {name, highway, ref} (+ `link`: 1 for _link roads, whose `highway` is the parent
class). Ways of one road (same name, ref and class) are joined end to end into one
MultiLineString and simplified with Douglas-Peucker (SIMPLIFY_M, in metres); coordinates are
rounded to 1e-5 degrees (about 1 m). Unnamed _link roads (ramps) are named after the named roads
at their two ends ("Whitemud Drive NW / 170 Street NW ramp"); a ramp joined to no named road
keeps no name and is drawn but not listed by name in the app.

Refresh (network needed; Overpass is retried on busy servers):

    python3 scripts/build_edmonton_roads.py
    # keep the raw response, and rebuild later without the network:
    python3 scripts/build_edmonton_roads.py --cache-dir /tmp/roads-cache
    python3 scripts/build_edmonton_roads.py --cache-dir /tmp/roads-cache --offline
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_edmonton_assets import BOUNDS, REPO, cached, overpass  # noqa: E402

OUT = REPO / "frontend" / "public" / "edmonton" / "roads.geojson"
CLASSES = ["motorway", "trunk", "primary", "secondary"]
SIMPLIFY_M = 5.0
DECIMALS = 5
ATTRIBUTION = "(c) OpenStreetMap contributors, ODbL 1.0"


def query() -> str:
    b = f"({BOUNDS['south']},{BOUNDS['west']},{BOUNDS['north']},{BOUNDS['east']})"
    rx = "^(" + "|".join(CLASSES) + ")(_link)?$"
    return f'[out:json][timeout:180];\nway["highway"~"{rx}"]{b};\nout body geom;'


# ── Geometry ─────────────────────────────────────────────────────────────────

LAT0 = (BOUNDS["south"] + BOUNDS["north"]) / 2
KX = 111_320 * math.cos(math.radians(LAT0))
KY = 111_320


def _seg_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = [(q[0] * KX, q[1] * KY) for q in (p, a, b)]
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(line: list[list[float]], tol_m: float = SIMPLIFY_M) -> list[list[float]]:
    """Douglas-Peucker in a local metric frame (iterative)."""
    if len(line) < 3:
        return line
    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        i, j = stack.pop()
        best, k = -1.0, -1
        for m in range(i + 1, j):
            d = _seg_dist(line[m], line[i], line[j])
            if d > best:
                best, k = d, m
        if best > tol_m:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [p for p, f in zip(line, keep) if f]


def join_lines(lines: list[list[list[float]]]) -> list[list[list[float]]]:
    """Join lines that share an end point (greedy), so a road is a few long lines."""
    lines = [l[:] for l in lines if len(l) >= 2]
    merged = True
    while merged:
        merged = False
        ends: dict[tuple, list[int]] = {}
        for i, l in enumerate(lines):
            ends.setdefault(tuple(l[0]), []).append(i)
            ends.setdefault(tuple(l[-1]), []).append(i)
        for i, l in enumerate(lines):
            if l is None:
                continue
            for j in ends.get(tuple(l[-1]), []):
                if j == i or lines[j] is None:
                    continue
                m = lines[j]
                if m[0] == l[-1]:
                    lines[i] = l + m[1:]
                elif m[-1] == l[-1]:
                    lines[i] = l + m[::-1][1:]
                else:
                    continue
                lines[j] = None
                merged = True
                break
            if merged:
                break
        lines = [l for l in lines if l is not None]
    return lines


# ── Build ────────────────────────────────────────────────────────────────────


def build(cache: Path | None, offline: bool) -> dict:
    raw = cached(cache, "overpass_roads.json", offline, lambda: overpass(query()))
    data = json.loads(raw)
    ways = [e for e in data.get("elements", []) if e["type"] == "way" and e.get("geometry")]

    # Ramps: name unnamed _link ways after the named roads at their ends (through chains of links)
    named_at: dict[int, set[str]] = {}
    for w in ways:
        t = w.get("tags", {})
        label = t.get("name") or t.get("ref")
        if label and not t["highway"].endswith("_link"):
            for n in (w["nodes"][0], w["nodes"][-1], *w["nodes"]):
                named_at.setdefault(n, set()).add(label)
    links = [w for w in ways if w["tags"]["highway"].endswith("_link") and not (w["tags"].get("name") or w["tags"].get("ref"))]
    link_ends: dict[int, list[dict]] = {}
    for w in links:
        for n in (w["nodes"][0], w["nodes"][-1]):
            link_ends.setdefault(n, []).append(w)

    def reach(w: dict) -> list[str]:
        """Named roads reachable from both ends of a ramp through other unnamed ramps."""
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

    groups: dict[tuple, list[list[list[float]]]] = {}
    unnamed_ramps = 0
    for w in ways:
        t = w.get("tags", {})
        hw = t["highway"]
        link = hw.endswith("_link")
        cls = hw[: -len("_link")] if link else hw
        name = t.get("name") or ""
        ref = t.get("ref") or ""
        if link and not name and not ref:
            roads = reach(w)
            if roads:
                name = " / ".join(sorted(roads)[:2]) + " ramp"
            else:
                unnamed_ramps += 1
        coords = [[p["lon"], p["lat"]] for p in w["geometry"]]
        groups.setdefault((name, ref, cls, link), []).append(coords)

    feats = []
    n_ways = len(ways)
    for (name, ref, cls, link), lines in sorted(groups.items()):
        out_lines = []
        for l in join_lines(lines):
            s = simplify(l)
            s = [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in s]
            s = [p for i, p in enumerate(s) if i == 0 or p != s[i - 1]]
            if len(s) >= 2:
                out_lines.append(s)
        if not out_lines:
            continue
        props = {"name": name, "highway": cls, "ref": ref}
        if link:
            props["link"] = 1
        if not name:
            del props["name"]
        geom = {"type": "LineString", "coordinates": out_lines[0]} if len(out_lines) == 1 else {"type": "MultiLineString", "coordinates": out_lines}
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    counts: dict[str, int] = {}
    for w in ways:
        counts[w["tags"]["highway"]] = counts.get(w["tags"]["highway"], 0) + 1
    vertices = sum(len(l) for f in feats for l in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"]))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "builder": "scripts/build_edmonton_roads.py",
            "source": "OpenStreetMap via the Overpass API",
            "attribution": ATTRIBUTION,
            "licence": "ODbL 1.0",
            "osm_data_date": (data.get("osm3s", {}).get("timestamp_osm_base") or "")[:10] or None,
            "bounds_wgs84": BOUNDS,
            "classes": CLASSES,
            "simplify_m": SIMPLIFY_M,
            "osm_ways": n_ways,
            "osm_ways_by_class": dict(sorted(counts.items())),
            "features": len(feats),
            "vertices": vertices,
            "unnamed_ramps": unnamed_ramps,
        },
        "features": feats,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--cache-dir", type=Path, default=None, help="save / reuse the raw Overpass response here")
    ap.add_argument("--offline", action="store_true", help="use only --cache-dir (no network)")
    a = ap.parse_args()
    fc = build(a.cache_dir, a.offline)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    m = fc["metadata"]
    print(f"wrote {a.out} ({a.out.stat().st_size / 1024:.0f} KB): {m['features']} features, {m['vertices']} vertices "
          f"from {m['osm_ways']} OSM ways {m['osm_ways_by_class']}; OSM data {m['osm_data_date']}")


if __name__ == "__main__":
    main()
