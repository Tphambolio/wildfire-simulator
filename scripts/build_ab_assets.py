#!/usr/bin/env python3
"""Build the Alberta critical-assets file (FlatGeobuf, served by area by the API).

    python3 scripts/build_ab_assets.py OUT_DIR [--cache-dir DIR] [--offline]

Same categories (ICS keys of ``frontend/src/utils/assets.ts``) and per-feature schema as the
bundled Edmonton layer (``scripts/build_edmonton_assets.py``), for the whole province:

* **hospital** (hospitals and care facilities): Statistics Canada Open Database of Healthcare
  Facilities (ODHF v1.1, 2020; Open Government Licence - Canada), Alberta records of type
  "Hospitals" and "Nursing and residential care facilities" (outpatient / day-surgery clinics
  left out, as for Edmonton), merged with OpenStreetMap hospitals, nursing homes, assisted living
  and hospices (ODbL). ODHF records without coordinates (177 of 331 in Alberta) are placed only
  when exactly one OpenStreetMap record in the same census subdivision (by name) has an agreeing name;
  otherwise they are left out (no geocoding of addresses). Single-source sites are flagged
  "verify".
* **school**: OpenStreetMap ``amenity=school`` (named). Alberta Education's school list (OGL -
  Alberta) has addresses but no coordinates, so it is not used (see docs/data-sources.md).
* **fire_station**, **police**, **ems** (EMS / ambulance stations): OpenStreetMap
  ``amenity=fire_station``, ``amenity=police``, ``emergency=ambulance_station``.
* **reception** (recreation centres, candidate reception centres): OpenStreetMap named
  ``amenity=community_centre`` and ``leisure=sports_centre`` whose name marks a public
  recreation facility (``REC_WORDS``).
* **seniors**: OpenStreetMap community / social centres and non-residential social facilities
  whose name or ``social_facility:for`` marks seniors.
* **water**: OpenStreetMap ``man_made=water_works`` / ``wastewater_plant`` (unnamed sites kept,
  labelled by kind: many town lagoons have no name).
* **power**: OpenStreetMap ``power=plant`` (named, or >= 5 MW) and ``power=substation`` of
  69 kV or more (traction / minor distribution left out), as for Edmonton.
* **transit**: OpenStreetMap light-rail stations (Calgary CTrain; Edmonton's come from the City).

Nothing personal is kept: no addresses, phone numbers or e-mails; ``detail`` holds the facility
type, kV, operator (an organisation) or grades. Geometry is the anchor point; plant sites keep
their outline in ``outline`` (GeoJSON text) so "within 500 m" is measured from the site edge.

Fields: name, category, detail, source, source_id, licence, sources (JSON list of
{source, source_id}), verify, outline. Writes ``<out>.json`` (counts, sources, data dates,
SHA-256). The whole province is a few MB; the API reads it by box (packed R-tree).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
import csv
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_edmonton_assets import (  # noqa: E402
    LIC_CANADA, LIC_OSM, ODHF_URL, SRC_ODHF, SRC_OSM, _get, cached, dist_m, is_clinic,
    max_kv, names_agree, osm_geometry, overpass, title,
)

REPO = Path(__file__).resolve().parents[1]
NAME = "ab_assets_2026.fgb"
CSD_PATH = REPO / "frontend" / "public" / "alberta" / "census_subdivisions.geojson"
AREA = 'area["ISO3166-2"="CA-AB"]["admin_level"="4"]->.ab;'
CARE_MATCH_M = 300
REC_WORDS = re.compile(r"recreation|leisure|arena|aquatic|multiplex|sportsplex|agriplex|community|"
                       r"civic|memorial|centennial|complex|curling|field ?house|pavilion|hall",
                       re.I)
SENIOR_WORDS = re.compile(r"senior|golden age|elder", re.I)
ATTRIBUTION = ("Statistics Canada ODHF (Open Government Licence - Canada); "
               "(c) OpenStreetMap contributors (ODbL)")


def in_alberta_box(xy) -> bool:
    """Inside Alberta's bounding box (49-60 N, 120-110 W, with a 0.05 degree margin)."""
    return -120.05 <= xy[0] <= -109.95 and 48.95 <= xy[1] <= 60.05


