#!/usr/bin/env python3
"""Build frontend/public/edmonton/assets.geojson: critical assets for the Edmonton fuel grid.

Every feature comes from an open, citable source and carries a common schema:

    {name, category, source, source_id, licence, fetched_at, sources}
    (+ optional `detail`, and `verify` when a data-quality flag applies)

`source`/`source_id`/`licence` are the primary record; `sources` lists every record the feature
was built from, primary first ({source, source_id}, plus `data_date`, and `role: "position"`
for the City address point that positions a geocoded site).

Sources
-------
* City of Edmonton Open Data (data.edmonton.ca, Socrata SODA API),
  Open Government Licence - City of Edmonton:
    fire stations b4y7-zhnz, police stations e7aq-scxv, recreation centres nz3t-vyg3
    (facility_type "Recreation Centre": candidate reception centres), seniors centres
    zmac-3mxq, Edmonton Catholic schools (current) gfxq-u8uu, EPSB school locations
    996c-239n, LRT stations and stops fhxi-cnhe; Parcel Addresses ut27-nrpn (geocoding).
* Government of Alberta, Continuing Care Accommodation Standards compliance reporting
  (open.alberta.ca, monthly XLSX), Open Government Licence - Alberta: current continuing
  care homes, lodges, assisted living and hospices, geocoded with the City address points.
* Statistics Canada, Open Database of Healthcare Facilities (ODHF v1.1, open.canada.ca
  dataset 543fe07a-fd79-40e9-a829-ccd697526765), Open Government Licence - Canada:
  hospitals and nursing/residential care facilities (2020); records without coordinates are
  geocoded through the civic address of the matching Alberta site (or OSM addr:* tags).
* OpenStreetMap via the Overpass API, (c) OpenStreetMap contributors, ODbL 1.0: hospitals,
  nursing homes, assisted living and hospices; water and wastewater treatment plants, power
  plants, and power substations of 69 kV or more (traction and distribution-only substations
  below that are left out).
* No manual points. The City of Edmonton EOC is not included: no public source gives its
  location (searched 2026-10-08), and the owner decided to leave it out (docs/data-sources.md).

Care facilities from different sources are merged by name and distance (CARE_MATCH_M);
ODHF-only and OSM-only sites are flagged "verify". Assets of one category with the same name
within DEDUP_SAME_NAME_M are merged (dedupe_same_name), and every merge is logged.

Everything is clipped to the Edmonton fuel grid (data/Edmonton_FBP_FuelLayer_20251105_10m.tif,
WGS84 bounds below). Points stay points; OSM areas become their centroid, and the plant
polygons (few, large sites) keep their outline so "within 500 m" is measured from the site
edge.

Refresh (network needed; about a minute):

    python3 scripts/build_edmonton_assets.py
    # keep the raw responses, and re-run offline from them:
    python3 scripts/build_edmonton_assets.py --cache-dir /tmp/assets-cache
    python3 scripts/build_edmonton_assets.py --cache-dir /tmp/assets-cache --offline

Reproducibility: the raw responses can be saved with --cache-dir; the output records
`fetched_at` per feature and a `metadata` block with each source's data date, counts, match
rates and merges.
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
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
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

# Alberta continuing care homes (Ministry of Assisted Living and Social Services, monthly
# extract on open.alberta.ca; site name, civic address, postal code, type, units, visits)
AB_CC_PACKAGE = "continuing-care-accommodation-standards-compliance-reporting"
LIC_ALBERTA = "Open Government Licence - Alberta"
SRC_AB_CC = "Government of Alberta continuing care list"
# Sites kept: every type except group homes (small residences, often private houses), with 10
# or more units, inspected within this many days of the extract (older: possibly closed)
AB_CC_MIN_UNITS = 10
AB_CC_RECENT_DAYS = 730
# Municipalities inside the grid outside Edmonton: their sites are not geocoded (City address
# points cover Edmonton only) but confirm OSM records of the same name as current
AB_CC_NEARBY = {"ST. ALBERT", "ST ALBERT", "SHERWOOD PARK", "BEAUMONT", "ARDROSSAN"}

SRC_CITY_ADDR = "City of Edmonton Open Data, Parcel Addresses (ut27-nrpn)"

# Licence of each source (the `sources` entries of a feature name the source only)
LICENCE = {
    SRC_CITY: LIC_CITY,
    SRC_CITY_ADDR: LIC_CITY,
    SRC_ODHF: LIC_CANADA,
    SRC_OSM: LIC_OSM,
    SRC_AB_CC: LIC_ALBERTA,
    SRC_MANUAL: "Public information (no dataset)",
}

# Care facilities from different sources are one site when within this distance and their
# names agree (a shared distinctive word); any asset with the same (equivalent) name in the
# same category within DEDUP_SAME_NAME_M is merged (e.g. "RCMP K Division" and "Royal Canadian
# Mounted Police" at two neighbouring addresses).
CARE_MATCH_M = 150
DEDUP_SAME_NAME_M = 300

# ODHF "Hospitals" records that are private day-surgery or outpatient clinics (CIHI codes them
# "active acute hospital"): not inpatient or residential care, so not in this layer
CLINIC_WORDS = ("surgery", "surgical", "laser", "dental", "oral", "dermatolog", "cosmetic", "lasik",
                "professional corporation", "clinic", "eye q", "health options", "dermasurgery")

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
            elif w in {"ii", "iii", "iv"}:
                words.append(w.upper())
            elif len(w) > 2 and w.startswith("mc"):
                words.append("Mc" + w[2:].capitalize())
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
    """The common schema. `sources` lists every source of the feature (the first is `source`)."""
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
    p["sources"] = [{"source": source, "source_id": source_id}]
    return p


# ── Names ────────────────────────────────────────────────────────────────────

# Words that say what kind of place it is, not which one (so they never make two names agree)
GENERIC = {
    "the", "and", "of", "at", "for", "by", "on", "de", "la", "in", "a", "inc", "ltd", "ltd.", "corp",
    "care", "centre", "center", "continuing", "long", "term", "home", "homes", "lodge", "society",
    "foundation", "place", "manor", "house", "residence", "retirement", "seniors", "senior", "living",
    "supportive", "assisted", "nursing", "health", "hospital", "community", "campus", "village",
    "edmonton", "alberta", "covenant", "capitalcare", "capital", "extendicare", "revera", "agecare",
    "good", "samaritan", "shepherd's", "shepherds", "park", "partnership", "seniors'", "operating",
    "gp", "national", "axr", "o/a", "canada", "auxiliary", "general", "station", "school", "services",
    "police", "fire", "division", "community", "plant", "treatment", "substation", "recreation",
    "dr", "dr.", "st", "st.", "saint", "mount", "sister", "sisters", "charity", "providence",
    "villa", "gardens", "garden", "court", "estates", "terrace", "heights", "pavilion", "hall",
    "residences", "suites",
}


def name_tokens(name: str) -> list[str]:
    s = name.lower().replace("’", "'").replace("—", " ").replace("–", " ")
    for ch in ",.()/-&:;\"":
        s = s.replace(ch, " ")
    return [t for t in s.split() if t]


def distinctive(name: str) -> set[str]:
    """Distinctive words, plus adjacent pairs written together ('Mill Woods' ~ 'Millwoods')."""
    toks = name_tokens(name)
    out = {t for t in toks if t not in GENERIC and len(t) > 2}
    out |= {a + b for a, b in zip(toks, toks[1:]) if a + b not in GENERIC and (a not in GENERIC or b not in GENERIC)}
    return out


def same_words(a: str, b: str) -> bool:
    return name_tokens(a) == name_tokens(b)


def initials(name: str) -> str:
    return "".join(t[0] for t in name_tokens(name) if t not in {"of", "the", "and", "for", "de", "la"})


def names_agree(a: str, b: str) -> bool:
    """Two names of the same place: equal, an acronym of the other, or a shared distinctive word."""
    ta, tb = name_tokens(a), name_tokens(b)
    if ta == tb:
        return True
    # "RCMP K Division" vs "Royal Canadian Mounted Police": an all-capitals word spelling the
    # other name's initials
    for x, y in ((a, b), (b, a)):
        for w in x.split():
            if len(w) >= 3 and w.isupper() and w.isalpha() and w.lower() == initials(y)[: len(w)]:
                return True
    if distinctive(a) & distinctive(b):
        return True
    # Only generic words ('Edmonton General Continuing Care' / '... Care Centre'): one name's
    # words all in the other's
    sa, sb = set(ta), set(tb)
    return min(len(sa), len(sb)) >= 3 and (sa <= sb or sb <= sa)


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
    sid = f"data.edmonton.ca/{dataset}#{row.get(key, '')}"
    # Police stations: one name can be listed at two addresses
    return f"{sid}@{row['address']}" if dataset == "e7aq-scxv" and row.get("address") else sid


def note_merge(p: dict, name: str, source_id: str, source: str) -> None:
    """Record a merged duplicate on the kept feature: its source, and its name when different."""
    if source_id not in [x["source_id"] for x in p["sources"]]:
        p["sources"].append({"source": source, "source_id": source_id})
    if not same_words(name, p["name"]) and name not in p.get("aka", []):
        p.setdefault("aka", []).append(name)


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
            # One point per site: the same category within 30 m (platforms of one LRT station);
            # same-name duplicates further apart are merged later (dedupe_same_name)
            same = next((f for f in out if f["properties"]["category"] == category
                         and dist_m(tuple(f["geometry"]["coordinates"]), (lng, lat)) < 30), None)
            if same:
                note_merge(same["properties"], name, city_source_id(ds, row), SRC_CITY)
                continue
            out.append(point_feature(lng, lat, props(name, category, SRC_CITY, city_source_id(ds, row), LIC_CITY, now, detail)))
            meta[ds]["count"] += 1
    return out


# ── StatCan ODHF ─────────────────────────────────────────────────────────────


def is_clinic(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in CLINIC_WORDS)


def fetch_odhf(cache: Path | None, offline: bool, meta: dict) -> tuple[list[dict], list[dict]]:
    """ODHF hospitals and nursing/residential care in the grid: (with coordinates, one record per
    site; Edmonton records without coordinates). Records are plain dicts, merged later."""
    raw = cached(cache, "ODHF.zip", offline, lambda: _get(ODHF_URL))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("cp1252", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    kept: list[dict] = []
    no_coords: list[dict] = []
    for r in rows:
        if r.get("odhf_facility_type") not in ODHF_TYPES:
            continue
        nm = title(r["facility_name"].replace("’", "'")).replace("Covenant Health ", "")
        try:
            lat, lng = float(r["latitude"]), float(r["longitude"])
        except ValueError:
            if "edmonton" in (r.get("city") or "").lower():
                no_coords.append({"name": nm, "id": r["index"], "type": r["odhf_facility_type"],
                                  "src_type": r["source_facility_type"], "postal": (r.get("postal_code") or "").replace(" ", "").upper()})
            continue
        if not in_bounds(lng, lat):
            continue
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
        out.append({"name": name, "aliases": others, "xy": k["xy"], "detail": detail,
                    "source": SRC_ODHF, "source_id": "odhf#" + "+".join(k["ids"]), "licence": LIC_CANADA})
    meta["odhf"] = {
        "name": "Open Database of Healthcare Facilities (ODHF) v1.1",
        "source": SRC_ODHF,
        "url": "https://open.canada.ca/data/en/dataset/543fe07a-fd79-40e9-a829-ccd697526765",
        "licence": LIC_CANADA,
        "data_updated": "2020-04-20",
        "records_with_coordinates": len(out),
        "edmonton_records_without_coordinates": len(no_coords),
    }
    return out, no_coords


# ── Alberta continuing care homes ────────────────────────────────────────────


def _xlsx_rows(raw: bytes) -> list[list]:
    """Rows of the first sheet of an .xlsx (standard library only): strings, numbers, None."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        rows: list[list] = []
        for _, row in ET.iterparse(io.BytesIO(z.read(sheet))):
            if row.tag != f"{ns}row":
                continue
            vals: dict[int, object] = {}
            for c in row.iter(f"{ns}c"):
                ref = c.get("r", "")
                col = 0
                for ch in ref:
                    if ch.isalpha():
                        col = col * 26 + ord(ch.upper()) - 64
                v = c.find(f"{ns}v")
                t = c.get("t")
                if t == "s" and v is not None:
                    vals[col - 1] = shared[int(v.text)]
                elif t == "inlineStr":
                    vals[col - 1] = "".join(x.text or "" for x in c.iter(f"{ns}t"))
                elif v is not None and v.text is not None:
                    try:
                        vals[col - 1] = float(v.text)
                    except ValueError:
                        vals[col - 1] = v.text
            rows.append([vals.get(i) for i in range(max(vals) + 1)] if vals else [])
            row.clear()
    return rows


