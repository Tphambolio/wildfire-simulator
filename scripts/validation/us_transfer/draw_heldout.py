"""Draw the fresh held-out US fire set of PREREG_US_TRANSFER1 (docs/PREREG_US_TRANSFER1.md §3).

Purpose: a held-out set of western-US fires that no FireSim test, agent or setting choice has touched, drawn by a
seeded rule from attributes and geometry only (no hotspot, weather, growth or model output is opened here).

Pool: MTBS burned-area boundaries (``mtbs_perims_DD.shp``, the v8 raw copy; public domain), rules applied in order:
  1. ``incid_type == 'Wildfire'``;
  2. state (first two letters of ``event_id``) in the 11 western states of PREREG_LR8;
  3. ignition date (``ig_date``) in 2017 (LANDFIRE LF2016 = disturbances through 2016 is pre-fire; VIIRS S-NPP);
  4. ``burnbndac`` >= 12,355.3 acres (5,000 ha, the v8 / SEALED_2026 size class);
  5. no ``COMPLEX`` in ``incid_name`` (several ignitions);
  6. not a v8 split key (``MTBS:<event_id>``; none expected for 2017) and not in the SEALED_2026 lists;
  7. never used in the record: ``event_id`` or ``irwinid`` (lower case) found, or "<incid_name> fire" (word-bounded,
     case-insensitive) found, in any text file (.md .txt .csv .json .geojson .py .tsv .yaml .yml, <= 50 MB) under
     ``~/dev/wildfire/reports/``, ``~/dev/wildfire/validation-data/`` (except ``sealed2026/`` and this draw's own
     output folder) or the repository's ``docs/`` (the SEALED_2026 rule 8);
  8. two boundaries overlapping by > 50 % of the smaller (EPSG:5070): keep the larger.
Draw: eligible keys sorted, permuted with ``numpy.random.default_rng(S_HO)``, ``S_HO = 2026102911``; the first
``N_TEST = 40`` are the ``test`` subset (all if fewer), the rest ``reserve`` (unopened, decide nothing).

Outputs (refuses to overwrite): ``~/dev/wildfire/validation-data/us-transfer-heldout/``: ``heldout_us_2017.csv``
(order, subset, fire_key, state, ig_date, ha; the seal), ``heldout_us_2017_perimeters.geojson``,
``eligibility_log.csv`` (every pool record and the rule that removed it), ``_about.json``. Prints counts and
sha256s only, never fire names.

Usage: ``python3 -m scripts.validation.us_transfer.draw_heldout`` (repository root).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import common as C

S_HO, N_TEST = 2026102911, 40
STATES = {"AZ", "CA", "CO", "ID", "MT", "NV", "NM", "OR", "UT", "WA", "WY"}
MTBS = C.V8 / "raw/mtbs/mtbs_perims_DD.shp"
OUT = C.W / "validation-data/us-transfer-heldout"
SEALED = C.W / "validation-data/sealed2026"
SCAN_EXT = {".md", ".txt", ".csv", ".json", ".geojson", ".py", ".tsv", ".yaml", ".yml"}
SCAN_MAX = 50 * 1024 * 1024
REPO = Path(__file__).resolve().parents[3]


def scan_texts(roots, skips):
    for root in roots:
        for p in root.rglob("*"):
            try:
                if (p.is_file() and p.suffix.lower() in SCAN_EXT and p.stat().st_size <= SCAN_MAX
                        and not any(s in p.resolve().parents for s in skips)):
                    yield p
            except OSError:
                continue


def record_hits(df, roots, skips):
    ids = {}
    for r in df.itertuples():
        for v in (str(r.event_id).lower(), str(r.irwinid or "").strip("{}").lower()):
            if v and v != "none" and v != "nan":
                ids[v] = r.fire_key
    names = {}
    for r in df.itertuples():
        n = str(r.incid_name or "").strip().lower()
        if n and n != "nan":
            names.setdefault(n, []).append(r.fire_key)
    id_re = re.compile("|".join(sorted(map(re.escape, ids), key=len, reverse=True))) if ids else None
    nm_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\s+fire\b") \
        if names else None
    hit, nfiles = {}, 0
    for p in scan_texts(roots, skips):
        nfiles += 1
        try:
            t = p.read_text(errors="ignore").lower()
        except OSError:
            continue
        if id_re:
            for m in id_re.finditer(t):
                hit.setdefault(ids[m.group(0)], "rule7_id_in_record")
        if nm_re:
            for m in nm_re.finditer(t):
                for k in names[m.group(1)]:
                    hit.setdefault(k, "rule7_name_in_record")
    return hit, nfiles


def main() -> None:
    import geopandas as gpd

    if OUT.exists() and any(OUT.iterdir()):
        raise SystemExit(f"{OUT} is not empty: refusing to overwrite")
    OUT.mkdir(parents=True, exist_ok=True)
    g = gpd.read_file(MTBS)
    g["fire_key"] = "MTBS:" + g.event_id.astype(str)
    g["state"] = g.event_id.str[:2]
    g["ig"] = pd.to_datetime(g.ig_date)
    g["ha"] = g.burnbndac.astype(float) * 0.40468564224
    log = []
    keep = pd.Series(True, index=g.index)

    def drop(mask, rule):
        nonlocal keep
        m = keep & mask
        for k in g.loc[m, "fire_key"]:
            log.append((k, rule))
        keep = keep & ~mask

    # rules 1-3 define the pool silently (the log lists records removed from rule 4 on)
    pool = (g.incid_type == "Wildfire") & g.state.isin(STATES) & (g.ig.dt.year == 2017)
    keep = pool.copy()
    n_pool = int(pool.sum())
    drop(g.burnbndac.astype(float) < 12355.3, "rule4_under_5000ha")
    drop(g.incid_name.astype(str).str.upper().str.contains("COMPLEX"), "rule5_complex")
    split = set(pd.read_csv(C.SPLIT).fire_key)
    sealed_keys = set()
    for f in ("us/sealed_us_2026.csv", "us/sealed_us_2026_supp.csv"):
        if (SEALED / f).exists():
            sealed_keys |= set(pd.read_csv(SEALED / f).fire_key)
    drop(g.fire_key.isin(split | sealed_keys), "rule6_v8_or_sealed")
    cand = g[keep]
    roots = [C.W / "reports", C.W / "validation-data", REPO / "docs"]
    hits, nfiles = record_hits(cand, roots, [SEALED.resolve(), OUT.resolve()])
    for k, why in hits.items():
        drop(g.fire_key == k, why)
    # rule 8: overlapping duplicates
    c = g[keep].to_crs(5070)
    c = c.assign(area=c.area).sort_values("area", ascending=False)
    kept = []
    for i, r in c.iterrows():
        dup = any(r.geometry.intersection(c.loc[j].geometry).area > 0.5 * min(r.area, c.loc[j].area) for j in kept)
        if dup:
            drop(g.index == i, "rule8_overlap_duplicate")
        else:
            kept.append(i)
    el = g[keep].sort_values("fire_key")
    perm = np.random.default_rng(S_HO).permutation(len(el))
    el = el.iloc[perm].reset_index(drop=True)
    el["order"] = np.arange(1, len(el) + 1)
    el["subset"] = np.where(el.order <= N_TEST, "test", "reserve")
    lst = el[["order", "subset", "fire_key", "state", "ig_date", "ha"]]
    lst.to_csv(OUT / "heldout_us_2017.csv", index=False)
    el[["order", "subset", "fire_key", "incid_name", "irwinid", "ig_date", "ha", "geometry"]].to_file(
        OUT / "heldout_us_2017_perimeters.geojson", driver="GeoJSON")
    pd.DataFrame(log, columns=["fire_key", "rule"]).to_csv(OUT / "eligibility_log.csv", index=False)
    counts = pd.Series([r for _, r in log]).value_counts().to_dict()
    about = {"created_utc": datetime.now(timezone.utc).isoformat(), "script": "scripts/validation/us_transfer/draw_heldout.py",
             "script_sha256": C.sha256(Path(__file__)), "mtbs_sha256": C.sha256(MTBS), "seed": S_HO, "n_test": N_TEST,
             "n_pool_rules_1_3": n_pool, "removed": counts, "n_eligible": int(len(el)),
             "n_test_drawn": int((el.subset == "test").sum()), "n_record_text_files_scanned": nfiles,
             "sha256": {p.name: C.sha256(p) for p in sorted(OUT.glob("*")) if p.suffix in (".csv", ".geojson")},
             "note": "attributes and geometry only; nothing run, no hotspot/weather/growth opened"}
    (OUT / "_about.json").write_text(json.dumps(about, indent=1))
    (OUT / "README.md").write_text(
        "# PREREG_US_TRANSFER1 held-out set (sealed)\n\nDrawn by `scripts/validation/us_transfer/draw_heldout.py` "
        "(rules in `docs/PREREG_US_TRANSFER1.md` §3). **Do not open** (no model run, no VIIRS, weather or growth "
        "of these fires) until the main session authorises held-out scoring. The `reserve` subset decides nothing.\n")
    print(json.dumps({k: v for k, v in about.items() if k not in ("note",)}, indent=1))


if __name__ == "__main__":
    sys.exit(main())