def overpass_query() -> str:
    return f"""[out:json][timeout:600];
{AREA}
(
  nwr["amenity"="hospital"](area.ab);
  nwr["healthcare"="hospital"](area.ab);
  nwr["amenity"="nursing_home"](area.ab);
  nwr["healthcare"="nursing_home"](area.ab);
  nwr["social_facility"~"^(nursing_home|assisted_living)$"](area.ab);
  nwr["healthcare"="hospice"](area.ab);
  nwr["amenity"="school"](area.ab);
  nwr["amenity"="fire_station"](area.ab);
  nwr["amenity"="police"](area.ab);
  nwr["emergency"="ambulance_station"](area.ab);
  nwr["amenity"="community_centre"](area.ab);
  nwr["amenity"="social_centre"](area.ab);
  nwr["amenity"="social_facility"](area.ab);
  nwr["leisure"="sports_centre"]["name"](area.ab);
  nwr["railway"="station"]["station"~"light_rail|subway"](area.ab);
  nwr["public_transport"="station"]["light_rail"="yes"](area.ab);
);
out center tags;
(
  nwr["man_made"="water_works"](area.ab);
  nwr["man_made"="wastewater_plant"](area.ab);
  nwr["power"="plant"](area.ab);
);
out geom tags;
(
  nwr["power"="substation"]["voltage"](area.ab);
);
out center tags;"""


def _mw(v: str | None) -> float:
    m = re.match(r"\s*([\d.]+)\s*(k|M|G)?W", v or "")
    if not m:
        return 0.0
    return float(m.group(1)) * {"k": 1e-3, "M": 1.0, "G": 1e3, None: 1e-6}[m.group(2)]


def classify(t: dict) -> tuple[str, str] | None:
    """(category, detail) of an OSM element's tags, or None (not a critical asset)."""
    name = t.get("name") or ""
    am, hc, sf = t.get("amenity"), t.get("healthcare"), t.get("social_facility")
    if am == "hospital" or hc == "hospital":
        return "hospital", "Hospital"
    if hc == "hospice":
        return "hospital", "Hospice"
    if am == "nursing_home" or hc == "nursing_home" or sf == "nursing_home":
        return "hospital", "Nursing home"
    if sf == "assisted_living":
        return "hospital", "Assisted living"
    if am == "fire_station":
        return "fire_station", "Fire station"
    if am == "police":
        return "police", "Police station"
    if t.get("emergency") == "ambulance_station":
        return "ems", "EMS station"
    if am == "school":
        return "school", "School"
    if am in ("community_centre", "social_centre", "social_facility"):
        if SENIOR_WORDS.search(name) or "senior" in (t.get("social_facility:for") or "") \
                or "senior" in (t.get("community_centre:for") or ""):
            if am == "social_facility" and sf in ("group_home", "shelter", "nursing_home",
                                                  "assisted_living"):
                return None
            return "seniors", "Seniors centre"
        if am == "community_centre" and name:
            return "reception", "Community centre (candidate reception centre)"
        return None
    if t.get("leisure") == "sports_centre":
        if name and REC_WORDS.search(name) and t.get("access") not in ("private", "customers"):
            return "reception", "Recreation centre (candidate reception centre)"
        return None
    if t.get("railway") == "station" or t.get("public_transport") == "station":
        return "transit", "LRT station"
    return None


def osm_assets(data: dict) -> list[dict]:
    out = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        geom, anchor = osm_geometry(el)
        if not anchor:
            continue
        name = t.get("name")
        sid = f"osm:{el['type']}/{el['id']}"
        outline = None
        if t.get("man_made") in ("water_works", "wastewater_plant"):
            if "storm" in (name or "").lower():
                continue
            kind = "Wastewater treatment" if t["man_made"] == "wastewater_plant" else "Water treatment"
            cat, detail = "water", kind + (f", {t['operator']}" if t.get("operator") else "")
            name = name or f"{kind} plant (unnamed)"
            outline = geom
        elif t.get("power") == "plant":
            mw = _mw(t.get("plant:output:electricity"))
            if not name and mw < 5:
                continue
            src = t.get("plant:source")
            cat = "power"
            detail = "Power plant" + (f" ({src}" + (f", {t['plant:output:electricity']}" if t.get("plant:output:electricity") else "") + ")" if src else "") + (f", {t['operator']}" if t.get("operator") else "")
            name = name or f"Power plant ({src or 'unnamed'})"
            outline = geom
        elif t.get("power") == "substation":
            kv = max_kv(t.get("voltage", ""))
            if kv < 69 or t.get("substation") in ("traction", "minor_distribution"):
                continue
            cat = "power"
            if not name:
                name = f"Substation {t['ref']}" if t.get("ref") else (f"Substation ({t['operator']})" if t.get("operator") else "Unnamed substation")
            detail = f"Substation, {kv:g} kV" + (f", {t['operator']}" if t.get("operator") else "")
        else:
            c = classify(t)
            if c is None or not name:
                continue
            cat, detail = c
        out.append({"name": " ".join(name.split()), "category": cat, "detail": detail, "xy": anchor,
                    "outline": outline, "sources": [{"source": SRC_OSM, "source_id": sid}]})
    return out