def _excel_date(v: object) -> date | None:
    if isinstance(v, float):
        return date(1899, 12, 30) + timedelta(days=int(v))
    if isinstance(v, str) and len(v) >= 10:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def fetch_ab_continuing_care(cache: Path | None, offline: bool, meta: dict) -> tuple[list[dict], date | None]:
    """Edmonton continuing care sites from the current Alberta extract, one record per site."""
    pkg = json.loads(cached(cache, "ab_cc_package.json", offline,
                            lambda: _get(f"https://open.alberta.ca/api/3/action/package_show?id={AB_CC_PACKAGE}")))["result"]
    res = [r for r in pkg["resources"] if (r.get("format") or "").upper() == "XLSX" and "column" not in r["name"].lower()]
    res.sort(key=lambda r: r.get("last_modified") or r.get("created") or "", reverse=True)
    resource = res[0]
    raw = cached(cache, "ab_cc.xlsx", offline, lambda: _get(resource["url"]))
    rows = _xlsx_rows(raw)
    hdr = [str(h or "").strip() for h in rows[0]]
    ix = {h: i for i, h in enumerate(hdr)}

    def col(r: list, h: str):
        i = ix.get(h)
        return r[i] if i is not None and i < len(r) else None

    sites: dict[tuple[str, str, str], dict] = {}
    extract: date | None = None
    for r in rows[1:]:
        if not r:
            continue
        d = _excel_date(col(r, "Visit Date"))
        if d and (extract is None or d > extract):
            extract = d
        city = str(col(r, "City") or "").strip().upper()
        if city != "EDMONTON" and city not in AB_CC_NEARBY:
            continue
        name = clean_site_name(" ".join(str(col(r, "Site Name") or "").split()))
        addr = " ".join(str(col(r, "Address 1") or "").split())
        # A building name in Address 2 tells one operator's sites apart ('... - Wyser Manor')
        a2 = " ".join(str(col(r, "Address  2") or col(r, "Address 2") or "").strip("() ").split())
        if a2 and not a2[:1].isdigit() and a2.upper() not in name.upper() and not a2.upper().startswith(("SUITE", "UNIT", "BOX", "#")):
            name = f"{name} - {a2.upper() if name.isupper() else a2}"
        if not name or not addr:
            continue
        s = sites.setdefault((name.upper(), addr.upper(), city), {
            "name": name, "address": addr, "city": city, "postal": str(col(r, "Postal Code") or "").replace(" ", "").upper(),
            "types": set(), "units": 0.0, "last": None,
        })
        typ, sub = str(col(r, "Accommodation Type") or ""), str(col(r, "Accommodation Subtype") or "")
        s["types"].add(f"{typ} / {sub}" if sub and sub not in (".", "NA", "None") else typ)
        try:
            s["units"] = max(s["units"], float(col(r, "Units") or 0))
        except (TypeError, ValueError):
            pass
        if d and (s["last"] is None or d > s["last"]):
            s["last"] = d
    out: list[dict] = []
    skipped: dict = {"group_home": 0, "under_10_units": 0, "no_recent_visit": []}
    for s in sites.values():
        edm = s["city"] == "EDMONTON"
        if all("Group Home" in t for t in s["types"]):
            skipped["group_home"] += edm
            continue
        if s["units"] < AB_CC_MIN_UNITS:
            skipped["under_10_units"] += edm
            continue
        if not s["last"] or (extract and (extract - s["last"]).days > AB_CC_RECENT_DAYS):
            if edm:
                skipped["no_recent_visit"].append(f"{s['name']} ({s['address']}; last visit {s['last']})")
            continue
        s["kind"] = care_kind(s["types"])
        out.append(s)
    meta["ab_cc"] = {
        "name": pkg.get("title"),
        "source": SRC_AB_CC,
        "url": f"https://open.alberta.ca/dataset/{AB_CC_PACKAGE}",
        "resource": resource.get("name"),
        "licence": LIC_ALBERTA,
        "data_updated": (resource.get("last_modified") or resource.get("created") or "")[:10] or None,
        "latest_visit_in_extract": extract.isoformat() if extract else None,
        "edmonton_sites": sum(1 for k in sites if k[2] == "EDMONTON"),
        "edmonton_sites_kept": sum(1 for s in out if s["city"] == "EDMONTON"),
        "nearby_sites_kept_for_name_checks": sum(1 for s in out if s["city"] != "EDMONTON"),
        "left_out": {"group_homes": skipped["group_home"], "under_10_units": skipped["under_10_units"],
                     "no_visit_within_2_years": skipped["no_recent_visit"]},
    }
    return out, extract


