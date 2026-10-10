#!/usr/bin/env python3
"""Pre-registered test: does the open-data fuel grid predict observed fire spread better than
the national grids? (CFSDS, 143 fire-days of 32 Alberta fires, the chosen set-up.)

The set-up below (``PREREG``) was committed before any open-data or CFS 2026 run was scored,
as the Jasper checks were. Grids are built by ``scripts/fuelgrid/fire_areas.py``; this script
turns them into validation domains, prints the run commands and analyses the runs.

    python scripts/validation/opendata_fire_test.py prereg
    python scripts/validation/opendata_fire_test.py domains open_masked     # (and the others)
    python scripts/validation/opendata_fire_test.py commands                # validate.py runs
    python scripts/validation/opendata_fire_test.py analyse --out <dir>

Data stay outside the repository: domains and runs under ``$FIRESIM_VALIDATION_DATA/opendata``,
grids under ``$FUELGRID_DATA/fires``. Needs PYTHONPATH=engine/src:. (repo root for
``scripts.fuelgrid``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
VALDATA = Path(os.environ.get("FIRESIM_VALIDATION_DATA", "/home/rpas/dev/wildfire/validation-data"))
OUT = VALDATA / "opendata"
FUELGRID = Path(os.environ.get("FUELGRID_DATA", Path.home() / "dev/wildfire/fuelgrid-data"))

PREREG = {
    "registered": "2026-10-10, committed before any open-data or CFS 2026 run was scored",
    "question": "Does the open-data FBP fuel grid (scripts/fuelgrid, PR #51) predict observed "
                "one-day fire growth better than the national grid FireSim's validation uses "
                "(CFS 2014b, 250 m) and the CFS 2026 national FBP layer (30 m)?",
    "fire_days": "The 143 fire-days of 32 Alberta fires in $FIRESIM_VALIDATION_DATA/manifest.json, "
                 "unchanged; same ERA5 hourly weather, CFSDS codes, DOB, DEM and working areas.",
    "setup": "The chosen set-up: --ignition active --active-days 2 --ffmc-spinup --burning-period "
             "10 20 --windows 4 6 8 10 12 14 17 24; deterministic, no spotting; every other harness "
             "default unchanged (O-1 curing 90 % outside / 60 % inside the green season, green-up "
             "day 150 / leaf-off day 258 swap D-1->D-2 and M-1->M-2, M-1/M-2 at 50 % conifer for "
             "every grid; non-fuel, water, wetland codes non-fuel).",
    "split": "Fires split by firesim.validation.report.split_fires (seed firesim-skill-2026): 16 "
             "calibration / 16 held-out test fires (79 test fire-days). No setting is tuned in "
             "this test, so the split only fixes the primary set.",
    "grids": {
        "nat2014": "(a) the current validation domains (CFS 2014b, nearest from 250 m). Must "
                   "reproduce the recorded held-out 17 h F1 0.212 (curing/runs/harness_90_60): "
                   "per-fire-day difference reported; if it differs, the rerun is the reference.",
        "open_masked": "(b, PRIMARY) open-data grid built with the merged pipeline's current "
                       "inputs (fire_areas variant 'current'), burn-scar cells replaced by the "
                       "nearest unmasked cell of the same grid (no leakage of the fire).",
        "cfs2026_masked": "(c) CFS 2026 30 m layer, the same scar mask and nearest-cell fill.",
        "open_unmasked": "(b') leakage sensitivity: open-data grid without the mask (contains "
                         "the post-fire state of the fire itself; flatters or hurts unknowably).",
        "cfs2026_unmasked": "(c') leakage sensitivity: CFS 2026 without the mask.",
        "open_prefire": "(b'') fires 2021-2024 only (12 fires, 56 fire-days): open-data grid "
                        "from inputs older than the fire year (fire_areas variant 'prefire'), "
                        "no mask: the only variant whose fuels inside the fire are real pre-fire "
                        "open-data fuels.",
    },
    "open_grid_build": {
        "pipeline": "scripts/fuelgrid as merged (#51): decision key, PARAMS, 88 features, "
                    "LightGBM conifer model refitted exactly as for St. Albert on all Edmonton "
                    "LiDAR labels (seed 20261010), quantile-mapped; NO retraining on fire areas "
                    "(transfer from Edmonton, applied as-is).",
        "area": "per fire, the union of the harness's per-day working areas (burned area to "
                "the day + 20 km), on a 20 m grid in EPSG:3400; AOI mask = whole area.",
        "deviations": "Sentinel-2: at most 4 clearest dates per MGRS tile and season (same "
                      "windows as Edmonton: spring Apr 12-May 3, autumn Oct 18-Nov 8, summer "
                      "Jul-Aug, winter Feb 10-Mar 20; current = 2023-2026 as merged); "
                      "WorldCover and footprint fractions by nearest-neighbour sub-sampling "
                      "(5 m / 4 m); OSM from the Alberta extract only; ACI exported per area.",
        "prefire_inputs": "Meta CHM (imagery 2004-2020; latest date per area checked < fire year for all 12 fires), WorldCover 2020 "
                          "v100, SCANFI v2 epoch 2020 and v3 year Y-1, ACI Y-1, Sentinel-2 "
                          "windows of years max(2018, Y-4)..Y-1 (summer/autumn from 2017); "
                          "footprints and OSM current.",
    },
    "scar_mask": "NBAC polygons (2026-05-13 release) with YEAR >= the fire year through 2025, "
                 "union the fire's own CFSDS DOB cells, dilated by 60 m (3 cells), on the 20 m "
                 "grid; masked cells take the class of the nearest unmasked cell (Euclidean, "
                 "20 m) before aggregation. Scars of fires before the fire year are kept "
                 "(they existed at the time). The CFS 2026 layer is warped (nearest) to the "
                 "same 20 m grid first so both modern grids are treated identically.",
    "codes": "Open grid -> CIFFC leafless codes (C-2 2, D-1 11, M-1 40, O-1a 31, O-1b 32, "
             "non-fuel 101, water 102); the harness swaps D-1/M-1 to D-2/M-2 by date as for the "
             "national grids. CFS 2026 mixedwood codes 4xx-9xx collapse to their base class "
             "(40-90; the harness uses 50 % conifer / 35 % dead fir for every grid). "
             "fuel_scheme 'cfs_national'.",
    "aggregation": "20 m classes -> the domain's 90 m (Horse River 180 m) lat/lng cells by "
                   "majority (GDAL mode); the 2014b grid keeps its nearest-neighbour resampling.",
    "primary_endpoint": "Held-out (16 test fires, 79 fire-days) mean 17 h F1, paired difference "
                        "open_masked - nat2014 and open_masked - cfs2026_masked, 95 % CI from "
                        "2,000 bootstrap draws of whole fires (seed 1).",
    "decision_rule": "The open grid predicts better than a baseline if the held-out mean ΔF1 > 0 "
                     "and its 95 % CI excludes 0; worse if the CI lies wholly below 0; otherwise "
                     "no detectable difference. open_prefire vs nat2014 on its 56 fire-days (all "
                     "12 fires, same rule) decides whether a masked deficit is an artefact of "
                     "the mask. Unmasked runs are never used for the verdict.",
    "secondary": "Area difference (normalised), forward spread within ±35 %, absolute head "
                 "bearing error (17 h), with fire-bootstrap CIs of paired differences; all "
                 "fires and calibration fires; 8 h and best-hour F1; per fire.",
    "strata": "Fire-day stratum = fuel group with the largest share of the day's observed "
              "growth on the 2014b grid (conifer C-1..C-7; deciduous D-1/D-2; mixedwood "
              "M-1..M-4; grass O-1a/O-1b; other = slash, non-fuel, wetland, water). Same strata "
              "for every grid.",
    "unrepresented_classes": "Share of observed growth (area, all 143 fire-days) whose 2014b "
                             "class is outside the open grid's set (C-2, D-1/2, M-1/2, O-1a/b, "
                             "non-fuel, water): C-1, C-3..C-7, M-3/M-4, S-1..S-3, wetland (120, "
                             "bog/fen/peat; non-fuel in the harness).",
}

GRIDS = ("nat2014", "open_masked", "cfs2026_masked", "open_unmasked", "cfs2026_unmasked", "open_prefire")
RUN_ARGS = ("--ignition active --active-days 2 --ffmc-spinup --burning-period 10 20 "
            "--windows 4 6 8 10 12 14 17 24")

# fuelgrid internal classes -> CIFFC leafless codes (harness swaps to green by date)
INTERNAL_TO_CIFFC = {0: 0, 1: 2, 2: 11, 3: 40, 4: 11, 5: 40, 6: 31, 7: 32, 8: 101, 9: 102}


def _load_validate():
    spec = importlib.util.spec_from_file_location("validate_cli", REPO / "scripts/validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── domains ──────────────────────────────────────────────────────────────────
def collapse_cfs(codes: np.ndarray) -> np.ndarray:
    out = codes.astype(np.int16).copy()
    mixed = (out >= 400) & (out < 1000)
    out[mixed] = (out[mixed] // 100) * 10
    return out


def nn_fill(codes: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Replace masked cells by the nearest unmasked cell (Euclidean)."""
    from scipy import ndimage as ndi
    if not mask.any():
        return codes
    if mask.all():
        raise ValueError("whole area masked")
    idx = ndi.distance_transform_edt(mask, return_distances=False, return_indices=True)
    return codes[idx[0], idx[1]]