# ── ODHF ─────────────────────────────────────────────────────────────────────


def odhf_alberta(raw_zip: bytes) -> list[dict]:
    z = zipfile.ZipFile(io.BytesIO(raw_zip))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    raw = z.read(name)
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        txt = raw.decode("cp1252")
    out = []
    for r in csv.DictReader(io.StringIO(txt)):
        if r["province"] != "ab" or r["odhf_facility_type"] not in (
                "Hospitals", "Nursing and residential care facilities"):
            continue
        nm = title(r["facility_name"].replace("’", "'")).replace("Covenant Health ", "")
        if r["odhf_facility_type"] == "Hospitals" and is_clinic(nm):
            continue
        try:
            xy = (float(r["longitude"]), float(r["latitude"]))
        except ValueError:
            xy = None
        if xy is not None and xy[0] > 0:
            # 4 Alberta ODHF records carry a positive (east) longitude: a sign error
            xy = (-xy[0], xy[1])
        if xy is not None and not in_alberta_box(xy):
            xy = None
        kind = "Hospital" if r["odhf_facility_type"] == "Hospitals" else "Nursing and residential care"
        out.append({"name": nm, "xy": xy, "csd": (r.get("CSDname") or "").strip().lower() or None, "kind": kind,
                    "source_id": f"odhf#{r['index']}"})
    return out


class CsdLookup:
    """Census subdivision (2021) name of a point, from the bundled Alberta CSD file (ODHF gives
    the subdivision name, not its code)."""

    def __init__(self, path: Path):
        from shapely import STRtree
        from shapely.geometry import shape

        fc = json.loads(path.read_text())
        self.geoms = [shape(f["geometry"]) for f in fc["features"]]
        self.ids = [f["properties"]["name"].strip().lower() for f in fc["features"]]
        self.tree = STRtree(self.geoms)

    def __call__(self, xy) -> str | None:
        from shapely.geometry import Point

        p = Point(xy)
        for i in self.tree.query(p, predicate="intersects"):
            return self.ids[int(i)]
        return None


def merge_care(odhf: list[dict], osm: list[dict], csd: CsdLookup, log: list[str]) -> tuple[list[dict], dict]:
    """ODHF records joined to OSM care records (name + distance, or name + census subdivision for
    ODHF records without coordinates)."""
    care = [o for o in osm if o["category"] == "hospital"]
    for o in care:
        o["csd"] = csd(o["xy"])
    stats = {"odhf_with_xy": 0, "odhf_matched_osm": 0, "odhf_placed_by_osm": 0,
             "odhf_left_out_no_xy": 0}
    out: list[dict] = []
    used: set[int] = set()
    for r in odhf:
        if r["xy"] is not None:
            stats["odhf_with_xy"] += 1
            m = [i for i, o in enumerate(care) if i not in used and dist_m(o["xy"], r["xy"]) <= CARE_MATCH_M
                 and names_agree(o["name"], r["name"])]
        else:
            m = [i for i, o in enumerate(care) if i not in used and r["csd"] and o["csd"] == r["csd"]
                 and names_agree(o["name"], r["name"])]
            if len(m) != 1:
                stats["odhf_left_out_no_xy"] += 1
                log.append(f"care: ODHF '{r['name']}' ({r['source_id']}) has no coordinates and "
                           f"{len(m)} OSM name matches in its census subdivision: left out")
                continue
            stats["odhf_placed_by_osm"] += 1
        sources = [{"source": SRC_ODHF, "source_id": r["source_id"], "data_date": "2020-04-20"}]
        xy = r["xy"]
        if m:
            o = care[m[0]]
            used.add(m[0])
            sources.append(o["sources"][0])
            xy = xy or o["xy"]
            stats["odhf_matched_osm"] += 1 if r["xy"] is not None else 0
        out.append({"name": r["name"], "category": "hospital", "detail": r["kind"], "xy": xy,
                    "outline": None, "sources": sources})
    for i, o in enumerate(care):
        if i not in used:
            out.append({k: o[k] for k in ("name", "category", "detail", "xy", "outline", "sources")})
    for a in out:
        srcs = {s["source"] for s in a["sources"]}
        if srcs == {SRC_ODHF}:
            a["verify"] = "ODHF (2020) only, verify"
        elif srcs == {SRC_OSM}:
            a["verify"] = "OpenStreetMap only, verify"
    stats["care_sites"] = len(out)
    return out, stats


