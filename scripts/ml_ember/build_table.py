#!/usr/bin/env python3
"""Build the frozen ML ember-cast training tables (PREREG_ML_EMBER_DEV sections 2-4, 8, 14; A1).

Purpose
    Labels, negative subsample and six Parquet tables from the training split of the v8 ember pool, through the
    guarded loader, with the descriptors (``README.md``, ``_about.json`` with sha256s) of the data folder.
    Fits nothing. Opens no sealed path.
Inputs
    frozen ``split.csv`` (sha256-checked), ``v8-pool/rule_A1/jumps_training.csv`` (must be byte-identical to the
    pre-freeze catalogue, sha256 ``5da778ae…ff54e``; otherwise the build STOPS), ``v8-pool/fit/fit_beta.json``,
    per-fire ``v8-pool/fires/<key>/`` products of training fires only, training hotspots, raw final perimeters.
Outputs (``<data folder>/table/``, Parquet, float32 features, int ids/categories; every table has ``row_id``,
``fire_key``, ``epoch``)
    cells_eval.parquet      every candidate cell of every interval holding >= 1 matched jump, label ``y``
    cells_fit.parquet       the section-3 negative subsample with ``y`` and ``w_sample``
    intervals.parquet       one row per valid interval (occurrence model, RQ2b)
    fires.parquet           one row per included fire
    coords_control.parquet  ``row_id, bi, bj`` only (section-8 box-leak control; never joined by the ladder)
    seam_control.parquet    ``in_final``, receptor fuel columns and the forbidden texture statistics (section-8
                            fuel-seam control only)
Labels (section 3)
    positive = candidate cell holding a counted jump of the primary rule (``fit.primary_jumps``: line check,
    inside final, separate start, interval <= 12 h, 1-15 km), matched by fire_key and interval end time
    (as ``fit.collect``), the catalogue ``epoch`` checked against the interval epoch, and
    ``block_of(meta, x_utm, y_utm)`` -> (bj, bi) looked up in the interval's candidate set.
Negative subsampling (section 3)
    ``rng = default_rng(S_NEG + fire_index)``, ``fire_index`` = position of the fire in the sorted list of all
    389 ``training`` keys of split.csv. Per interval in epoch order: with >= 1 jump, ``rng.choice(neg, 200,
    replace=False)`` (all if fewer), weight n_neg / n_drawn; with no jump, ``u = rng.random()``; if u < 0.25,
    ``rng.choice(neg, 50, replace=False)`` (all if fewer), weight n_cand / (0.25 n_drawn). Positives weight 1.
Usage
    CAPJOB_MEM=5G capjob <ml-ember python> -I scripts/ml_ember/build_table.py [--workers 3] [--fires KEY ...]
                                                            [--confirmation-commit SHA] [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "scripts.ml_ember"

from . import ROOT, paths as P
from . import features as F
from .loader import Guard, default_guard
from scripts.validation.v8 import common as C
from scripts.validation.v8.intervals import block_of

INTERVAL_COLUMNS = ("fire_key", "epoch", "t_start", "t_end", "gap_h", "n_candidates", "n_jumps", "region", "year",
                    "u10_ms", "wd_deg", "m_ff_pct", "ffmc", "temp_c", "rh_pct", "act_len_km", "act_share",
                    "fire_area_km2", "fire_age_h", "I_head_max_kwm")
FIRE_COLUMNS = ("fire_key", "region", "year", "n_intervals", "n_jumps", "fuel_restored_share", "stn_dist_km", "legacy")
FORBIDDEN_IN_CELLS = ("bi", "bj", "row", "col", "x_box", "y_box", "code_entropy_1km", "patch_size", "edge_density_1km",
                      "in_final", "gap_class", "n_jumps", "frp_end", "out_final_m")


def row_ids(fire_index: int, epoch: int, idx: np.ndarray) -> np.ndarray:
    return (np.int64(fire_index) << np.int64(40)) + (np.int64(epoch) << np.int64(24)) + idx.astype(np.int64)


def subsample(y: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Section-3 negative subsample of one interval. ``y`` = labels of the interval's candidate cells, ``rng`` =
    the fire's stream, advanced in epoch order. Returns (row indices kept, weights 1/sampling fraction).
    With >= 1 positive: all positives (w 1) + ``rng.choice(neg, 200, replace=False)`` (all if fewer; w n_neg/n_drawn).
    With none: ``u = rng.random()``; if u < 0.25, ``rng.choice(neg, 50, replace=False)`` (w n_cand / (0.25 n_drawn));
    else nothing."""
    n = len(y)
    neg = np.where(y == 0)[0]
    pos = np.where(y == 1)[0]
    if len(pos):
        k = min(P.N_NEG_POSITIVE_INTERVAL, len(neg))
        drawn = np.sort(rng.choice(neg, size=k, replace=False)) if k else neg[:0]
        w_neg = len(neg) / k if k else 1.0
        fit_idx = np.concatenate([pos, drawn])
        return fit_idx, np.where(y[fit_idx] == 1, 1.0, w_neg)
    u = rng.random()
    if u < P.P_EMPTY_INTERVAL and len(neg):
        k = min(P.N_NEG_EMPTY_INTERVAL, len(neg))
        fit_idx = np.sort(rng.choice(neg, size=k, replace=False))
        return fit_idx, np.full(k, n / (P.P_EMPTY_INTERVAL * k))
    return neg[:0], np.zeros(0)


