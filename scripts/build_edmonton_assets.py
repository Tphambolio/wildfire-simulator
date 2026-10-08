#!/usr/bin/env python3
"""Build frontend/public/edmonton/assets.geojson: critical assets for the Edmonton fuel grid.

Every feature comes from an open, citable source and carries a common schema:

    {name, category, source, source_id, licence, fetched_at}  (+ optional `detail`)

Sources
-------
* City of Edmonton Open Data (data.edmonton.ca, Socrata SODA API),
  Open Government Licence - City of Edmonton:
    fire stations b4y7-zhnz, police stations e7aq-scxv, recreation centres nz3t-vyg3
    (facility_type "Recreation Centre": candidate reception centres), seniors centres
    zmac-3mxq, Edmonton Catholic schools (current) gfxq-u8uu, EPSB school locations
    996c-239n, LRT stations and stops fhxi-cnhe.
* Statistics Canada, Open Database of Healthcare Facilities (ODHF v1.1, open.canada.ca
  dataset 543fe07a-fd79-40e9-a829-ccd697526765), Open Government Licence - Canada:
  hospitals and nursing/residential care facilities that have coordinates.
* OpenStreetMap via the Overpass API, (c) OpenStreetMap contributors, ODbL 1.0: water and
  wastewater treatment plants, power plants, and power substations of 69 kV or more
  (traction and distribution-only substations below that are left out).
* One manual point: the City of Edmonton Emergency Operations Centre, which is in none of
  the open datasets above (source "City of Edmonton public information").

Everything is clipped to the Edmonton fuel grid (data/Edmonton_FBP_FuelLayer_20251105_10m.tif,
WGS84 bounds below). Points stay points; OSM areas become their centroid, and the plant
polygons (few, large sites) keep their outline so "within 500 m" is measured from the site
edge.

Refresh (network needed; about a minute):

    python3 scripts/build_edmonton_assets.py
    # offline re-run from saved raw responses:
    python3 scripts/build_edmonton_assets.py --cache-dir /tmp/assets-cache --offline

Reproducibility: the raw responses can be saved with --cache-dir; the output records
`fetched_at` per feature and a `metadata` block with each source's data date.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "frontend" / "public" / "edmonton" / "assets.geojson"

# WGS84 bounds of data/Edmonton_FBP_FuelLayer_20251105_10m.tif (EPSG:3776 -> 4326)
BOUNDS = {"west": -113.71564, "south": 53.33576, "east": -113.27046, "north": 53.71772}

UA = "FireSim-asset-build/1.0 (+https://github.com/Tphambolio/wildfire-simulator)"

LIC_CITY = "Open Government Licence - City of Edmonton"
LIC_CANADA = "Open Government Licence - Canada"
LIC_OSM = "ODbL 1.0 (c) OpenStreetMap contributors"

SRC_CITY = "City of Edmonton Open Data"
SRC_ODHF = "Statistics Canada ODHF v1.1"
SRC_OSM = "OpenStreetMap"
SRC_MANUAL = "City of Edmonton public information"

CITY_DATASETS = {
    # id: (category, name field, detail builder)
    "b4y7-zhnz": "fire_station",
    "e7aq-scxv": "police",
    "nz3t-vyg3": "reception",
    "zmac-3mxq": "seniors",
    "gfxq-u8uu": "school",
    "996c-239n": "school",
    "fhxi-cnhe": "transit",
}

ODHF_URL = "https://www150.statcan.gc.ca/n1/pub/13-26-0001/2020001/ODHF.zip"
ODHF_TYPES = {"Hospitals", "Nursing and residential care facilities"}

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Manual point (not in any open dataset). Position as previously bundled in FireSim; check
# against City of Edmonton public information before relying on it operationally.
EOC_MANUAL = {
    "name": "City of Edmonton Emergency Operations Centre",
    "lng": -113.4808,
    "lat": 53.5460,
    "detail": "Manual point: not in an open dataset; position not independently verified",
}


# ── HTTP ─────────────────────────────────────────────────────────────────────


def _get(url: str, data: bytes | None = None, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def cached(cache: Path | None, name: str, offline: bool, fetch) -> bytes:
    """Return the raw response, from the cache directory when present (or required)."""
    if cache:
        p = cache / name
        if p.exists():
            return p.read_bytes()
        if offline:
            sys.exit(f"--offline: {p} is missing")
    raw = fetch()
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_bytes(raw)
    return raw


def overpass(query: str) -> bytes:
    last: Exception | None = None
    for attempt in range(4):
        for url in OVERPASS_ENDPOINTS:
            try:
                raw = _get(url, urllib.parse.urlencode({"data": query}).encode())
                json.loads(raw)
                return raw
            except Exception as e:  # busy servers answer 429/504 or an HTML error page
                last = e
                print(f"  overpass {url}: {e}", file=sys.stderr)
        time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"Overpass failed: {last}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def in_bounds(lng: float, lat: float) -> bool:
    return BOUNDS["west"] <= lng <= BOUNDS["east"] and BOUNDS["south"] <= lat <= BOUNDS["north"]


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    kx = 111_320 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 111_320)


def title(s: str) -> str:
    """'POLICE SERVICES - NORTHEAST DIVISION' -> 'Police Services - Northeast Division'."""
    s = " ".join(s.split())
    if s.isupper() or s.islower():
        small = {"and", "of", "the", "at", "for", "de", "la"}
        words = []
        for i, w in enumerate(s.lower().split(" ")):
            if i and w in small:
                words.append(w)
            elif w in {"nw", "ne", "sw", "se", "rcmp", "epsb", "lrt", "nait", "ymca"}:
                words.append(w.upper())
            elif len(w) > 1 and w[0] in "(\"'":
                words.append(w[0] + w[1:].capitalize())
            else:
                words.append(w[:1].upper() + w[1:])
        s = " ".join(words)
    return s.replace("Childrens", "Children's").replace("Josephs", "Joseph's").replace("Mccauley", "McCauley")


def point_feature(lng: float, lat: float, props: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
        "properties": props,
    }


def props(name: str, category: str, source: str, source_id: str, licence: str, fetched_at: str, detail: str | None = None) -> dict:
    p = {
        "name": name,
        "category": category,
        "source": source,
        "source_id": source_id,
        "licence": licence,
        "fetched_at": fetched_at,
    }
    if detail:
        p["detail"] = detail
    return p


# ── City of Edmonton Open Data ───────────────────────────────────────────────


def city_name(dataset: str, row: dict) -> tuple[str, str | None] | None:
    if dataset == "b4y7-zhnz":
        return f"Fire Station {row['name']}" if row.get("name", "").isdigit() else title(row.get("name", "Fire station")), title(row.get("address", "")) or None
    if dataset == "e7aq-scxv":
        return title(row["name"]), title(row.get("address", "")) or None
    if dataset == "nz3t-vyg3":
        if row.get("facility_type") != "Recreation Centre":
            return None
        return row["facility_name"], "Recreation centre (candidate reception centre)"
    if dataset == "zmac-3mxq":
        return title(row["name"]), title(row.get("address", "")) or None
    if dataset == "gfxq-u8uu":
        return f"{row['school_name']} (Catholic)", row.get("grades_offered") or row.get("grade_level")
    if dataset == "996c-239n":
        return f"{row['school_nam']} (EPSB)", row.get("grades")
    if dataset == "fhxi-cnhe":
        return row["lrt_stop_description"], f"LRT stop {row.get('lrt_stop_number', '')}".strip()
    return None


def city_source_id(dataset: str, row: dict) -> str:
    key = {
        "b4y7-zhnz": "name",
        "e7aq-scxv": "name",
        "nz3t-vyg3": "facility_name",
        "zmac-3mxq": "name",
        "gfxq-u8uu": "ab_school_number",
        "996c-239n": "abed_id",
        "fhxi-cnhe": "lrt_stop_number",
    }[dataset]
    return f"data.edmonton.ca/{dataset}#{row.get(key, '')}"


def fetch_city(cache: Path | None, offline: bool, now: str, meta: dict) -> list[dict]:
    out = []
    for ds, category in CITY_DATASETS.items():
        info = json.loads(cached(cache, f"city_{ds}_meta.json", offline,
                                 lambda: _get(f"https://data.edmonton.ca/api/views/{ds}.json")))
        rows = json.loads(cached(cache, f"city_{ds}.json", offline,
                                 lambda: _get(f"https://data.edmonton.ca/resource/{ds}.json?$limit=50000")))
        updated = info.get("rowsUpdatedAt")
        meta[ds] = {
            "name": info.get("name"),
            "source": SRC_CITY,
            "url": f"https://data.edmonton.ca/d/{ds}",
            "licence": LIC_CITY,
            "data_updated": datetime.fromtimestamp(updated, timezone.utc).date().isoformat() if updated else None,
            "count": 0,
        }
        for row in rows:
            try:
                lat, lng = float(row["latitude"]), float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not in_bounds(lng, lat):
                continue
            nd = city_name(ds, row)
            if not nd:
                continue
            name, detail = nd
            # One point per site: the same category within 30 m, or the same name within 200 m
            # (e.g. the RCMP listed at two neighbouring addresses)
            if any(f["properties"]["category"] == category
                   and (dist_m(tuple(f["geometry"]["coordinates"]), (lng, lat)) < 30
                        or (f["properties"]["name"] == name and dist_m(tuple(f["geometry"]["coordinates"]), (lng, lat)) < 200))
                   for f in out):
                continue
            out.append(point_feature(lng, lat, props(name, category, SRC_CITY, city_source_id(ds, row), LIC_CITY, now, detail)))
            meta[ds]["count"] += 1
    return out


# ── StatCan ODHF ─────────────────────────────────────────────────────────────


def fetch_odhf(cache: Path | None, offline: bool, now: str, meta: dict) -> list[dict]:
    raw = cached(cache, "ODHF.zip", offline, lambda: _get(ODHF_URL))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("cp1252", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    kept: list[dict] = []
    no_coords = 0
    for r in rows:
        if r.get("odhf_facility_type") not in ODHF_TYPES:
            continue
        try:
            lat, lng = float(r["latitude"]), float(r["longitude"])
        except ValueError:
            if "edmonton" in (r.get("city") or "").lower():
                no_coords += 1
            continue
        if not in_bounds(lng, lat):
            continue
        nm = title(r["facility_name"].replace("’", "'")).replace("Covenant Health ", "")
        # Same facility listed by two providers (CIHI and PHAC): one record per site (within
        # 150 m, or the same name within 1 km)
        dup = next((k for k in kept if dist_m(k["xy"], (lng, lat)) < 150
                    or (dist_m(k["xy"], (lng, lat)) < 1000 and nm.lower() in [n.lower() for n in k["names"]])), None)
        if dup:
            if nm.lower() not in [n.lower() for n in dup["names"]]:
                dup["names"].append(nm)
            dup["ids"].append(r["index"])
            continue
        kept.append({"xy": (lng, lat), "names": [nm], "ids": [r["index"]], "type": r["odhf_facility_type"], "src_type": r["source_facility_type"]})
    out = []
    for k in kept:
        # Prefer the name the others contain (the campus), then the longer; mention the others
        # (e.g. the Stollery and the Mazankowski on the University of Alberta Hospital site)
        names = sorted(k["names"], key=lambda n: (-sum(n.lower() in o.lower() for o in k["names"]), -len(n)))
        name = names[0]
        others = [n for n in names[1:] if name.lower() not in n.lower()]
        # The source sub-type only for single records (merged ones may differ per record)
        detail = f"{k['type']} ({k['src_type']})" if k["src_type"] and len(k["ids"]) == 1 else k["type"]
        if others:
            detail += "; also: " + ", ".join(others)
        out.append(point_feature(k["xy"][0], k["xy"][1], props(name, "hospital", SRC_ODHF, "odhf#" + "+".join(k["ids"]), LIC_CANADA, now, detail)))
    meta["odhf"] = {
        "name": "Open Database of Healthcare Facilities (ODHF) v1.1",
        "source": SRC_ODHF,
        "url": "https://open.canada.ca/data/en/dataset/543fe07a-fd79-40e9-a829-ccd697526765",
        "licence": LIC_CANADA,
        "data_updated": "2020-04-20",
        "count": len(out),
        "edmonton_records_without_coordinates": no_coords,
    }
    return out


# ── OpenStreetMap (Overpass) ─────────────────────────────────────────────────


def overpass_query() -> str:
    b = f"({BOUNDS['south']},{BOUNDS['west']},{BOUNDS['north']},{BOUNDS['east']})"
    return f"""[out:json][timeout:120];