def dedupe(feats: list[dict], log: list[str], radius_m: float = 300.0) -> list[dict]:
    """One asset per category and (case-insensitive) name within ``radius_m`` (OSM often maps a
    site as a node and an area)."""
    out: list[dict] = []
    grid: dict[tuple, list[dict]] = {}
    for f in feats:
        key = (f["category"], f["name"].lower())
        cell = (round(f["xy"][0] * 50), round(f["xy"][1] * 100))
        near = [g for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for g in grid.get((key, cell[0] + dx, cell[1] + dy), [])
                if dist_m(g["xy"], f["xy"]) <= radius_m]
        if near:
            k = near[0]
            k["sources"] += [s for s in f["sources"] if s not in k["sources"]]
            if k.get("outline") is None and f.get("outline") is not None:
                k["outline"] = f["outline"]
            log.append(f"dedupe: '{f['name']}' ({f['category']}) merged")
            continue
        grid.setdefault((key, *cell), []).append(f)
        out.append(f)
    return out


LICENCE_OF = {SRC_ODHF: LIC_CANADA, SRC_OSM: LIC_OSM}


def write_fgb(feats: list[dict], path: Path) -> None:
    import numpy as np
    import pyogrio
    import shapely

    if path.exists():
        path.unlink()
    pts = shapely.points(np.array([f["xy"] for f in feats], dtype=float))
    cols = {
        "name": [f["name"] for f in feats],
        "category": [f["category"] for f in feats],
        "detail": [f.get("detail") or "" for f in feats],
        "source": [f["sources"][0]["source"] for f in feats],
        "source_id": [f["sources"][0]["source_id"] for f in feats],
        "licence": ["; ".join(dict.fromkeys(LICENCE_OF[s["source"]] for s in f["sources"])) for f in feats],
        "sources": [json.dumps([{"source": s["source"], "source_id": s["source_id"]} for s in f["sources"]]) for f in feats],
        "verify": [f.get("verify") or "" for f in feats],
        "outline": [json.dumps(f["outline"], separators=(",", ":")) if f.get("outline") else "" for f in feats],
    }
    pyogrio.raw.write(str(path), geometry=shapely.to_wkb(pts), field_data=[np.array(v, dtype=object) for v in cols.values()],
                      fields=list(cols), driver="FlatGeobuf", geometry_type="Point", crs="EPSG:4326",
                      layer_options={"SPATIAL_INDEX": "YES"})


def build(out_dir: Path, cache: Path | None, offline: bool) -> dict:
    log: list[str] = []
    raw = cached(cache, "overpass_ab_assets.json", offline, lambda: overpass(overpass_query()))
    data = json.loads(raw)
    osm = osm_assets(data)
    odhf = odhf_alberta(cached(cache, "ODHF.zip", offline, lambda: _get(ODHF_URL)))
    care, care_stats = merge_care(odhf, osm, CsdLookup(CSD_PATH), log)
    feats = [f for f in care + [o for o in osm if o["category"] != "hospital"]
             if in_alberta_box(f["xy"])]
    n0 = len(feats)
    feats = dedupe(feats, log)
    order = ["eoc", "hospital", "fire_station", "police", "ems", "reception", "seniors", "school",
             "water", "power", "transit"]
    feats.sort(key=lambda f: (order.index(f["category"]), f["name"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / NAME
    write_fgb(feats, path)
    by: dict[str, int] = {}
    for f in feats:
        by[f["category"]] = by.get(f["category"], 0) + 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        "file": NAME, "bytes": path.stat().st_size, "sha256": digest,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": "scripts/build_ab_assets.py", "features": len(feats), "by_category": by,
        "attribution": ATTRIBUTION,
        "sources": {
            "osm": {"licence": LIC_OSM, "url": "https://www.openstreetmap.org/copyright",
                    "data_updated": (data.get("osm3s", {}).get("timestamp_osm_base") or "")[:10] or None,
                    "elements": len(data.get("elements", []))},
            "odhf": {"licence": LIC_CANADA, "url": ODHF_URL, "version": "1.1 (2020-04)",
                     "alberta_records_kept": len(odhf), **care_stats},
        },
        "deduplicated": n0 - len(feats),
        "verify_flags": sum(1 for f in feats if f.get("verify")),
        "personal_data": "none (no addresses, phones or e-mails kept)",
    }
    (out_dir / (NAME + ".json")).write_text(json.dumps(meta, indent=2) + "\n")
    (out_dir / (NAME + ".log")).write_text("\n".join(log) + "\n")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(a.out_dir, a.cache_dir, a.offline), indent=2))


if __name__ == "__main__":
    main()