def primary_jumps(guard: Guard) -> pd.DataFrame:
    """The counted training jumps (frozen definition ``fit.primary_jumps``), after the identity check."""
    from scripts.validation.v8 import fit as Fit
    p = guard.check(C.RULE_OUT / "jumps_training.csv")
    pre = guard.check(P.PREFREEZE / "results" / "jumps_training.csv")
    sha, sha_pre = C.sha256(p), C.sha256(pre)
    if not (sha == sha_pre == P.JUMPS_TRAINING_SHA256):
        raise RuntimeError(f"STOP: labels differ. rule_A1/jumps_training.csv {sha}; pre-freeze {sha_pre}; "
                           f"frozen {P.JUMPS_TRAINING_SHA256}")
    return Fit.primary_jumps(), sha


def build_fire(args) -> dict:
    """Worker: one training fire -> tables as DataFrames + accounting + npz verification."""
    fire_key, fire_index, jumps_rows, confirmation_commit = args
    guard = Guard(confirmation_commit=confirmation_commit)
    out = dict(fire_key=fire_key, fire_index=fire_index, status="ok", excluded=None, cells_eval=[], cells_fit=[],
               intervals=[], coords=[], seam=[], acc=dict(matched=0, no_interval=0, not_candidate=0, epoch_mismatch=0),
               verify={}, fire=None)
    fd = P.POOL / "fires" / fire_key.replace(":", "_")
    done_p = fd / "intervals" / "_done.json"
    meta = guard.read_json(fd / "meta.json")
    if not meta.get("eligible_station"):
        out.update(status="excluded", excluded="no_eligible_station"); return out
    if not done_p.exists():
        out.update(status="excluded", excluded="intervals_not_run"); return out
    done = guard.read_json(done_p)
    if done.get("status") != "ok":
        out.update(status="excluded", excluded=f"intervals_status_{done.get('status')}"); return out
    beta = F.load_beta(guard)
    ctx = F.load_fire(fire_key, guard, beta=beta)
    if ctx is None:
        out.update(status="excluded", excluded="no_detections"); return out
    by_end = {pd.Timestamp(it["t_end"]): it["epoch"] for it in done["intervals"]}
    jumps_by_epoch: dict[int, list] = {}
    for r in jumps_rows:
        ep = by_end.get(pd.Timestamp(r["t_end"]))
        if ep is None:
            out["acc"]["no_interval"] += 1; continue
        if int(r["epoch"]) != int(ep):
            out["acc"]["epoch_mismatch"] += 1
        jumps_by_epoch.setdefault(ep, []).append(block_of(meta, r["x_utm"], r["y_utm"]))
    rng = np.random.default_rng(P.S_NEG + fire_index)
    worst = {k: 0.0 for k in F.NPZ_FIELDS}
    n_int = 0; n_jumps_fire = 0
    for ip in F.iter_intervals(ctx, guard):
        n_int += 1
        for k, v in ip.verify().items():
            worst[k] = max(worst[k], v)
        n = ip.n
        y = np.zeros(n, np.int8)
        for (bj, bi) in jumps_by_epoch.get(ip.epoch, []):
            hit = np.where((ip.bj == bj) & (ip.bi == bi))[0]
            if len(hit):
                y[hit[0]] = 1; out["acc"]["matched"] += 1
            else:
                out["acc"]["not_candidate"] += 1
        n_pos = int(y.sum()); n_jumps_fire += n_pos
        neg = np.where(y == 0)[0]
        fit_idx, w = subsample(y, rng)
        rid = row_ids(fire_index, ip.epoch, np.arange(n))
        base = dict(row_id=rid, fire_key=fire_key, epoch=np.full(n, ip.epoch, np.int32))
        cells = pd.DataFrame({**base, **ip.cells, "y": y})
        if n_pos:
            out["cells_eval"].append(cells)
        if len(fit_idx):
            cf = cells.iloc[fit_idx].copy(); cf["w_sample"] = w
            out["cells_fit"].append(cf)
        union = np.union1d(np.arange(n) if n_pos else neg[:0], fit_idx)
        if len(union):
            out["coords"].append(pd.DataFrame(dict(row_id=rid[union], fire_key=fire_key, epoch=np.full(len(union), ip.epoch, np.int32),
                                                   bi=ip.control["bi"][union], bj=ip.control["bj"][union])))
            seam = {k: v[union] for k, v in ip.control.items() if k not in ("bi", "bj")}
            out["seam"].append(pd.DataFrame(dict(row_id=rid[union], fire_key=fire_key, epoch=np.full(len(union), ip.epoch, np.int32), **seam)))
        out["intervals"].append({**ip.interval, "n_jumps": n_pos})
    out["verify"] = worst
    out["fire"] = dict(fire_key=fire_key, region=ctx.region_code, year=int(ctx.rec["year"]), n_intervals=n_int, n_jumps=n_jumps_fire,
                       fuel_restored_share=float(meta.get("fuel_restored_share") or 0.0),
                       stn_dist_km=float(ctx.stn_dist.get(meta["zone_station"][0], np.nan)), legacy=False)
    out["cells_eval"] = pd.concat(out["cells_eval"], ignore_index=True) if out["cells_eval"] else None
    out["cells_fit"] = pd.concat(out["cells_fit"], ignore_index=True) if out["cells_fit"] else None
    out["coords"] = pd.concat(out["coords"], ignore_index=True) if out["coords"] else None
    out["seam"] = pd.concat(out["seam"], ignore_index=True) if out["seam"] else None
    return out