def clean_site_name(name: str) -> str:
    """'9385452 CANADA INC., O/A CHARTWELL WESCOTT ...' -> 'CHARTWELL WESCOTT ...'."""
    u = name.upper()
    for sep in (" O/A ", ", O/A "):
        i = u.find(sep)
        if i >= 0:
            return name[i + len(sep):].strip()
    return name


def care_kind(types: set[str]) -> str:
    t = " ".join(sorted(types))
    if "Type A" in t or "Long Term Care" in t:
        return "Continuing care home type A (long-term care)"
    if "Hospice" in t:
        return "Hospice"
    if "Type B" in t:
        return "Continuing care home type B (designated supportive living)"
    if "Lodge" in t:
        return "Seniors lodge (supportive living)"
    return "Supportive living (assisted living)"


# ── Geocoding: City of Edmonton Parcel Addresses ─────────────────────────────

STREET_TYPES = {
    "st": "STREET", "st.": "STREET", "street": "STREET", "ave": "AVENUE", "av": "AVENUE", "ave.": "AVENUE",
    "avenue": "AVENUE", "rd": "ROAD", "road": "ROAD", "dr": "DRIVE", "drive": "DRIVE", "blvd": "BOULEVARD",
    "boulevard": "BOULEVARD", "cres": "CRESCENT", "crescent": "CRESCENT", "ct": "COURT", "court": "COURT",
    "pl": "PLACE", "place": "PLACE", "trail": "TRAIL", "tr": "TRAIL", "trl": "TRAIL", "way": "WAY",
    "ln": "LANE", "lane": "LANE", "cl": "CLOSE", "close": "CLOSE", "gate": "GATE", "link": "LINK",
    "wynd": "WYND", "point": "POINT", "pt": "POINT", "sq": "SQUARE", "square": "SQUARE",
    "hts": "HEIGHTS", "heights": "HEIGHTS", "pkwy": "PARKWAY", "parkway": "PARKWAY", "landing": "LANDING",
}
QUADRANTS = {"NW", "SW", "NE", "SE"}