def grid_codes(fire_id: str, grid: str) -> tuple[np.ndarray, object]:
    """20 m code array (CIFFC) and its fire-area Grid for one fire and grid variant."""
    import rasterio

    from scripts.fuelgrid import fire_areas as FA
    a = next(x for x in FA.load_areas() if x["fire_id"] == fire_id)
    g = FA.area_grid(a)
    if grid.startswith("open"):
        variant = "prefire" if grid == "open_prefire" else "current"
        z = np.load(FA.area_dir(a, variant) / "opendata_grid.npz")
        lut = np.zeros(256, np.int16)
        for k, v in INTERNAL_TO_CIFFC.items():
            lut[k] = v
        codes = lut[z["cls"]]
    else:
        with rasterio.open(FA.shared_dir(a) / "cfs_fbp_2026.tif") as src:
            codes = collapse_cfs(src.read(1))
    if grid.endswith("_masked"):
        with rasterio.open(FA.shared_dir(a) / "scar_mask.tif") as src:
            mask = src.read(1).astype(bool)
        codes = nn_fill(codes, mask)
    return codes, g


def make_domain(fire_id: str, grid: str):
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import reproject

    from firesim.validation.cfsds import FireDomain
    dom = FireDomain.load(VALDATA / f"domains/{fire_id}.npz")
    codes, g = grid_codes(fire_id, grid)
    dst = np.zeros(dom.dob.shape, np.int32)
    reproject(codes.astype(np.int32), dst, src_transform=g.transform, src_crs=g.crs,
              dst_transform=from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat),
              dst_crs="EPSG:4326", resampling=Resampling.mode, src_nodata=0, dst_nodata=0)
    meta = dict(dom.meta, fuel_source=f"opendata test grid '{grid}' (scripts/validation/opendata_fire_test.py)",
                fuel_resampling="mode from 20 m EPSG:3400")
    return FireDomain(fire_id=dom.fire_id, year=dom.year, dob=dom.dob, fuel=dst, elevation=dom.elevation,
                      lat_max=dom.lat_max, lng_min=dom.lng_min, cell_lat=dom.cell_lat,
                      cell_lng=dom.cell_lng, fuel_scheme="cfs_national", meta=meta)