def cast(df: pd.DataFrame) -> pd.DataFrame:
    """float32 for every numeric feature; declared int types for ids / categories (section 14)."""
    for c in df.columns:
        if c in ("row_id",):
            df[c] = df[c].astype(np.int64)
        elif c in ("epoch",):
            df[c] = df[c].astype(np.int32)
        elif c in F.INT_COLUMNS:
            df[c] = df[c].astype(F.INT_COLUMNS[c])
        elif c in ("y", "in_final", "legacy"):
            df[c] = df[c].astype(np.int8) if c != "legacy" else df[c].astype(bool)
        elif c in ("n_candidates", "n_jumps", "n_intervals"):
            df[c] = df[c].astype(np.int32)
        elif c in ("bi", "bj"):
            df[c] = df[c].astype(np.int32)
        elif c in ("w_sample",):
            df[c] = df[c].astype(np.float64)
        elif pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].astype(np.float32)
        elif pd.api.types.is_integer_dtype(df[c]) and c not in ("fire_key",):
            df[c] = df[c].astype(np.int32)
    return df


def write_parquet(df: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, engine="pyarrow")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


TABLE_DOC = {
    "cells_eval": "every candidate cell (375 m) of every valid interval that holds >= 1 matched jump, with the label `y` "
                  "(1 = the cell holds a counted jump). Scoring always uses this full candidate set (section 3).",
    "cells_fit": "the section-3 fitting subsample: all positives of jump intervals + 200 uniformly drawn negatives per jump "
                 "interval; 25 % of empty intervals x 50 negatives; `w_sample` = 1 / sampling fraction (positives 1).",
    "intervals": "one row per valid interval (<= 12 h): times, candidate count, matched jump count (occurrence target, RQ2b), "
                 "zone-0 interval weather, start-perimeter geometry and the maximum head intensity over the active segments.",
    "fires": "one row per included training fire (region, year, interval and jump counts, SCANFI-restored share, "
             "zone-0 station distance, `legacy` = False for all).",
    "coords_control": "`row_id, bi, bj` ONLY: the box-relative block coordinates of every row of `cells_eval` and "
                      "`cells_fit`, for the section-8 box-leak control (control 5). Never joined by the ladder.",
    "seam_control": "per row of `cells_eval` / `cells_fit`: `in_final` (block centre inside the fire's final perimeter, the "
                    "NBAC / MTBS / WFIGS polygon the v8 code uses), the receptor fuel columns and the deliberately FORBIDDEN "
                    "texture statistics `code_entropy_1km`, `patch_size` (km2), `edge_density_1km`, for the section-8 "
                    "fuel-seam control (control 6) only.",
}