(
  nwr["man_made"="water_works"]{b};
  nwr["man_made"="wastewater_plant"]{b};
  nwr["landuse"="industrial"]["name"~"Water Treatment Plant"]{b};
  nwr["power"="plant"]{b};
);
out geom tags;
(
  nwr["power"="substation"]["voltage"]{b};
);
out center tags;"""


def max_kv(voltage: str) -> float:
    vals = []
    for part in voltage.replace(":", ";").split(";"):
        try:
            vals.append(float(part) / 1000)
        except ValueError:
            pass
    return max(vals) if vals else 0.0


def ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    a2 = sx = sy = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i - 1]
        x2, y2 = ring[i]
        c = x1 * y2 - x2 * y1
        a2 += c
        sx += (x1 + x2) * c
        sy += (y1 + y2) * c
    if abs(a2) < 1e-15:
        xs, ys = zip(*ring)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return sx / (3 * a2), sy / (3 * a2)


def osm_geometry(el: dict) -> tuple[dict | None, tuple[float, float] | None]:
    """Polygon (closed ways, outer rings of relations) and an anchor point."""
    if el["type"] == "node":
        return None, (el["lon"], el["lat"])
    rings: list[list[list[float]]] = []
    if el["type"] == "way" and el.get("geometry"):
        rings = [[[round(p["lon"], 6), round(p["lat"], 6)] for p in el["geometry"]]]
    elif el["type"] == "relation":
        for m in el.get("members", []):
            if m.get("role") == "outer" and m.get("geometry"):
                rings.append([[round(p["lon"], 6), round(p["lat"], 6)] for p in m["geometry"]])
    rings = [r for r in rings if len(r) >= 4 and r[0] == r[-1]]
    if el.get("center"):
        anchor = (el["center"]["lon"], el["center"]["lat"])
    elif rings:
        anchor = ring_centroid(max(rings, key=len))
    else:
        return None, None
    if not rings:
        return None, anchor
    geom = {"type": "Polygon", "coordinates": [rings[0]]} if len(rings) == 1 else {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
    return geom, anchor


def fetch_osm(cache: Path | None, offline: bool, now: str, meta: dict) -> list[dict]:
    raw = cached(cache, "overpass.json", offline, lambda: overpass(overpass_query()))
    data = json.loads(raw)
    out = []
    counts = {"water": 0, "power": 0}
    for el in data.get("elements", []):
        t = el.get("tags", {})
        geom, anchor = osm_geometry(el)
        if not anchor or not in_bounds(*anchor):
            continue
        sid = f"osm:{el['type']}/{el['id']}"
        name = t.get("name")
        if t.get("man_made") in ("water_works", "wastewater_plant") or (t.get("landuse") == "industrial" and "Water Treatment" in (name or "")):
            if "storm" in (name or "").lower():
                continue  # storm basins are drainage, not treatment
            if not name:
                continue
            kind = "Wastewater treatment" if t.get("man_made") == "wastewater_plant" else "Water treatment"
            detail = kind + (f", {t['operator']}" if t.get("operator") else "")
            cat = "water"
        elif t.get("power") == "plant":
            if not name:
                continue
            src = t.get("plant:source")
            out_e = t.get("plant:output:electricity")
            detail = "Power plant" + (f" ({src}" + (f", {out_e}" if out_e else "") + ")" if src else "") + (f", {t['operator']}" if t.get("operator") else "")
            cat = "power"
        elif t.get("power") == "substation":
            kv = max_kv(t.get("voltage", ""))
            if kv < 69 or t.get("substation") in ("traction", "minor_distribution"):
                continue
            name = name or f"Substation {t.get('ref', '')}".strip()
            if not t.get("name"):
                name = f"{name} ({t['operator']})" if t.get("operator") else f"Unnamed substation"
            detail = f"Substation, {kv:g} kV" + (f", {t['substation']}" if t.get("substation") not in (None, "yes") else "") + (f", {t['operator']}" if t.get("operator") and t.get("name") else "")
            cat = "power"
            geom = None  # substations: points keep the file small
        else:
            continue
        p = props(name, cat, SRC_OSM, sid, LIC_OSM, now, detail)
        if geom is not None:
            f = {"type": "Feature", "geometry": geom, "properties": {**p, "anchor": [round(anchor[0], 6), round(anchor[1], 6)]}}
        else:
            f = point_feature(anchor[0], anchor[1], p)
        out.append(f)
        counts[cat] += 1
    meta["osm"] = {
        "name": "OpenStreetMap (Overpass API)",
        "source": SRC_OSM,
        "url": "https://www.openstreetmap.org/copyright",
        "licence": LIC_OSM,
        "data_updated": (data.get("osm3s", {}).get("timestamp_osm_base") or "")[:10] or None,
        "count": len(out),
        "by_category": counts,
    }
    return out


def manual_eoc(now: str, meta: dict) -> list[dict]:
    e = EOC_MANUAL
    meta["manual"] = {
        "name": "City of Edmonton Emergency Operations Centre",
        "source": SRC_MANUAL,
        "licence": "Public information (no dataset)",
        "count": 1,
        "note": e["detail"],
    }
    return [point_feature(e["lng"], e["lat"], props(e["name"], "eoc", SRC_MANUAL, "manual:edmonton-eoc", "Public information (no dataset)", now, e["detail"]))]


# ── Main ─────────────────────────────────────────────────────────────────────


def build(cache: Path | None, offline: bool) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta: dict = {}
    feats = manual_eoc(now, meta) + fetch_odhf(cache, offline, now, meta) + fetch_city(cache, offline, now, meta) + fetch_osm(cache, offline, now, meta)
    order = ["eoc", "hospital", "fire_station", "police", "reception", "seniors", "school", "water", "power", "transit"]
    feats.sort(key=lambda f: (order.index(f["properties"]["category"]), f["properties"]["name"]))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "built": now,
            "builder": "scripts/build_edmonton_assets.py",
            "bounds_wgs84": BOUNDS,
            "attribution": "City of Edmonton Open Data (Open Government Licence - City of Edmonton); "
            "Statistics Canada ODHF (Open Government Licence - Canada); (c) OpenStreetMap contributors (ODbL)",
            "sources": meta,
        },
        "features": feats,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--cache-dir", type=Path, default=None, help="save / reuse raw responses here")
    ap.add_argument("--offline", action="store_true", help="use only --cache-dir (no network)")
    a = ap.parse_args()
    fc = build(a.cache_dir, a.offline)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    by: dict[str, int] = {}
    for f in fc["features"]:
        by[f["properties"]["category"]] = by.get(f["properties"]["category"], 0) + 1
    print(f"wrote {a.out} ({a.out.stat().st_size / 1024:.0f} KB): {len(fc['features'])} features {by}")


if __name__ == "__main__":
    main()