def root_of(grid: str) -> Path:
    return VALDATA if grid == "nat2014" else OUT / "roots" / grid


def cmd_domains(args) -> None:
    from firesim.validation.harness import crop_for_day
    manifest = json.loads((VALDATA / "manifest.json").read_text())
    if args.grid == "open_prefire":
        manifest = [m for m in manifest if m["year"] >= 2021]
    root = root_of(args.grid)
    (root / "domains").mkdir(parents=True, exist_ok=True)
    for name in ("cfsds", "weather"):
        link = root / name
        if not link.exists():
            link.symlink_to(VALDATA / name)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1))
    report = {}
    for m in manifest:
        fid = m["fire_id"]
        p = root / f"domains/{fid}.npz"
        dom = make_domain(fid, args.grid)
        # every cell of every working area must be covered by the grid
        holes = 0
        for d in m["days"]:
            holes += int((crop_for_day(dom, d).fuel == 0).sum())
        dom.save(p)
        report[fid] = {"cells_without_fuel_code_in_working_areas": holes}
        print(fid, report[fid], flush=True)
    (root / "domains_report.json").write_text(json.dumps(report, indent=1))


def cmd_commands(args) -> None:
    for g in GRIDS:
        env = "" if g == "nat2014" else f"FIRESIM_VALIDATION_DATA={root_of(g)} "
        print(f"{env}PYTHONPATH=engine/src python scripts/validate.py --runs-dir {OUT / 'runs'} "
              f"run --name {g} {RUN_ARGS} --workers 4")


