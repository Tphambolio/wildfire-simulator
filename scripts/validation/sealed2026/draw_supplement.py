"""Seal the supplementary 2026 US block (docs/SEALED_2026.md section 6a; owner decision D2, 2026-10-25).

Purpose: from the SAME pool snapshot as the main seal (raw/wfigs_ytd_2026_ge5000ha.geojson, no re-download),
take the records that pass rules 1-4 and were removed only by the conservative rule-5 clause "100 % contained,
no containment/control/out time", apply rules 6-8 exactly as draw.py does (checking duplicates against the 32
sealed fires as well as within the block), order them with the frozen seed S_SEAL_US_SUPP = 2026102403, label
every record `scale` (never decides), and write the sealed list with its sha256.

Inputs (outside the repository, default ~/dev/wildfire/validation-data/sealed2026, override with
FIRESIM_SEALED2026_DIR): raw/wfigs_ytd_2026_ge5000ha.geojson (sha256 checked against section 6) and
us/sealed_us_2026.csv (read only for its fire keys, IRWIN ids and geometry match, to exclude duplicates; its
sha256 is checked; nothing of it is printed).
Outputs:
  us/sealed_us_2026_supp.csv                 the sealed supplementary list (its sha256 is the seal)
  us/sealed_us_2026_supp_perimeters.geojson  the block's perimeters as downloaded
  us/supp_eligibility_log.csv                candidates removed by a rule, with the rule
  us/supp_draw_summary.json                  counts, hashes (no fire names)
Prints counts and hashes only, never fire names. Refuses to overwrite an existing file.

Usage: python3 -I scripts/validation/sealed2026/draw_supplement.py [--scan-root DIR ...]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from draw import HA_AC, HOME, OUT, OVERLAP, REPO, START_TOL_M, WUS, ms, norm_id, record_hits, sha256  # noqa: E402

S_SEAL_US_SUPP = 2026102403
RAW_SHA = "9d04ba84696b5af48efac2794c8eb1f741f22e603b9e780a89962b1149140c19"
SEAL_SHA = "b9c7a303c71e63ebc63e9fdf25cda18e8ebcaf61a5af36a3ac69cdd297748e80"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-root", nargs="*", type=Path,
                    default=[HOME / "reports", HOME / "validation-data", REPO / "docs"])
    a = ap.parse_args()
    us = OUT / "us"
    lst = us / "sealed_us_2026_supp.csv"
    if lst.exists():
        sys.exit(f"refusing to overwrite an existing seal: {lst}")
    raw = OUT / "raw" / "wfigs_ytd_2026_ge5000ha.geojson"
    main_seal = us / "sealed_us_2026.csv"
    if sha256(raw) != RAW_SHA:
        sys.exit("raw snapshot sha256 does not match SEALED_2026 section 6")
    if sha256(main_seal) != SEAL_SHA:
        sys.exit("main seal sha256 does not match SEALED_2026 section 6")
    gj = json.loads(raw.read_text())
    t_dl = gj["_query"]["downloaded_utc"]
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
    df = pd.DataFrame(rows)
    df["geometry"] = [shape(g) if g else None for g in df.geometry]
    g = gpd.GeoDataFrame(df, geometry="geometry", crs=4326)
    g["end"] = g[["cont", "ctrl", "out"]].min(axis=1, skipna=True)
    pct = pd.to_numeric(g.pct, errors="coerce")
    # candidates: pass rules 1-4, and were removed by draw.py only as rule5_no_end_time
    r1_4 = ((g.cat == "WF") & g.state.isin(WUS) & (pd.to_numeric(g.acres, errors="coerce") >= HA_AC)
            & g.geometry.notna() & g.disc.notna() & (g.disc.dt.year == 2026))
    cand_mask = r1_4 & g.end.isna() & (pct >= 100)
    cand = list(g.index[cand_mask])
    n_candidates = len(cand)
    removed: dict[int, str] = {}

    def drop(idx_list, rule):
        for i in idx_list:
            if i in cand and i not in removed:
                removed[i] = rule

    ga = g.to_crs(5070)
    ga["geometry"] = ga.geometry.make_valid()
    lat, lon = pd.to_numeric(g.lat, errors="coerce"), pd.to_numeric(g.lon, errors="coerce")
    nostart = lat.isna() | lon.isna()
    drop([i for i in cand if nostart[i]], "rule6_no_initial_point")
    for i in cand:
        if i in removed:
            continue
        pt = gpd.GeoSeries([Point(lon[i], lat[i])], crs=4326).to_crs(5070)[0]
        if ga.geometry[i] is None or pt.distance(ga.geometry[i]) > START_TOL_M:
            removed[i] = "rule6_initial_point_over_2km"
    # rule 7 against the main seal: same fire key or IRWIN id, or overlapping shape
    sealed = pd.read_csv(main_seal, dtype=str)
    sealed_keys = set(sealed.fire_key)
    sealed_irwin = set(norm_id(x) for x in sealed.irwin_id.fillna("") if norm_id(x))
    sealed_idx = [i for i in g.index if g.fire_key[i] in sealed_keys]
    area = ga.geometry.area
    for i in cand:
        if i in removed:
            continue
        if g.fire_key[i] in sealed_keys or (g.irwin[i] and g.irwin[i] in sealed_irwin):
            removed[i] = "rule7_in_main_seal"
            continue
        gi = ga.geometry[i]
        for j in sealed_idx:
            gj_ = ga.geometry[j]
            if gj_ is not None and gi.intersects(gj_) and gi.intersection(gj_).area > OVERLAP * min(area[i], area[j]):
                removed[i] = "rule7_overlaps_main_seal"
                break
    # rule 7 within the block: one per IRWIN (largest kept), no complexes, overlapping duplicates
    keep = [i for i in cand if i not in removed]
    sub = g.loc[keep].assign(_ac=pd.to_numeric(g.acres, errors="coerce"))
    for irw, grp in sub[sub.irwin != ""].groupby("irwin"):
        if len(grp) > 1:
            best = grp.sort_values(["_ac", "fire_key"], ascending=[False, True]).index[0]
            drop([i for i in grp.index if i != best], "rule7_duplicate_irwin")
    drop([i for i in keep if "COMPLEX" in str(g.name[i]).upper()], "rule7_complex")
    keep = [i for i in cand if i not in removed]
    kept: list[int] = []
    for i in sorted(keep, key=lambda i: (-area[i], g.fire_key[i])):
        gi = ga.geometry[i]
        if any(gi.intersects(ga.geometry[j]) and gi.intersection(ga.geometry[j]).area > OVERLAP * min(area[i], area[j])
               for j in kept):
            removed[i] = "rule7_overlapping_duplicate"
        else:
            kept.append(i)
    # rule 8: never used in the record
    hits, nfiles = record_hits(g.loc[kept], a.scan_root)
    for i in kept:
        if g.fire_key[i] in hits:
            removed[i] = hits[g.fire_key[i]]
    elig = [i for i in kept if i not in removed]
    keys = sorted(g.fire_key[i] for i in elig)
    perm = np.random.default_rng(S_SEAL_US_SUPP).permutation(len(keys))
    ordered = [keys[k] for k in perm]
    byk = {g.fire_key[i]: i for i in elig}
    out = []
    for o, k in enumerate(ordered, start=1):
        i = byk[k]
        out.append(dict(order=o, subset="scale", fire_key=k, irwin_id=g.irwin[i], incident_name=g.name[i],
                        state=g.state[i], discovery_utc=g.disc[i].strftime("%Y-%m-%dT%H:%MZ"),
                        end_utc="", percent_contained=f"{float(pct[i]):.0f}",
                        acres=f"{float(g.acres[i]):.1f}", ha=f"{float(g.acres[i]) * 0.40468564224:.1f}",
                        initial_lat=f"{float(lat[i]):.5f}", initial_lon=f"{float(lon[i]):.5f}"))
    us.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out).to_csv(lst, index=False, lineterminator="\n")
    per = us / "sealed_us_2026_supp_perimeters.geojson"
    chosen = set(ordered)
    pf = [f for f in gj["features"]
          if "WFIGS:" + str(f["properties"].get("attr_UniqueFireIdentifier")) in chosen]
    per.write_text(json.dumps({"type": "FeatureCollection", "features": pf}, separators=(",", ":"), sort_keys=True))
    log = pd.DataFrame([dict(fire_key=g.fire_key[i], incident_name=g.name[i], state=g.state[i], rule=r)
                        for i, r in sorted(removed.items(), key=lambda x: g.fire_key[x[0]])])
    logp = us / "supp_eligibility_log.csv"
    log.to_csv(logp, index=False, lineterminator="\n")
    summ = dict(
        rules="docs/SEALED_2026.md section 6a", pool="raw/wfigs_ytd_2026_ge5000ha.geojson (section 6 snapshot)",
        pool_downloaded_utc=t_dl, seed_S_SEAL_US_SUPP=S_SEAL_US_SUPP, n_candidates=n_candidates,
        removed_by_rule=log.rule.value_counts().sort_index().to_dict() if len(log) else {},
        n_record_text_files_scanned=nfiles, scan_roots=[str(p) for p in a.scan_root],
        n_sealed=len(out), subset="scale (never decides)",
        sealed_by_state=pd.Series([r["state"] for r in out]).value_counts().sort_index().to_dict() if out else {},
        sha256={"us/sealed_us_2026_supp.csv": sha256(lst), "us/sealed_us_2026_supp_perimeters.geojson": sha256(per),
                "us/supp_eligibility_log.csv": sha256(logp), "raw/wfigs_ytd_2026_ge5000ha.geojson": RAW_SHA},
        script_sha256=sha256(Path(__file__)))
    (us / "supp_draw_summary.json").write_text(json.dumps(summ, indent=1, default=str))
    print(json.dumps(summ, indent=1, default=str))


if __name__ == "__main__":
    main()
