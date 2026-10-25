"""Seal the 2026 US held-out fire block (docs/SEALED_2026.md sections 1-4; backlog C4).

Purpose: download the NIFC WFIGS 2026 year-to-date interagency perimeters (wildfires >= 5,000 ha), apply
the frozen eligibility rules of SEALED_2026 section 2 (attributes and geometry only; no hotspots, weather,
growth or model output), order every eligible fire with the frozen seed, split it into the `confirm` and
`scorecard` subsets, and write the sealed list with its sha256.

Inputs: the WFIGS layer below (open, no key). Outputs (outside the repository, default
~/dev/wildfire/validation-data/sealed2026, override with FIRESIM_SEALED2026_DIR):
  raw/wfigs_ytd_2026_ge5000ha.geojson    full query result, every state (pool snapshot)
  us/sealed_us_2026.csv                   the sealed list (its sha256 is the seal)
  us/sealed_us_2026_perimeters.geojson    the sealed fires' perimeters as downloaded
  us/eligibility_log.csv                  pool records removed by a rule, with the rule
  us/draw_summary.json                    counts, download time, hashes (no fire names)
Prints counts and hashes only, never fire names. Refuses to overwrite an existing seal.

Usage: python3 -I scripts/validation/sealed2026/draw.py [--scan-root DIR ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

LAYER = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
         "WFIGS_Interagency_Perimeters_YearToDate/FeatureServer/0")
ITEM = "7c81ab78d8464e5c9771e49b64e834e9"
WHERE = "attr_IncidentTypeCategory='WF' AND poly_GISAcres>=12355.3"
HA_AC = 12355.3                     # 5,000 ha in acres (PREREG_LR8 pool threshold)
WUS = {"AZ", "CA", "CO", "ID", "MT", "NV", "NM", "OR", "UT", "WA", "WY"}
S_SEAL_US = 2026102401
END_LAG = pd.Timedelta(days=7)      # containment/control/out at least 7 d before download (ERA5 latency)
START_TOL_M = 2000.0                # initial point within 2 km of the final perimeter
OVERLAP = 0.5                       # duplicate shapes: overlap > 50 % of the smaller
SCAN_EXT = {".md", ".txt", ".csv", ".json", ".geojson", ".py", ".tsv", ".yaml", ".yml"}
SCAN_MAX = 50 * 1024 * 1024
HOME = Path.home() / "dev/wildfire"
OUT = Path(os.environ.get("FIRESIM_SEALED2026_DIR", HOME / "validation-data/sealed2026"))
REPO = Path(__file__).resolve().parents[3]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def http(url: str, tries: int = 6) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                return r.read()
        except Exception:  # noqa: BLE001 - retry any transport error
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))
    raise RuntimeError("unreachable")


def download(raw: Path) -> tuple[dict, str]:
    """One paged query of the year-to-date layer; returns the merged GeoJSON and the UTC download time."""
    t0 = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    feats, off = [], 0
    while True:
        q = {"where": WHERE, "outFields": "*", "outSR": "4326", "f": "geojson", "orderByFields": "OBJECTID",
             "resultOffset": off, "resultRecordCount": 500}
        page = json.loads(http(LAYER + "/query?" + urllib.parse.urlencode(q)))
        if "error" in page:
            raise RuntimeError(page["error"])
        fs = page.get("features", [])
        feats += fs
        if not fs or not (page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")):
            break
        off += len(fs)
    gj = {"type": "FeatureCollection", "features": feats,
          "_query": {"layer": LAYER, "item": ITEM, "where": WHERE, "downloaded_utc": t0}}
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(gj, separators=(",", ":"), sort_keys=True))
    return gj, t0


def ms(v) -> pd.Timestamp:
    return pd.NaT if v is None or (isinstance(v, float) and math.isnan(v)) else pd.Timestamp(int(v), unit="ms", tz="UTC")


def norm_id(s) -> str:
    return "" if not isinstance(s, str) else s.strip().strip("{}").lower()


def scan_texts(roots: list[Path], skip: Path):
    for root in roots:
        for p in root.rglob("*"):
            try:
                if (p.is_file() and p.suffix.lower() in SCAN_EXT and p.stat().st_size <= SCAN_MAX
                        and skip not in p.resolve().parents):
                    yield p
            except OSError:
                continue


def record_hits(df: pd.DataFrame, roots: list[Path]) -> dict[str, str]:
    """Rule 8: fire_key -> reason, for IDs or '<name> Fire' found in any text file of the record."""
    ids = {}
    for r in df.itertuples():
        for v in (r.irwin, norm_id(r.unique_id)):
            if v:
                ids[v] = r.fire_key
    names = {}
    for r in df.itertuples():
        n = (r.name or "").strip()
        if n:
            names.setdefault(n.lower(), []).append(r.fire_key)
    id_re = re.compile("|".join(sorted(map(re.escape, ids), key=len, reverse=True))) if ids else None
    nm_re = (re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\s+fire\b")
             if names else None)
    hit: dict[str, str] = {}
    nfiles = 0
    for p in scan_texts(roots, OUT.resolve()):
        nfiles += 1
        try:
            t = p.read_text(errors="ignore").lower()
        except OSError:
            continue
        if id_re:
            for m in id_re.finditer(t):
                hit.setdefault(ids[m.group(0)], "rule8_id_in_record")
        if nm_re:
            for m in nm_re.finditer(t):
                for k in names[m.group(1)]:
                    hit.setdefault(k, "rule8_name_in_record")
    return hit, nfiles


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-root", nargs="*", type=Path,
                    default=[HOME / "reports", HOME / "validation-data", REPO / "docs"])
    a = ap.parse_args()
    us = OUT / "us"
    lst = us / "sealed_us_2026.csv"
    if lst.exists():
        sys.exit(f"refusing to overwrite an existing seal: {lst}")
    us.mkdir(parents=True, exist_ok=True)
    raw = OUT / "raw" / "wfigs_ytd_2026_ge5000ha.geojson"
    gj, t_dl = download(raw)
    t_dl_ts = pd.Timestamp(t_dl)
    rows = []
    for f in gj["features"]:
        p = f["properties"]
        rows.append(dict(
            objectid=p.get("OBJECTID"), unique_id=p.get("attr_UniqueFireIdentifier"),
            fire_key="WFIGS:" + str(p.get("attr_UniqueFireIdentifier") or f"OID{p.get('OBJECTID')}"),
            irwin=norm_id(p.get("attr_IrwinID")) or norm_id(p.get("poly_IRWINID")),
            name=p.get("attr_IncidentName") or p.get("poly_IncidentName") or "",
            cat=p.get("attr_IncidentTypeCategory"), state=str(p.get("attr_POOState") or "")[3:],
            acres=p.get("poly_GISAcres"), disc=ms(p.get("attr_FireDiscoveryDateTime")),
            cont=ms(p.get("attr_ContainmentDateTime")), ctrl=ms(p.get("attr_ControlDateTime")),
            out=ms(p.get("attr_FireOutDateTime")), pct=p.get("attr_PercentContained"),
            lat=p.get("attr_InitialLatitude"), lon=p.get("attr_InitialLongitude"),
            geometry=f.get("geometry")))
    from shapely.geometry import shape
    df = pd.DataFrame(rows)
    df["geometry"] = [shape(g) if g else None for g in df.geometry]
    g = gpd.GeoDataFrame(df, geometry="geometry", crs=4326)
    g["end"] = g[["cont", "ctrl", "out"]].min(axis=1, skipna=True)
    removed: dict[int, str] = {}

    def drop(mask, rule):
        for i in g.index[mask & ~g.index.isin(list(removed))]:
            removed[i] = rule

    drop(g.cat != "WF", "rule1_not_WF")
    drop(~g.state.isin(WUS), "rule2_outside_11_western_states")
    drop(~(pd.to_numeric(g.acres, errors="coerce") >= HA_AC) | g.geometry.isna(), "rule3_under_5000ha_or_no_shape")
    drop(g.disc.isna() | (g.disc.dt.year != 2026), "rule4_not_discovered_2026")
    pct = pd.to_numeric(g.pct, errors="coerce")
    complete = g.end.notna() | (pct >= 100)
    drop(~complete, "rule5_not_complete")
    drop(complete & g.end.notna() & (g.end > t_dl_ts - END_LAG), "rule5_ended_within_7d")
    # rule 5 with only PercentContained >= 100 and no date: the end time is unknown, so the 7-day lag cannot be
    # shown; such records are removed as not complete (conservative).
    drop(g.end.isna(), "rule5_no_end_time")
    ga = g.to_crs(5070)
    ga["geometry"] = ga.geometry.make_valid()
    lat, lon = pd.to_numeric(g.lat, errors="coerce"), pd.to_numeric(g.lon, errors="coerce")
    nostart = lat.isna() | lon.isna()
    drop(nostart, "rule6_no_initial_point")
    ok = ~nostart
    pts = gpd.GeoSeries([Point(x, y) if o else None for x, y, o in zip(lon, lat, ok)], index=g.index, crs=4326).to_crs(5070)
    dist = pd.Series([pts[i].distance(ga.geometry[i]) if ok[i] and ga.geometry[i] is not None else np.inf
                      for i in g.index], index=g.index)
    drop(ok & (dist > START_TOL_M), "rule6_initial_point_over_2km")
    # one per IRWIN ID (largest polygon kept)
    keep = g.index[~g.index.isin(list(removed))]
    sub = g.loc[keep].assign(_ac=pd.to_numeric(g.acres, errors="coerce"))
    for irw, grp in sub[sub.irwin != ""].groupby("irwin"):
        if len(grp) > 1:
            best = grp.sort_values(["_ac", "fire_key"], ascending=[False, True]).index[0]
            drop(g.index.isin([i for i in grp.index if i != best]), "rule7_duplicate_irwin")
    drop(g.name.str.upper().str.contains("COMPLEX", na=False), "rule7_complex")
    # spatial duplicates: overlap > 50 % of the smaller -> keep the larger
    keep = list(g.index[~g.index.isin(list(removed))])
    area = ga.geometry.area
    order_sz = sorted(keep, key=lambda i: (-area[i], g.fire_key[i]))
    kept: list[int] = []
    for i in order_sz:
        gi = ga.geometry[i]
        dup = False
        for j in kept:
            gj_ = ga.geometry[j]
            if gi.intersects(gj_) and gi.intersection(gj_).area > OVERLAP * min(area[i], area[j]):
                dup = True
                break
        if dup:
            removed[i] = "rule7_overlapping_duplicate"
        else:
            kept.append(i)
    # rule 8: never used in the record
    cand = g.loc[kept]
    hits, nfiles = record_hits(cand, a.scan_root)
    for i in kept:
        if g.fire_key[i] in hits:
            removed[i] = hits[g.fire_key[i]]
    elig = [i for i in kept if i not in removed]
    # draw
    keys = sorted(g.fire_key[i] for i in elig)
    perm = np.random.default_rng(S_SEAL_US).permutation(len(keys))
    ordered = [keys[k] for k in perm]
    n = len(ordered)
    n_conf = math.ceil(n / 2)
    byk = {g.fire_key[i]: i for i in elig}
    out = []
    for o, k in enumerate(ordered, start=1):
        i = byk[k]
        out.append(dict(order=o, subset="confirm" if o <= n_conf else "scorecard", fire_key=k,
                        irwin_id=g.irwin[i], incident_name=g.name[i], state=g.state[i],
                        discovery_utc=g.disc[i].strftime("%Y-%m-%dT%H:%MZ"),
                        end_utc=g.end[i].strftime("%Y-%m-%dT%H:%MZ"),
                        acres=f"{float(g.acres[i]):.1f}", ha=f"{float(g.acres[i]) * 0.40468564224:.1f}",
                        initial_lat=f"{float(lat[i]):.5f}", initial_lon=f"{float(lon[i]):.5f}"))
    pd.DataFrame(out).to_csv(lst, index=False, lineterminator="\n")
    per = us / "sealed_us_2026_perimeters.geojson"
    pf = [f for f in gj["features"] if "WFIGS:" + str(f["properties"].get("attr_UniqueFireIdentifier")) in set(ordered)]
    per.write_text(json.dumps({"type": "FeatureCollection", "features": pf}, separators=(",", ":"), sort_keys=True))
    log = pd.DataFrame([dict(fire_key=g.fire_key[i], incident_name=g.name[i], state=g.state[i], rule=r)
                        for i, r in sorted(removed.items(), key=lambda x: g.fire_key[x[0]])])
    log.to_csv(us / "eligibility_log.csv", index=False, lineterminator="\n")
    summ = dict(
        rules="docs/SEALED_2026.md sections 1-4", layer=LAYER, item=ITEM, where=WHERE, downloaded_utc=t_dl,
        seed_S_SEAL_US=S_SEAL_US, n_query_records=len(g),
        removed_by_rule=log.rule.value_counts().sort_index().to_dict() if len(log) else {},
        n_record_text_files_scanned=nfiles, scan_roots=[str(p) for p in a.scan_root],
        n_sealed=n, n_confirm=n_conf, n_scorecard=n - n_conf,
        sealed_by_state=pd.Series([r["state"] for r in out]).value_counts().sort_index().to_dict() if out else {},
        sha256={"raw/wfigs_ytd_2026_ge5000ha.geojson": sha256(raw), "us/sealed_us_2026.csv": sha256(lst),
                "us/sealed_us_2026_perimeters.geojson": sha256(per), "us/eligibility_log.csv": sha256(us / "eligibility_log.csv")},
        script_sha256=sha256(Path(__file__)))
    (us / "draw_summary.json").write_text(json.dumps(summ, indent=1, default=str))
    print(json.dumps({k: v for k, v in summ.items()}, indent=1, default=str))


if __name__ == "__main__":
    main()