# ── analysis ─────────────────────────────────────────────────────────────────
GROUPS = {"C": "conifer", "D": "deciduous", "M": "mixedwood", "O": "grass"}


def fuel_group(ft: str) -> str:
    return GROUPS.get(ft[:1], "other") if ft != "NF" else "other"


def day_stratum(mix: dict[str, float]) -> str:
    acc: dict[str, float] = defaultdict(float)
    for k, v in mix.items():
        acc[fuel_group(k)] += v
    return max(acc, key=acc.get) if acc else "other"


def _read_run(name: str) -> dict[tuple, dict]:
    p = OUT / "runs" / f"{name}.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text().splitlines():
        r = json.loads(line)
        if "error" not in r:
            out[(r["fire_id"], r["day"])] = r
    return out


def _vals(rec: dict, w: str) -> dict:
    s = rec["members"]["det"][w]
    b = s.get("bearing_err_deg")
    return {"f1": float(np.nan_to_num(s["f1"], nan=0.0)), "area_diff": float(s["area_diff_norm"]),
            "within35": (None if s.get("ros_within_35pct") is None else float(bool(s["ros_within_35pct"]))),
            "abs_bearing": (abs(b) if b is not None and not (isinstance(b, float) and math.isnan(b)) else None),
            "precision": s.get("precision"), "recall": s.get("recall")}


def boot(diffs: dict[tuple, float], n: int = 2000, seed: int = 1) -> tuple[float, float, float]:
    by_fire: dict[str, list[float]] = defaultdict(list)
    for (fid, _), d in diffs.items():
        if d is not None and not math.isnan(d):
            by_fire[fid].append(d)
    fires = sorted(by_fire)
    if not fires:
        return (math.nan,) * 3
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.choice(len(fires), len(fires), replace=True)
        means.append(np.mean([v for k in pick for v in by_fire[fires[k]]]))
    allv = [v for f in fires for v in by_fire[f]]
    return float(np.mean(allv)), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _fmt_ci(t) -> str:
    m, lo, hi = t
    return "–" if math.isnan(m) else f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}]"


def summary_row(runs: dict, keys: list, w: str) -> dict:
    v = [_vals(runs[k], w) for k in keys]
    f1 = np.array([x["f1"] for x in v])
    ad = np.array([x["area_diff"] for x in v])
    w35 = [x["within35"] for x in v if x["within35"] is not None]
    ab = [x["abs_bearing"] for x in v if x["abs_bearing"] is not None]
    pf = defaultdict(list)
    for k, x in zip(keys, v):
        pf[k[0]].append(x["f1"])
    return {"n": len(v), "f1": float(f1.mean()) if len(v) else math.nan,
            "f1_median": float(np.median(f1)) if len(v) else math.nan,
            "area_diff": float(ad.mean()) if len(v) else math.nan,
            "over_pred": float((ad > 0).mean()) if len(v) else math.nan,
            "within35": float(np.mean(w35)) if w35 else math.nan,
            "abs_bearing_median": float(np.median(ab)) if ab else math.nan,
            "per_fire_f1": float(np.mean([np.mean(x) for x in pf.values()])) if pf else math.nan}


def paired(runs_a: dict, runs_b: dict, keys: list, w: str) -> dict:
    out = {}
    for m in ("f1", "area_diff", "within35", "abs_bearing"):
        d = {}
        for k in keys:
            a, b = _vals(runs_a[k], w)[m], _vals(runs_b[k], w)[m]
            if a is not None and b is not None:
                d[k] = a - b
        out[m] = boot(d)
    return out