def write_readme(about: dict, out: Path) -> None:
    """Render the data folder's README.md from _about.json (purpose, tables, counts, accounting, caveats)."""
    f, lb, run, nv = about["fires"], about["labels"], about["run"], about["npz_verification"]
    lines = [
        "# ML ember-cast training tables (Phase 2 data infrastructure, 2026-10-29)", "",
        "**Purpose.** Frozen training-split feature tables for the ML ember-cast development ladder "
        "(`docs/PREREG_ML_EMBER_DEV.md` sections 2-4, 8, 14, addendum A1), built from the v8 long-range-ember pool "
        "through a loader that refuses every sealed path. **No model was fitted. No sealed path was opened** "
        f"(`refusals.log`: {run['refusals']} lines). Development uses the `training` split only; the 13 `training_legacy` "
        "fires are excluded (list below). Unit of analysis: a 375 m candidate cell in a fire-interval, exactly PREREG_LR8 "
        "sections 4 and 6. Weather is the interval's observed station weather (perfect prognosis, disclosed).", "",
        f"**Code.** `{about['code']['repo']}` branch `{about['code']['branch']}` commit `{about['code']['commit']}`, "
        f"`scripts/ml_ember/` (the frozen v8 code is imported, never copied). Built {about['built_utc']} with "
        f"{about['environment']['executable']} (python {about['environment']['python']}; "
        + ", ".join(f"{k} {v}" for k, v in about["environment"]["packages"].items()) + f"); wall {run['wall_seconds']:.0f} s, "
        f"peak RSS parent {run['peak_rss_mb_parent']:.0f} MB / largest worker {run['peak_rss_mb_largest_child']:.0f} MB, "
        f"{run['workers']} workers under `CAPJOB_MEM=5G capjob`.", "",
        "## Tables (`table/*.parquet`; float32 for every numeric feature, int for ids / categories; every table has "
        "`row_id` (int64, unique), `fire_key`, `epoch`)", "",
        "| Table | Rows | sha256 | What it is |", "|---|---|---|---|",
    ]
    for name, t in about["tables"].items():
        lines.append(f"| `{name}.parquet` | {t['rows']:,} | `{t['sha256']}` | {TABLE_DOC.get(name, '')} |")
    lines += ["", "`row_id` = fire_index << 40 | epoch << 24 | candidate index, identical across `cells_eval`, `cells_fit`, "
              "`coords_control` and `seam_control` (so the control tables join the cell tables on `row_id` only inside the "
              "controls). `intervals` and `fires` carry their own sequential `row_id`.", "",
              "## Feature columns (PREREG_ML_EMBER_DEV section 4; exact names and units)", "",
              "`d_act_km`, `d_all_km` (km, from the npz), `sec_act`, `sec_all` (0/1, ±45° of the zone's interval mean downwind "
              "direction), `bear_off_deg` (0-180°, bearing from the nearest active cell to the candidate vs the downwind "
              "direction), `act_len_km` (active edge cells x 125 m), `act_share` (active / edge cells), `fire_area_km2` (start "
              "perimeter), `fire_age_h` (t_start − first in-perimeter detection), `gap_h`, `hour_local` (solar hour of t_start at "
              "the grid centre longitude), `u10_ms`, `wd_deg` (from), `m_ff_pct`, `ffmc`, `temp_c`, `rh_pct` (zone interval "
              "means, station arm), `stn_dist_km` (D), `I_head_kwm`, `ros_mmin`, `cfb` (FBP at the nearest active cell at the "
              "observed spread rate over the previous interval; NaN when the interval has no active cell; first interval of a "
              "fire: ROS floor 0.5 m/min), `spread_prev_km` (advance of the nearest active cell over the previous interval; NaN "
              "for the first interval), `frp_prev_mw` (summed VIIRS FRP of epoch i−1 detections within 1 km of an active cell), "
              "`fuel_code` (CIFFC national code at the block centre; US crosswalk), `fuel_group` (M8 WAF class: 0 grass / US "
              "grass-shrub-urban, 1 D-M, 2 C-timber, −1 non-fuel centre), `hazard`, `hazshare` (npz), `curing_pct`, "
              "`d_nonfuel_km`, `d_water_km` (NaN when the box holds no water), `A_s0..A_s2`, `A_l0..A_l2` (M8 landing "
              "sufficient statistics by WAF class, short / long mode), `zone`, `m8_log_frozen` (log M8 score with "
              "`fit_beta.json`, reference only; −690.8 = log 1e-300 for zero cells), `b1`, `region` (0 CAN, 1 US), `year`, "
              "`sat_era` (1 = S-NPP + NOAA-20), `fuel_restored_share` (D).", "",
              "**Not produced (conservative readings, for the professor):**", ""]
    for k, v in about["omitted_contract_columns"].items():
        lines.append(f"- `{k}`: {v}")
    lines += ["", "**Forbidden everywhere in the cell tables** (asserted at build time and in the tests): `bi`, `bj`, any "
              "box-relative coordinate, fuel texture statistics, anything from epoch i or later, the final perimeter, gap "
              "class, jump counts, ELMFIRE.", "",
              "## Labels (section 3) and match accounting", "",
              f"Positives = candidate cells holding a counted jump of `v8-pool/rule_A1/jumps_training.csv` (primary rule of "
              f"`fit.primary_jumps`), verified byte-identical to the pre-freeze catalogue: observed sha256 "
              f"`{about['hashes']['jumps_training_observed_rule_A1']}` (rule_A1) = `{about['hashes']['jumps_training_observed_prefreeze']}` "
              f"(pre-freeze) = frozen `{about['hashes']['jumps_training_frozen']}`. Matched by fire_key + interval end time "
              "(as `fit.collect`), epoch checked, `block_of(meta, x_utm, y_utm)` looked up in the interval's candidate set.", "",
              "| Count | Value |", "|---|---|",
              f"| primary catalogue rows | {lb['catalogue_primary_rows']} |",
              f"| on training fires / on legacy fires / on other fires | {lb['on_training_fires']} / {lb['on_legacy_fires']} / {lb['on_decision_or_unknown_fires']} |",
              f"| matched to a candidate cell (positives) | {lb['matched']} |",
              f"| not matched: no valid interval with that end time | {lb['no_interval']} |",
              f"| not matched: cell not a candidate (inside the start perimeter, non-burnable block, or outside the band) | {lb['not_candidate']} |",
              f"| not matched: on an excluded fire, by reason | {lb['fire_excluded']} |",
              f"| catalogue epoch ≠ interval epoch (matched by time anyway) | {lb['epoch_mismatch']} |", "",
              "## Fires", "",
              f"{f['training_keys_in_split']} `training` keys in `split.csv`; {f['included']} included (eligible station, "
              f"intervals run, detections); excluded by reason: {f['excluded_counts']}. Legacy fires excluded "
              f"(`training_legacy`, {len(f['legacy_excluded'])}): " + ", ".join(f"`{k}`" for k in f["legacy_excluded"]) + ".", "",
              "## Verification against the v8 products", "",
              f"For every included fire the start perimeter, active segments, candidate set, distances, downwind sectors, "
              f"hazard share and zone weather were recomputed by `features.py`'s sibling path and compared with "
              f"`intervals/*.npz`; maximum absolute difference over all fires and intervals: {nv['max_abs_diff_over_all_fires']} "
              "(0.0 = bit-identical; per fire in `_about.json`).", "",
              "## Seeds", "",
              f"`S_NEG = {about['seeds']['S_NEG']}`; {about['seeds']['stream']}; {about['seeds']['n_neg_positive_interval']} "
              f"negatives per jump interval; empty intervals sampled with p = {about['seeds']['p_empty_interval']}, "
              f"{about['seeds']['n_neg_empty_interval']} negatives each; the stream advances in epoch order "
              "(`rng.choice` for jump intervals; `rng.random()` then `rng.choice` for empty ones).", "",
              "## Caveats", "",
              "- Terrain features are absent (zero `elev` in the v8 inputs); `stn_dist_km` is the station's distance to the "
              "final perimeter as `meta.stations` records it (diagnostic only, never a product feature).",
              "- `I_head_kwm`, `ros_mmin`, `cfb`, `spread_prev_km` are NaN where no active cell exists (M8 falls back to the "
              "perimeter for distances; FBP has no source there); the ladder must handle NaN (GBM natively; GLM by imputation, "
              "disclosed).",
              "- Multi-zone fires: cell weather is the cell's zone; `intervals.parquet` weather is zone 0 (the nearest station).",
              "- `d_nonfuel_km` / `d_water_km` are clipped by the run box like every v8 distance field.",
              "- Canadian fuel is a composite (SCANFI re-typing of burned cells): gated by the fuel-seam control (`seam_control`).",
              "- Only 158 of 389 training fires have an eligible station and intervals; the station-arm population is 'fires "
              "near airports' (PREREG_LR8 A2.1).", "",
              "## Files", "",
              "`README.md` (this), `_about.json` (machine-readable descriptor with sha256s, per-fire verification, environment), "
              "`table/` (six Parquet files), `scripts/README.md` (pointer to the repository code), `refusals.log` (created only "
              "if a sealed path was ever refused).", ""]
    (out / "README.md").write_text("\n".join(lines))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=P.DATA_FOLDER)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--fires", nargs="*", default=None, help="subset of training keys (default: all)")
    ap.add_argument("--confirmation-commit", default=None, help="stage-2 commit sha on origin; the only guard override")
    ap.add_argument("--min-free-gb", type=float, default=20.0)
    ap.add_argument("--readme-only", action="store_true", help="re-render README.md from an existing _about.json")
    a = ap.parse_args(argv)
    if a.readme_only:
        write_readme(json.loads((a.out / "_about.json").read_text()), a.out); return 0
    C.guard()
    t_wall = time.time()
    st = os.statvfs(a.out.parent if a.out.parent.exists() else Path.home())
    free_gb = st.f_bavail * st.f_frsize / 1e9
    if free_gb < a.min_free_gb:
        print(f"refusing to write: {free_gb:.0f} GB free < {a.min_free_gb} GB", file=sys.stderr); return 2
    guard = default_guard(a.confirmation_commit)
    J, labels_sha = primary_jumps(guard)
    training = guard.training_keys()
    legacy = guard.legacy_keys()
    fire_index = {k: i for i, k in enumerate(training)}
    keys = a.fires or training
    for k in keys:
        if k not in fire_index:
            raise SystemExit(f"{k} is not a training fire")
    jumps_by_fire = {k: J[J.fire_key == k][["epoch", "t_start", "t_end", "x_utm", "y_utm"]].to_dict("records") for k in keys}
    acc_total = dict(catalogue_primary_rows=int(len(J)),
                     on_legacy_fires=int(J.fire_key.isin(legacy).sum()),
                     on_decision_or_unknown_fires=int((~J.fire_key.isin(training + legacy)).sum()),
                     on_training_fires=int(J.fire_key.isin(training).sum()),
                     matched=0, no_interval=0, not_candidate=0, epoch_mismatch=0, fire_excluded={})
    tasks = [(k, fire_index[k], jumps_by_fire[k], a.confirmation_commit) for k in keys]
    results = []
    if a.workers > 1:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(a.workers) as pool:
            for r in pool.imap_unordered(build_fire, tasks):
                results.append(r); print(r["fire_key"], r["status"], r.get("excluded") or r["acc"], flush=True)
    else:
        for t in tasks:
            r = build_fire(t); results.append(r); print(r["fire_key"], r["status"], r.get("excluded") or r["acc"], flush=True)
    results.sort(key=lambda r: r["fire_index"])
    excluded = {}
    for r in results:
        if r["status"] != "ok":
            excluded[r["fire_key"]] = r["excluded"]
            nj = len(jumps_by_fire[r["fire_key"]])
            acc_total["fire_excluded"][r["excluded"]] = acc_total["fire_excluded"].get(r["excluded"], 0) + nj
            continue
        for k in ("matched", "no_interval", "not_candidate", "epoch_mismatch"):
            acc_total[k] += r["acc"][k]
    ok = [r for r in results if r["status"] == "ok"]
    tables = {
        "cells_eval": pd.concat([r["cells_eval"] for r in ok if r["cells_eval"] is not None], ignore_index=True),
        "cells_fit": pd.concat([r["cells_fit"] for r in ok if r["cells_fit"] is not None], ignore_index=True),
        "intervals": pd.DataFrame([it for r in ok for it in r["intervals"]], columns=list(INTERVAL_COLUMNS)),
        "fires": pd.DataFrame([r["fire"] for r in ok], columns=list(FIRE_COLUMNS)),
        "coords_control": pd.concat([r["coords"] for r in ok if r["coords"] is not None], ignore_index=True),
        "seam_control": pd.concat([r["seam"] for r in ok if r["seam"] is not None], ignore_index=True),
    }
    for name in ("cells_eval", "cells_fit"):
        bad = [c for c in tables[name].columns if c in FORBIDDEN_IN_CELLS]
        assert not bad, f"forbidden columns in {name}: {bad}"
        extra = set(tables[name].columns) - set(F.CELL_FEATURES) - {"row_id", "fire_key", "epoch", "y", "w_sample"}
        assert not extra, f"columns outside the section-4 contract in {name}: {extra}"
    for name in ("intervals", "fires"):
        tables[name].insert(0, "row_id", np.arange(len(tables[name]), dtype=np.int64))
    tables["fires"]["epoch"] = np.int32(0)  # every table carries fire_key and epoch; a fire row has no interval
    table_dir = a.out / "table"
    about_tables = {}
    for name, df in tables.items():
        df = cast(df)
        assert df.row_id.is_unique, name
        sha = write_parquet(df, table_dir / f"{name}.parquet")
        about_tables[name] = dict(rows=int(len(df)), sha256=sha, columns={c: str(t) for c, t in df.dtypes.items()})
        print(name, len(df), sha, flush=True)
    verify = {r["fire_key"]: r["verify"] for r in ok}
    worst_all = {k: max(v[k] for v in verify.values()) for k in F.NPZ_FIELDS} if verify else {}
    env = dict(python=platform.python_version(), executable=sys.executable,
               packages={m: __import__(m).__version__ for m in ("numpy", "pandas", "scipy", "pyarrow", "shapely", "geopandas", "rasterio")})
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    peak_children_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0
    about = dict(
        title="ML ember-cast training tables (Phase 2 data infrastructure)",
        built_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        code=dict(repo="Tphambolio/wildfire-simulator", branch="research/ml-ember-cast", commit=git_head(),
                  modules=["scripts/ml_ember/{__init__,paths,loader,features,build_table}.py"],
                  frozen_v8_imports=["scripts/validation/v8/{common,intervals,inputs,fit}.py", "scripts/validation/v8_score.py"]),
        prereg=dict(file="docs/PREREG_ML_EMBER_DEV.md", sections_implemented=["2 (data, sealed list)", "3 (unit, labels, negatives, S_NEG)",
                    "4 (feature contract)", "8 (control tables: box coordinates, fuel seam)", "14 (loader guard, frozen table)", "A1 (sealed paths, label identity)"]),
        environment=env,
        seeds=dict(S_NEG=P.S_NEG, stream="default_rng(S_NEG + fire_index), fire_index = rank in sorted training keys of split.csv (389 keys)",
                   n_neg_positive_interval=P.N_NEG_POSITIVE_INTERVAL, p_empty_interval=P.P_EMPTY_INTERVAL, n_neg_empty_interval=P.N_NEG_EMPTY_INTERVAL),
        hashes=dict(split_csv=P.SPLIT_SHA256, jumps_training_frozen=P.JUMPS_TRAINING_SHA256, jumps_training_observed_rule_A1=labels_sha,
                    jumps_training_observed_prefreeze=C.sha256(P.PREFREEZE / "results" / "jumps_training.csv"), fit_beta=P.FIT_BETA_SHA256),
        fires=dict(training_keys_in_split=len(training), requested=len(keys), included=len(ok), excluded=excluded,
                   excluded_counts=pd.Series(list(excluded.values())).value_counts().to_dict() if excluded else {},
                   legacy_excluded=legacy, included_keys=[r["fire_key"] for r in ok]),
        labels=acc_total,
        tables=about_tables,
        npz_verification=dict(fields=list(F.NPZ_FIELDS), max_abs_diff_over_all_fires=worst_all, per_fire=verify,
                              note="recomputed start perimeter, active segments, distances, sectors, hazard share, zone weather vs the v8 intervals/*.npz; 0.0 = identical"),
        omitted_contract_columns=dict(
            elev_m_slope_pct_aspect_deg_tpi_1km="inputs.npz elev is all zero for every v8 fire (inputs.build writes np.zeros); not computable from the contract's source",
            d_act_x_fuel_group="derived by the ladder from d_act_km and fuel_group",
            perim_agree_flag="struck by PREREG_LR8 A1.4; does not exist",
            gap_class="jump attribute, forbidden as a feature; stays in the catalogue",
            m8_log="beta refit per outer fold by the ladder from A_s*, A_l*, u10_ms, m_ff_pct, zone; m8_log_frozen stored for reference"),
        encodings=dict(region={"CAN": 0, "US": 1}, fuel_group={"0": "unsheltered grass / US grass-shrub-urban (WAF 0.4)", "1": "D, M (WAF 0.2)", "2": "C, S, US timber (WAF 0.15)", "-1": "non-fuel centre cell"},
                       sat_era={"0": "S-NPP only (t_start before 2018-04-01)", "1": "S-NPP + NOAA-20"}, sec_act_sec_all="1 inside +-45 deg of the zone's interval mean downwind direction",
                       units="km, m/s, deg, kW/m, m/min, MW, percent, hours, km2"),
        run=dict(wall_seconds=time.time() - t_wall, peak_rss_mb_parent=peak_mb, peak_rss_mb_largest_child=peak_children_mb, workers=a.workers,
                 refusals_log=str(P.REFUSALS_LOG), refusals=(P.REFUSALS_LOG.read_text().count("\n") if P.REFUSALS_LOG.exists() else 0)),
    )
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "_about.json").write_text(json.dumps(about, indent=1, default=float))
    write_readme(about, a.out)
    (a.out / "scripts").mkdir(exist_ok=True)
    (a.out / "scripts" / "README.md").write_text(
        "# Scripts\n\nThe code lives in the repository, not here: `Tphambolio/wildfire-simulator`, branch "
        f"`research/ml-ember-cast`, commit `{about['code']['commit']}`, `scripts/ml_ember/` (worktree "
        "`~/dev/wildfire/wt-ml-ember`). Run: `CAPJOB_MEM=5G capjob ~/miniforge3/envs/ml-ember/bin/python -I "
        "scripts/ml_ember/build_table.py`.\n")
    print(json.dumps({k: about[k] for k in ("fires", "labels", "run")}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