def parse_address(addr: str) -> tuple[str, str] | None:
    """'9649 - 71 Ave NW' -> ('9649', '71 AVENUE NW'); 'Providence Centre, 3005 119 Street NW'
    -> ('3005', '119 STREET NW'). Edmonton addresses without a quadrant are NW."""
    a = addr.replace(",", " , ").replace(" - ", " ").replace("N.W.", "NW").replace("S.W.", "SW")
    for long, short in (("north-west", "NW"), ("northwest", "NW"), ("south-west", "SW"), ("southwest", "SW")):
        i = a.lower().find(long)
        if i >= 0:
            a = a[:i] + short + a[i + len(long):]
    toks = a.split()
    start = next((i for i, t in enumerate(toks) if t[:1].isdigit()), None)
    if start is None:
        return None
    toks = [t for t in toks[start:] if t != ","]
    if "," in a[a.find(toks[0]):]:
        toks = a[a.find(toks[0]):].split(",")[0].split()
    house = toks[0]
    street = []
    for t in toks[1:]:
        u = t.upper().strip(".")
        street.append(STREET_TYPES.get(t.lower(), u))
    if not street:
        return None
    if street[-1] not in QUADRANTS:
        street.append("NW")
    return house.upper(), " ".join(street)


def fetch_city_addresses(cache: Path | None, offline: bool, houses: list[str]) -> dict[tuple[str, str], tuple[float, float]]:
    """Official address points (parcels and the suites of multi-unit buildings, which can carry a
    civic address of their own) for the given house numbers, averaged per civic address:
    {(house, street): (lng, lat)}."""
    def fetch() -> bytes:
        rows: list[dict] = []
        hs = sorted(set(houses))
        for i in range(0, len(hs), 80):
            inlist = ",".join("'" + h.replace("'", "") + "'" for h in hs[i:i + 80])
            q = urllib.parse.urlencode({
                "$select": "house_number,street_name,avg(latitude) as lat,avg(longitude) as lng,count(*) as n",
                "$where": f"house_number in({inlist})",
                "$group": "house_number,street_name",
                "$limit": "50000",
            })
            rows += json.loads(_get(f"https://data.edmonton.ca/resource/ut27-nrpn.json?{q}"))
        return json.dumps(rows, separators=(",", ":")).encode()
    rows = json.loads(cached(cache, "city_address_points.json", offline, fetch))
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for r in rows:
        try:
            out[(r["house_number"].upper(), " ".join(r["street_name"].upper().split()))] = (float(r["lng"]), float(r["lat"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def geocode(index: dict[tuple[str, str], tuple[float, float]], addr: str) -> tuple[tuple[float, float] | None, str]:
    """(position, how) from a civic address: exact house number and street, then the house
    number without a unit letter ('6620C' -> '6620'), then the other quadrant."""
    p = parse_address(addr)
    if not p:
        return None, "no civic number in the address"
    house, street = p
    houses = [house] + ([house.rstrip("ABCDEFGH")] if house[-1:].isalpha() else [])
    for h in houses:
        if (h, street) in index:
            return index[(h, street)], "exact" if h == house else f"house number {h}"
    # Spacing ('Millwoods Road' / 'MILL WOODS ROAD') and the quadrant may differ
    base = street.rsplit(" ", 1)[0].replace(" ", "")
    for h in houses:
        hits = [v for (hh, s), v in index.items() if hh == h and s.rsplit(" ", 1)[0].replace(" ", "") == base]
        if len(hits) == 1:
            return hits[0], "street spelling or quadrant differs"
    return None, f"no City parcel address {house} {street}"


def wanted_houses(addrs: list[str]) -> list[str]:
    out = []
    for a in addrs:
        p = parse_address(a)
        if p:
            out.append(p[0])
            if p[0][-1:].isalpha():
                out.append(p[0].rstrip("ABCDEFGH"))
    return out


# ── OpenStreetMap care facilities ────────────────────────────────────────────


def overpass_care_query() -> str:
    b = f"({BOUNDS['south']},{BOUNDS['west']},{BOUNDS['north']},{BOUNDS['east']})"
    return f"""[out:json][timeout:120];
(
  nwr["amenity"="hospital"]{b};
  nwr["healthcare"="hospital"]{b};
  nwr["amenity"="nursing_home"]{b};
  nwr["healthcare"="nursing_home"]{b};
  nwr["social_facility"~"^(nursing_home|assisted_living)$"]{b};
  nwr["healthcare"="hospice"]{b};
);
out center tags;"""


def fetch_osm_care(cache: Path | None, offline: bool, meta: dict) -> list[dict]:
    raw = cached(cache, "overpass_care.json", offline, lambda: overpass(overpass_care_query()))
    data = json.loads(raw)
    out = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = t.get("name")
        xy = (el["lon"], el["lat"]) if el["type"] == "node" else ((el["center"]["lon"], el["center"]["lat"]) if el.get("center") else None)
        if not name or not xy or not in_bounds(*xy):
            continue
        if t.get("amenity") == "hospital" or t.get("healthcare") == "hospital":
            kind = "Hospital"
        elif t.get("healthcare") == "hospice":
            kind = "Hospice"
        elif t.get("social_facility") == "assisted_living":
            kind = "Assisted living"
        else:
            kind = "Nursing home"
        addr = f"{t['addr:housenumber']} {t['addr:street']}" if t.get("addr:housenumber") and t.get("addr:street") else None
        out.append({"name": name, "xy": xy, "kind": kind, "address": addr, "source": SRC_OSM,
                    "source_id": f"osm:{el['type']}/{el['id']}", "licence": LIC_OSM})
    meta["osm_care"] = {
        "name": "OpenStreetMap (Overpass API): hospitals, nursing homes, assisted living, hospices",
        "source": SRC_OSM,
        "url": "https://www.openstreetmap.org/copyright",
        "licence": LIC_OSM,
        "data_updated": (data.get("osm3s", {}).get("timestamp_osm_base") or "")[:10] or None,
        "named_records": len(out),
    }
    return out


# ── Hospitals and care facilities: all sources merged ────────────────────────


def build_care(cache: Path | None, offline: bool, now: str, meta: dict, log: list[str]) -> list[dict]:
    """One feature per site from the current Alberta list (geocoded with City address points),
    ODHF (2020) and OSM, every source recorded per feature (flags: care_flags)."""
    odhf, odhf_nocoord = fetch_odhf(cache, offline, meta)
    ab_all, _ = fetch_ab_continuing_care(cache, offline, meta)
    ab = [s for s in ab_all if s["city"] == "EDMONTON"]
    ab_near = [s for s in ab_all if s["city"] != "EDMONTON"]
    osm = fetch_osm_care(cache, offline, meta)

    # ODHF records without coordinates have only a postal code: take the civic address from the
    # Alberta list (same postal code and agreeing names, else a unique name match city-wide) or
    # from OSM addr:* tags, then geocode it with the City address points like the Alberta sites
    for r in odhf_nocoord:
        if r["type"] == "Hospitals" and is_clinic(r["name"]):
            r["outcome"] = "left out: private day-surgery or outpatient clinic (CIHI 'active acute hospital'), not inpatient or residential care"
            continue
        cand = [s for s in ab if s["postal"] == r["postal"] and names_agree(s["name"], r["name"])]
        if not cand:
            cand = [s for s in ab if names_agree(s["name"], r["name"])]
            cand = cand if len(cand) == 1 else []
        if cand:
            r["address"], r["address_from"] = cand[0]["address"], SRC_AB_CC
            continue
        o = [x for x in osm if x["address"] and names_agree(x["name"], r["name"])]
        if len(o) == 1:
            r["address"], r["address_from"] = o[0]["address"], "OpenStreetMap addr:* tags"
            continue
        r["outcome"] = "unmatched: ODHF has no civic address (postal code only), and neither the Alberta list nor OSM has a site of that name with an address; left out"

    index = fetch_city_addresses(cache, offline, wanted_houses([s["address"] for s in ab] + [r["address"] for r in odhf_nocoord if r.get("address")]))
    ab_missed = []
    for s in ab:
        s["xy"], s["how"] = geocode(index, s["address"])
        if not s["xy"]:
            ab_missed.append(f"{s['name']} ({s['address']}): {s['how']}")
            log.append(f"care: Alberta site not geocoded, left out: {ab_missed[-1]}")
    meta["ab_cc"]["edmonton_sites_not_geocoded"] = ab_missed
    report = []
    for r in odhf_nocoord:
        if r.get("address") and "outcome" not in r:
            xy, how = geocode(index, r["address"])
            if xy:
                r["xy"] = xy
                r["outcome"] = f"geocoded: {r['address']} (address from {r['address_from']}; City address point, {how})"
            else:
                r["outcome"] = f"unmatched: civic address {r['address']} from {r['address_from']}, but {how}"
        report.append({"odhf_id": r["id"], "name": r["name"], "type": r["type"], "outcome": r["outcome"]})
    in_scope = [r for r in odhf_nocoord if not r["outcome"].startswith("left out")]
    geocoded = [r for r in in_scope if r.get("xy")]
    meta["odhf"]["no_coordinates_geocoding"] = {
        "records": len(odhf_nocoord),
        "left_out_clinics": len(odhf_nocoord) - len(in_scope),
        "in_scope": len(in_scope),
        "geocoded": len(geocoded),
        "match_rate": f"{len(geocoded)}/{len(in_scope)}" if in_scope else "n/a",
        "records_detail": report,
    }

    # Merge, most authoritative first: the current Alberta list, then ODHF (official, 2020),
    # then OSM. A record joins a site within CARE_MATCH_M whose names agree; two Alberta records
    # (or two ODHF sites) never merge, the source lists them as separate sites.
    sites: list[dict] = []

    def add(name: str, xy: tuple[float, float], detail: str | None, srcs: list[dict], aliases: list[str] = (), create: bool = True) -> None:
        if not in_bounds(*xy):
            return
        own = srcs[0]["source"]
        cands = []
        for s in sites:
            if own in (SRC_AB_CC, SRC_ODHF) and own in [x["source"] for x in s["sources"]]:
                continue
            d = dist_m(s["xy"], xy)
            if d < CARE_MATCH_M and any(names_agree(n, name) for n in [s["name"], *s["aliases"]]):
                # The best match when several agree: most distinctive words in common, then nearest
                common = max(len(distinctive(n) & distinctive(name)) + 10 * same_words(n, name) for n in [s["name"], *s["aliases"]])
                cands.append((-common, d, len(cands), s))
        if cands:
            s = min(cands)[3]
            for src in srcs:
                if src["source_id"] not in [x["source_id"] for x in s["sources"]]:
                    s["sources"].append(src)
            log.append(f"care: merged {own} '{name}' into '{s['name']}' ({dist_m(s['xy'], xy):.0f} m)")
            return
        if create:
            sites.append({"name": name, "xy": xy, "detail": detail, "sources": list(srcs), "aliases": list(aliases)})
        else:
            log.append(f"care: {own} '{name}' left out (clinic, not inpatient or residential care)")

    def position_src(addr: str) -> dict:
        return {"source": SRC_CITY_ADDR, "source_id": f"data.edmonton.ca/ut27-nrpn#{addr}", "role": "position"}

    for s in ab:
        if s["xy"]:
            add(title(s["name"]), s["xy"], f"{s['kind']}, {s['units']:.0f} units; {s['address']}", [
                {"source": SRC_AB_CC, "source_id": f"ab-cc#{s['name']}@{s['address']}", "data_date": s["last"].isoformat()},
                position_src(s["address"]),
            ])
    for r in odhf:
        add(r["name"], r["xy"], r["detail"], [{"source": SRC_ODHF, "source_id": r["source_id"], "data_date": "2020-04-20"}], r["aliases"])
    for r in geocoded:
        add(r["name"], r["xy"], f"{r['type']} ({r['src_type']})", [
            {"source": SRC_ODHF, "source_id": f"odhf#{r['id']}", "data_date": "2020-04-20"},
            position_src(r["address"]),
        ])
    for o in osm:
        add(o["name"], o["xy"], o["kind"], [{"source": SRC_OSM, "source_id": o["source_id"]}], create=not is_clinic(o["name"]))

    # OSM sites in the municipalities around Edmonton: the Alberta list confirms them by name
    for s in sites:
        if [x["source"] for x in s["sources"]] != [SRC_OSM] * len(s["sources"]):
            continue
        # Strict here (no position to compare): the same words, or two distinctive words in
        # common; hospitals are not in the continuing care list
        if s["detail"] == "Hospital":
            continue
        hit = [a for a in ab_near if same_words(a["name"], s["name"]) or len(distinctive(a["name"]) & distinctive(s["name"])) >= 2]
        if len(hit) == 1:
            a = hit[0]
            s["sources"].append({"source": SRC_AB_CC, "source_id": f"ab-cc#{a['name']}@{a['address']}, {a['city'].title()}", "data_date": a["last"].isoformat()})
            s["detail"] = f"{a['kind']}, {a['units']:.0f} units; {a['address']}, {a['city'].title()}"
            log.append(f"care: OSM '{s['name']}' confirmed by the Alberta list ({a['name']}, {a['city'].title()})")

    out = []
    for s in sites:
        first = s["sources"][0]
        p = props(s["name"], "hospital", first["source"], first["source_id"], LICENCE[first["source"]], now, s["detail"])
        p["sources"] = s["sources"]
        out.append(point_feature(s["xy"][0], s["xy"][1], p))
    meta["care_merged"] = {"features": len(out)}
    return out


def care_flags(feats: list[dict], meta: dict) -> None:
    """Flag hospitals and care facilities no current official source confirms."""
    by_first: dict[str, int] = {}
    flagged = {"possibly_closed": [], "osm_only": []}
    for f in feats:
        p = f["properties"]
        if p["category"] != "hospital":
            continue
        by_first[p["source"]] = by_first.get(p["source"], 0) + 1
        srcs = {x["source"] for x in p["sources"]}
        if srcs <= {SRC_ODHF, SRC_CITY_ADDR}:
            p["verify"] = "Possibly closed, verify: in ODHF (2020) only, in no current source"
            flagged["possibly_closed"].append(p["name"])
        elif srcs == {SRC_OSM}:
            p["verify"] = "OpenStreetMap only: in no official list, verify"
            flagged["osm_only"].append(p["name"])
    meta["care_merged"].update({"features": sum(by_first.values()), "by_primary_source": by_first,
                                "flagged_possibly_closed": flagged["possibly_closed"], "flagged_osm_only": flagged["osm_only"]})


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


# ── De-duplication ───────────────────────────────────────────────────────────


def same_name(a: str, b: str) -> bool:
    """The same name: equal words (case and punctuation aside), or one is an acronym of the other
    ('RCMP K Division' / 'Royal Canadian Mounted Police')."""
    if name_tokens(a) == name_tokens(b):
        return True
    for x, y in ((a, b), (b, a)):
        for w in x.split():
            if len(w) >= 3 and w.isupper() and w.isalpha() and w.lower() == initials(y)[: len(w)]:
                return True
    return False


def anchor_of(f: dict) -> tuple[float, float]:
    p = f["properties"]
    if p.get("anchor"):
        return tuple(p["anchor"])
    return tuple(f["geometry"]["coordinates"])


def dedupe_same_name(feats: list[dict], log: list[str], radius_m: float = DEDUP_SAME_NAME_M) -> list[dict]:
    """Merge assets of one category with the same name within `radius_m` (the first kept, the
    others' sources added to its `sources`). Every merge is logged."""
    out: list[dict] = []
    for f in feats:
        p = f["properties"]
        xy = anchor_of(f)
        keep = next((g for g in out if g["properties"]["category"] == p["category"]
                     and dist_m(anchor_of(g), xy) <= radius_m and same_name(g["properties"]["name"], p["name"])), None)
        if keep is None:
            out.append(f)
            continue
        kp = keep["properties"]
        for s in p.get("sources", []):
            if s["source_id"] not in [x["source_id"] for x in kp["sources"]]:
                kp["sources"].append(s)
        for n in [p["name"], *p.get("aka", [])]:
            if not same_words(n, kp["name"]) and n not in kp.get("aka", []):
                kp.setdefault("aka", []).append(n)
        log.append(f"dedupe: '{p['name']}' ({p['source_id']}) merged into '{kp['name']}' ({kp['source_id']}), "
                   f"{dist_m(anchor_of(keep), xy):.0f} m apart")
    return out


# ── Main ─────────────────────────────────────────────────────────────────────


def build(cache: Path | None, offline: bool, log: list[str] | None = None) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta: dict = {}
    log = log if log is not None else []
    feats = build_care(cache, offline, now, meta, log) + fetch_city(cache, offline, now, meta) + fetch_osm(cache, offline, now, meta)
    n0 = len(feats)
    feats = dedupe_same_name(feats, log)
    meta["dedupe_same_name"] = {"radius_m": DEDUP_SAME_NAME_M, "merged": n0 - len(feats),
                                "log": [x for x in log if x.startswith("dedupe:")]}
    care_flags(feats, meta)
    for f in feats:
        p = f["properties"]
        aka = p.pop("aka", None)
        if aka:
            p["detail"] = (p.get("detail") or "") + ("; " if p.get("detail") else "") + "also listed as: " + ", ".join(aka)
    order = ["eoc", "hospital", "fire_station", "police", "reception", "seniors", "school", "water", "power", "transit"]
    feats.sort(key=lambda f: (order.index(f["properties"]["category"]), f["properties"]["name"]))
    return {
        "type": "FeatureCollection",
        "metadata": {
            "built": now,
            "builder": "scripts/build_edmonton_assets.py",
            "bounds_wgs84": BOUNDS,
            "attribution": "City of Edmonton Open Data (Open Government Licence - City of Edmonton); "
            "Government of Alberta (Open Government Licence - Alberta); "
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
    log: list[str] = []
    fc = build(a.cache_dir, a.offline, log)
    for line in log:
        print(line, file=sys.stderr)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    by: dict[str, int] = {}
    for f in fc["features"]:
        by[f["properties"]["category"]] = by.get(f["properties"]["category"], 0) + 1
    print(f"wrote {a.out} ({a.out.stat().st_size / 1024:.0f} KB): {len(fc['features'])} features {by}")


if __name__ == "__main__":
    main()