def verdict(ci: tuple) -> str:
    m, lo, hi = ci
    if math.isnan(m):
        return "n/a"
    if lo > 0:
        return "better"
    if hi < 0:
        return "worse"
    return "no detectable difference"


def growth_composition(keys: list) -> dict:
    """Observed growth area by 2014b class (raw codes, incl. wetland) over the fire-days."""
    from firesim.validation.cfsds import FireDomain
    names = {101: "C-1", 102: "C-2", 103: "C-3", 104: "C-4", 105: "C-5", 106: "C-6", 107: "C-7",
             108: "D-1/D-2", 109: "M-1/M-2", 110: "M-1/M-2", 111: "M-3/M-4", 112: "M-3/M-4",
             113: "S-1", 114: "S-2", 115: "S-3", 116: "O-1a", 117: "O-1b", 118: "water",
             119: "non-fuel", 120: "wetland", 121: "urban", 122: "vegetated non-fuel", 0: "no data"}
    area: dict[str, float] = defaultdict(float)
    days_with: dict[str, int] = defaultdict(int)
    by_fire = defaultdict(list)
    for fid, d in keys:
        by_fire[fid].append(d)
    for fid, days in by_fire.items():
        dom = FireDomain.load(VALDATA / f"domains/{fid}.npz")
        for d in days:
            g = dom.dob == d
            codes, cnt = np.unique(dom.fuel[g], return_counts=True)
            seen = set()
            for c, n in zip(codes, cnt):
                nm = names.get(int(c), str(int(c)))
                area[nm] += n * dom.cell_area_m2 / 1e4
                if n / g.sum() >= 0.05:
                    seen.add(nm)
            for nm in seen:
                days_with[nm] += 1
    tot = sum(area.values())
    return {k: {"ha": round(v), "share": v / tot, "days_ge5pct": days_with.get(k, 0)}
            for k, v in sorted(area.items(), key=lambda t: -t[1])}


def cmd_analyse(args) -> None:
    from firesim.validation.report import split_fires
    runs = {g: _read_run(g) for g in GRIDS}
    manifest = json.loads((VALDATA / "manifest.json").read_text())
    split = split_fires({m["fire_id"] for m in manifest})
    allkeys = sorted({(m["fire_id"], d) for m in manifest for d in m["days"]})
    res: dict = {"prereg": PREREG, "n_runs": {g: len(r) for g, r in runs.items()}}
    nat = runs["nat2014"]
    # reproduction of the recorded numbers
    ref_path = VALDATA / "curing/runs/harness_90_60.jsonl"
    if ref_path.exists() and nat:
        ref = {(r["fire_id"], r["day"]): r for r in map(json.loads, ref_path.read_text().splitlines())}
        diffs = [abs(_vals(nat[k], "17h")["f1"] - _vals(ref[k], "17h")["f1"]) for k in nat if k in ref]
        test = [k for k in allkeys if split[k[0]] == "test" and k in nat]
        res["reproduction"] = {"n": len(diffs), "max_abs_f1_diff": max(diffs) if diffs else None,
                               "heldout_f1_17h_rerun": float(np.mean([_vals(nat[k], "17h")["f1"] for k in test])),
                               "heldout_f1_17h_recorded": float(np.mean([_vals(ref[k], "17h")["f1"] for k in test]))}
    strata = {k: day_stratum(nat[k]["obs_growth_fuel"]) for k in allkeys if k in nat}
    sets = {"test": lambda k: split[k[0]] == "test", "calibration": lambda k: split[k[0]] == "calibration",
            "all": lambda k: True}
    table = {}
    for g in GRIDS:
        if not runs[g]:
            continue
        for sname, f in sets.items():
            keys = [k for k in allkeys if f(k) and k in runs[g] and k in nat]
            if not keys:
                continue
            for w in ("8h", "17h", "oracle"):
                row = summary_row(runs[g], keys, w)
                if g != "nat2014":
                    pd = paired(runs[g], nat, keys, w)
                    row["vs_nat2014"] = pd
                    if g.startswith("open") and runs["cfs2026_masked"]:
                        kk = [k for k in keys if k in runs["cfs2026_masked"]]
                        row["vs_cfs2026_masked"] = paired(runs[g], runs["cfs2026_masked"], kk, w)
                    if g == "open_unmasked" and runs["cfs2026_unmasked"]:
                        kk = [k for k in keys if k in runs["cfs2026_unmasked"]]
                        row["vs_cfs2026_unmasked"] = paired(runs[g], runs["cfs2026_unmasked"], kk, w)
                    if g in ("open_masked", "open_unmasked", "open_prefire") and g != "open_masked" and runs["open_masked"]:
                        kk = [k for k in keys if k in runs["open_masked"]]
                        row["vs_open_masked"] = paired(runs[g], runs["open_masked"], kk, w)
                table[(g, sname, w)] = row
    res["table"] = {"|".join(k): v for k, v in table.items()}
    # nat2014 restricted to the prefire subset, for a like-for-like line
    pre_keys = sorted(runs["open_prefire"]) if runs["open_prefire"] else []
    if pre_keys:
        res["prefire_subset"] = {g: summary_row(runs[g], [k for k in pre_keys if k in runs[g]], "17h")
                                 for g in GRIDS if runs[g]}
        res["prefire_subset_paired"] = {
            g: paired(runs["open_prefire"], runs[g], [k for k in pre_keys if k in runs[g]], "17h")
            for g in ("nat2014", "cfs2026_masked", "open_masked", "cfs2026_unmasked", "open_unmasked") if runs[g]}
    # strata
    st = {}
    for g in GRIDS:
        if not runs[g]:
            continue
        for s in sorted(set(strata.values())):
            for sname in ("all", "test"):
                keys = [k for k in allkeys if strata.get(k) == s and k in runs[g] and sets[sname](k)]
                if not keys:
                    continue
                row = summary_row(runs[g], keys, "17h")
                if g != "nat2014":
                    row["vs_nat2014"] = paired(runs[g], nat, keys, "17h")
                st[f"{g}|{s}|{sname}"] = row
    res["strata"] = st
    # per fire
    pf = {}
    for fid in sorted({k[0] for k in allkeys}):
        keys = [k for k in allkeys if k[0] == fid]
        pf[fid] = {"set": split[fid], "n": len(keys),
                   "obs_growth_ha": float(sum(nat[k]["obs_growth_ha"] for k in keys if k in nat)),
                   **{g: float(np.mean([_vals(runs[g][k], "17h")["f1"] for k in keys if k in runs[g]]))
                      for g in GRIDS if runs[g] and any(k in runs[g] for k in keys)}}
    res["per_fire"] = pf
    res["growth_composition_2014b"] = growth_composition(allkeys)
    # growth fuel mix per grid (harness view, area-weighted)
    mix = {}
    for g in GRIDS:
        if not runs[g]:
            continue
        acc: dict[str, float] = defaultdict(float)
        for k, r in runs[g].items():
            for ft, v in r.get("obs_growth_fuel", {}).items():
                acc[ft] += v * r["obs_growth_ha"]
        tot = sum(acc.values()) or 1
        mix[g] = {ft: round(v / tot, 4) for ft, v in sorted(acc.items(), key=lambda t: -t[1])}
    res["growth_fuel_mix_by_grid"] = mix
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "opendata_fire_test.json").write_text(json.dumps(res, indent=1, default=float))
    write_markdown(res, out / "opendata_fire_test.md")
    print((out / "opendata_fire_test.md").read_text())


def write_markdown(res: dict, path: Path) -> None:
    L = ["# Open-data fuel grid fire test (generated)", ""]
    if "reproduction" in res:
        L += [f"Reproduction of nat2014: {json.dumps(res['reproduction'])}", ""]
    L += ["| grid | set | window | n | F1 mean (median) | per-fire F1 | area diff | over-pred | within ±35 % | abs bearing med | ΔF1 vs nat2014 [95 % CI] | verdict | ΔF1 vs CFS 2026 masked | Δarea vs nat2014 | Δwithin35 vs nat2014 | Δabs bearing vs nat2014 |",
          "|" + "---|" * 16]
    for key, r in res["table"].items():
        g, s, w = key.split("|")
        vn = r.get("vs_nat2014", {})
        vc = r.get("vs_cfs2026_masked", {})
        L.append(f"| {g} | {s} | {w} | {r['n']} | {r['f1']:.3f} ({r['f1_median']:.3f}) | {r['per_fire_f1']:.3f} | "
                 f"{r['area_diff']:+.3f} | {100 * r['over_pred']:.0f} % | {100 * r['within35']:.0f} % | "
                 f"{r['abs_bearing_median']:.0f} | {_fmt_ci(vn['f1']) if vn else '–'} | "
                 f"{verdict(vn['f1']) if vn else '–'} | {_fmt_ci(vc['f1']) if vc else '–'} | "
                 f"{_fmt_ci(vn['area_diff']) if vn else '–'} | {_fmt_ci(vn['within35']) if vn else '–'} | "
                 f"{_fmt_ci(vn['abs_bearing']) if vn else '–'} |")
    if "prefire_subset" in res:
        L += ["", "## Pre-fire subset (fires 2021-2024, 17 h)", "",
              "| grid | n | F1 | per-fire F1 | area diff | within ±35 % | open_prefire − grid ΔF1 [95 % CI] | verdict |",
              "|---|---|---|---|---|---|---|---|"]
        for g, r in res["prefire_subset"].items():
            p = res["prefire_subset_paired"].get(g)
            L.append(f"| {g} | {r['n']} | {r['f1']:.3f} | {r['per_fire_f1']:.3f} | {r['area_diff']:+.3f} | "
                     f"{100 * r['within35']:.0f} % | {_fmt_ci(p['f1']) if p else '–'} | {verdict(p['f1']) if p else '–'} |")
    L += ["", "## Strata (dominant 2014b fuel group of the observed growth), 17 h", "",
          "| grid | stratum | set | n | F1 | area diff | ΔF1 vs nat2014 [95 % CI] |", "|---|---|---|---|---|---|---|"]
    for key, r in res["strata"].items():
        g, s, sn = key.split("|")
        vn = r.get("vs_nat2014", {})
        L.append(f"| {g} | {s} | {sn} | {r['n']} | {r['f1']:.3f} | {r['area_diff']:+.3f} | "
                 f"{_fmt_ci(vn['f1']) if vn else '–'} |")
    grids = [g for g in GRIDS if any(g in v for v in res["per_fire"].values())]
    L += ["", "## Per fire, 17 h F1", "", "| fire | set | days | growth ha | " + " | ".join(grids) + " |",
          "|" + "---|" * (4 + len(grids))]
    for fid, r in res["per_fire"].items():
        L.append(f"| {fid} | {r['set']} | {r['n']} | {r['obs_growth_ha']:,.0f} | "
                 + " | ".join(f"{r[g]:.3f}" if g in r else "–" for g in grids) + " |")
    L += ["", "## Observed growth by 2014b class (all fire-days)", "", "| class | ha | share | fire-days ≥ 5 % |",
          "|---|---|---|---|"]
    for k, v in res["growth_composition_2014b"].items():
        L.append(f"| {k} | {v['ha']:,} | {100 * v['share']:.1f} % | {v['days_ge5pct']} |")
    L += ["", "## Observed growth by fuel type, each grid (harness view, area-weighted)", ""]
    for g, m in res["growth_fuel_mix_by_grid"].items():
        L.append(f"- **{g}**: " + ", ".join(f"{k} {100 * v:.1f} %" for k, v in m.items()))
    path.write_text("\n".join(L) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prereg")
    d = sub.add_parser("domains")
    d.add_argument("grid", choices=GRIDS[1:])
    sub.add_parser("commands")
    a = sub.add_parser("analyse")
    a.add_argument("--out", default=str(OUT / "analysis"))
    args = ap.parse_args()
    if args.cmd == "prereg":
        print(json.dumps(PREREG, indent=1, ensure_ascii=False))
    elif args.cmd == "domains":
        cmd_domains(args)
    elif args.cmd == "commands":
        cmd_commands(args)
    else:
        cmd_analyse(args)


if __name__ == "__main__":
    main()
