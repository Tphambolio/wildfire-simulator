#!/usr/bin/env python3
"""confirm.py -- the one-shot stage-2 confirmation of the ML ember-cast programme (docs/PREREG_ML_EMBER.md).

Purpose
    Scores the two deciding models (rung 2 of the clean arm; the pre-registered offset hybrid on the same base) and the
    rung-0 distance-only comparator ONCE on the v8 confirmation set (the 203 eligible decision fires of PREREG_LR8, as
    drawn and unsealed by v8: ``v8-pool/final/{candidates,jumps.csv,counts.csv,gate.json}``), with exactly the v8
    scorer's metric (``scripts/validation/v8_score.py``: within-interval percentile rank, ties half; per-fire mean
    difference; S3 weights; one-sided fire-level sign-flip test, 20,000 flips, seed S_TEST; stratified fire bootstrap,
    2,000 draws, seed S_BOOT). Every sealed read goes through ``loader.Guard`` and needs ``--confirmation-commit <sha>``
    naming the pushed stage-2 pre-registration commit (the only override; logged).

Stages (run in this order; each writes under reports/data/ml-ember-cast-2026-10-29/stage2/)
    fit        Fit the three models on the WHOLE training split (165 fires; no sealed path): distance-only logistic
               (C = 1), rung 2 clean arm (C = 0.01, 13 features, the ladder's monotone rule applied once and recorded),
               the hybrid = the same additive design with the frozen-beta M8 log-score as a fixed offset and the
               pre-declared d_act x fuel_group product terms. Writes models.pkl (+ sha256) and models.json. Run BEFORE
               the pre-registration freeze; the pickle's sha256 is registered.
    dryrun     In-sample smoke test on a few TRAINING fires (default: the first five with jumps): builds their features
               with the same code path as ``features``, fabricates candidate files from the frozen-beta M8 and B1 of the
               feature table, and runs ``score``'s machinery on the training labels. Proves the pipeline end to end
               without opening a sealed path. Its numbers are in-sample and are never reported as a result.
    features   Builds the section-4 features of every decision-fire interval that holds a ranked jump (jumps.csv rows
               with a candidate cell), through ``features.load_fire(..., assignment="decision")`` and
               ``iter_intervals`` (bit-for-bit the training builder), terrain from Copernicus GLO-90 (addendum A2),
               and writes decision_cells.parquet + decision_build.json (counts, npz verification, failures).
    score      Checks models.pkl against the registered sha256, scores the three models, aligns them to v8's candidate
               files (cell sets must be identical and the frozen-beta M8 log-score must equal log(score_M8); any
               mismatch halts BEFORE any S3 is computed), runs the per-fire ranking with M8, B1, DIST, RUNG2, HYBRID and
               ELMFIRE (where v8 ran it), evaluates the registered pass rules and writes confirmation.json,
               ranks_per_jump.csv and per_fire.csv. Refuses to run twice (confirmation.json present) unless --force-rescore
               is given, which is itself recorded.

Usage
    PYTHONPATH=engine/src:. python -I scripts/ml_ember/confirm.py fit
    PYTHONPATH=engine/src:. python -I scripts/ml_ember/confirm.py dryrun [--fires KEY ...]
    CAPJOB_MEM=5G capjob env PYTHONPATH=engine/src:. python -I scripts/ml_ember/confirm.py features --confirmation-commit SHA [--workers 2]
    PYTHONPATH=engine/src:. python -I scripts/ml_ember/confirm.py score --confirmation-commit SHA

Never: refit, re-tune or change a feature after any decision-fire score is seen; score a model not named in
PREREG_ML_EMBER; open a sealed 2026 record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "engine" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ml_ember import cv  # noqa: E402
from scripts.ml_ember import features as F  # noqa: E402
from scripts.ml_ember import ladder as L  # noqa: E402
from scripts.ml_ember import paths as P  # noqa: E402
from scripts.ml_ember.loader import Guard  # noqa: E402
from scripts.validation import v8_score as V  # noqa: E402
from scripts.validation.v8 import common as C  # noqa: E402

STAGE2 = P.DATA_FOLDER / "stage2"
FINAL = P.POOL / "final"
ARM = "clean"
C2 = 0.01          # registered: chosen on the inner folds in 5 of 5 outer folds of both arms (ladder.json fold_models)
C_DIST = 1.0       # rung-0 reference, as in ladder.py
R2_FEATURES = [f for f in L.R1_FEATURES if f not in L.ARM_DROP[ARM]]  # 13 features (clean arm; addendum A3)
MODELS = ["M8", "B1", "DIST", "RUNG2", "HYBRID"]
DECIDING = {"RQ1a": ("RUNG2", "M8"), "RQ1b": ("RUNG2", "DIST"), "RQ3": ("HYBRID", "M8")}
DESCRIPTIVE = [("DIST", "M8"), ("M8", "B1"), ("RUNG2", "B1"), ("HYBRID", "RUNG2"), ("HYBRID", "DIST")]
NEED = ["row_id", "fire_key", "epoch", "y", "region", "year", "gap_h", "b1", "m8_log_frozen", "fuel_group", "act_share"] + \
       [f for f in L.R1_FEATURES if f not in cv.TERRAIN]
NEED = list(dict.fromkeys(NEED))


def sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def log_factory(t0):
    return lambda *x: print(f"[{time.time() - t0:6.0f}s]", *x, flush=True)


# ── fit ──────────────────────────────────────────────────────────────────────────────────────────
def training_frame() -> tuple[pd.DataFrame, bool]:
    fit = cv.table("cells_fit", NEED + ["w_sample"])
    terr = P.TABLE_DIR / "terrain.parquet"
    T = pd.read_parquet(terr).set_index("row_id")[cv.TERRAIN]
    return fit.join(T, on="row_id"), True


def fit_models(fit: pd.DataFrame) -> tuple[dict, dict]:
    cv.assert_allowed(R2_FEATURES)
    assert len(R2_FEATURES) == 13 and "hazshare" not in R2_FEATURES, R2_FEATURES
    groups = fit.fire_key.astype(str) + "/" + fit.epoch.astype(str)
    dist = L.fit_rung1(fit, C_DIST, features=["d_act_km"])
    r2 = L.fit_rung2(fit, C2, features=R2_FEATURES); lin = []
    X2, _ = L.design(fit, R2_FEATURES, r2["med"])
    ale = {}
    for col, sgn in L.MONOTONE.items():
        if col in X2 and X2[col].nunique() > 6:
            sp = L.ale(lambda X: r2["model"].margin(L.spline_design(X, r2["spl"], r2["linear"])[0][r2["cols"]]), X2, col)["spearman"]
            ale[col] = sp
            if np.isfinite(sp) and np.sign(sp) != sgn:
                lin.append(col)
    if lin:
        r2 = L.fit_rung2(fit, C2, linear_cols=tuple(lin), features=R2_FEATURES)
    hyb = L.fit_rung2(fit, C2, linear_cols=tuple(lin), offset=fit.m8_log_frozen.to_numpy(float), features=R2_FEATURES)
    models = dict(DIST=dist, RUNG2=r2, HYBRID=hyb)
    summary = dict(arm=ARM, C2=C2, C_dist=C_DIST, features_rung2=R2_FEATURES, linearised=lin, monotone_ale_spearman=ale,
                   n_rows=int(len(fit)), n_positives=int(fit.y.sum()), n_fires=int(fit.fire_key.nunique()),
                   n_intervals=int(groups.nunique()),
                   offset="m8_log_frozen (frozen fit_beta.json, PREREG_ML_EMBER_DEV section 2) on both the training rows and the decision fires",
                   coefficients={m: dict(zip(models[m]["cols"], [float(v) for v in models[m]["model"].coef_])) for m in models},
                   converged={m: bool(getattr(models[m]["model"], "converged_", True)) for m in models})
    return models, summary


def cmd_fit(a) -> int:
    t0 = time.time(); log = log_factory(t0)
    STAGE2.mkdir(parents=True, exist_ok=True)
    fit, _ = training_frame()
    log(f"training rows {len(fit)} positives {int(fit.y.sum())} fires {fit.fire_key.nunique()}")
    models, summary = fit_models(fit)
    pk = STAGE2 / "models.pkl"
    pk.write_bytes(pickle.dumps(models, protocol=5))
    import lightgbm, scipy, sklearn  # noqa: E401
    summary.update(models_pkl_sha256=sha256(pk), fitted_utc=pd.Timestamp.now("UTC").isoformat(),
                   inputs=dict(cells_fit=sha256(P.TABLE_DIR / "cells_fit.parquet"), terrain=sha256(P.TABLE_DIR / "terrain.parquet"),
                               fit_beta_frozen=P.FIT_BETA_SHA256),
                   code=dict(ladder=sha256(ROOT / "scripts/ml_ember/ladder.py"), confirm=sha256(Path(__file__)),
                             features=sha256(ROOT / "scripts/ml_ember/features.py"), cv=sha256(ROOT / "scripts/ml_ember/cv.py")),
                   environment=dict(python=sys.version.split()[0], numpy=np.__version__, scipy=scipy.__version__,
                                    sklearn=sklearn.__version__, lightgbm=lightgbm.__version__, pandas=pd.__version__),
                   wall_seconds=time.time() - t0)
    (STAGE2 / "models.json").write_text(json.dumps(summary, indent=1, default=float))
    log("models.pkl sha256", summary["models_pkl_sha256"], "linearised", summary["linearised"])
    return 0


# ── features (decision fires) ───────────────────────────────────────────────────────────────────
def build_fire(args) -> dict:
    """Worker: one fire -> feature rows of its jump intervals (cell level) + accounting + npz verification."""
    fire_key, epochs_wanted, confirmation_commit, assignment = args
    from scripts.ml_ember import terrain as TR
    guard = Guard(confirmation_commit=confirmation_commit)
    out = dict(fire_key=fire_key, status="ok", cells=None, intervals=[], verify={}, terrain=None, n_intervals=0)
    fd = P.POOL / "fires" / fire_key.replace(":", "_")
    try:
        beta = F.load_beta(guard)
        done = guard.read_json(fd / "intervals" / "_done.json")
        meta = guard.read_json(fd / "meta.json")
        if done.get("status") != "ok":
            out.update(status=f"intervals_status_{done.get('status')}"); return out
        bnx_done = int(done["block_shape"][1])
        ctx = F.load_fire(fire_key, guard, beta=beta, with_texture=False, assignment=assignment)
        if ctx is None:
            out.update(status="no_detections_or_ineligible"); return out
        g = meta["grid"]; bnx = g["nx"] // C.CAND_F
        if bnx != bnx_done:
            out.update(status=f"block_shape_mismatch {bnx} vs {bnx_done}"); return out
        try:
            T = TR.terrain_grid(meta); out["terrain"] = "ok"
        except Exception as e:  # noqa: BLE001
            T = None; out["terrain"] = f"failed: {e}"
        worst = {k: 0.0 for k in F.NPZ_FIELDS}
        parts = []
        for ip in F.iter_intervals(ctx, guard):
            out["n_intervals"] += 1
            for k, v in ip.verify().items():
                worst[k] = max(worst[k], v)
            if epochs_wanted is not None and ip.epoch not in epochs_wanted:
                continue
            n = ip.n
            cells = pd.DataFrame({"fire_key": fire_key, "epoch": np.full(n, ip.epoch, np.int32),
                                  "cell_id": ip.bj.astype(np.int64) * bnx + ip.bi.astype(np.int64), **ip.cells})
            if T is not None:
                bj = np.clip(ip.bj, 0, T["nby"] - 1); bi = np.clip(ip.bi, 0, T["nbx"] - 1)
                cells["elev_m"] = T["elev"][bj, bi]; cells["slope_pct"] = T["slope"][bj, bi]
                cells["aspect_deg"] = T["aspect"][bj, bi]; cells["tpi_1km"] = T["tpi"][bj, bi]; cells["terrain_fill"] = T["fill"][bj, bi]
            else:
                for c in cv.TERRAIN:
                    cells[c] = np.nan
                cells["terrain_fill"] = np.int8(1)
            parts.append(cells)
            out["intervals"].append({**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in ip.interval.items()}})
        out["verify"] = worst
        out["cells"] = pd.concat(parts, ignore_index=True) if parts else None
    except Exception as e:  # noqa: BLE001
        out.update(status=f"error: {type(e).__name__}: {e}")
    return out


def read_jumps(guard: Guard, final: Path) -> pd.DataFrame:
    p = guard.check(final / "jumps.csv")
    J = pd.read_csv(p)
    if "out_final_m" not in J.columns:
        raise RuntimeError("jumps.csv without out_final_m: refused (PREREG_LR8 A1)")
    return J


def wanted_epochs(J: pd.DataFrame) -> dict[str, set[int]]:
    """Fires and epochs that hold a ranked candidate jump (cell_id >= 0, interval numeric)."""
    q = J[(J.cell_id >= 0) & J.interval.astype(str).str.fullmatch(r"\d+")]
    return {fk: set(int(x) for x in g.interval.astype(int)) for fk, g in q.groupby("fire_key")}


def build_cells(fire_epochs: dict[str, set[int] | None], confirmation_commit: str | None, assignment: str, workers: int, log) -> tuple[pd.DataFrame, dict]:
    from multiprocessing import get_context
    jobs = [(k, fire_epochs[k], confirmation_commit, assignment) for k in sorted(fire_epochs)]
    parts, report = [], dict(fires=len(jobs), ok=[], failed={}, verify_worst={}, terrain_failed=[], intervals=[], n_intervals_iterated=0)
    def take(r):
        if r["status"] != "ok":
            report["failed"][r["fire_key"]] = r["status"]; log(r["fire_key"], "FAILED", r["status"]); return
        report["ok"].append(r["fire_key"]); report["n_intervals_iterated"] += r["n_intervals"]
        if r["terrain"] != "ok":
            report["terrain_failed"].append((r["fire_key"], r["terrain"]))
        for k, v in r["verify"].items():
            report["verify_worst"][k] = max(report["verify_worst"].get(k, 0.0), v)
        report["intervals"].extend(r["intervals"])
        if r["cells"] is not None:
            parts.append(r["cells"])
        log(f"{r['fire_key']} ok: {len(r['intervals'])} jump intervals, {0 if r['cells'] is None else len(r['cells'])} cells, verify {max(r['verify'].values()) if r['verify'] else 'n/a'}")
    if workers > 1:
        with get_context("spawn").Pool(workers, maxtasksperchild=1) as pool:
            for r in pool.imap_unordered(build_fire, jobs):
                take(r)
    else:
        for j in jobs:
            take(build_fire(j))
    cells = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return cells, report


def cmd_features(a) -> int:
    t0 = time.time(); log = log_factory(t0)
    if not a.confirmation_commit:
        raise SystemExit("features: --confirmation-commit <sha> is required (PREREG_ML_EMBER section 4)")
    guard = Guard(confirmation_commit=a.confirmation_commit)
    J = read_jumps(guard, FINAL)
    fe = wanted_epochs(J)
    log(f"decision fires with ranked candidate jumps: {len(fe)}; jump intervals {sum(len(v) for v in fe.values())}")
    cells, report = build_cells(fe, a.confirmation_commit, "decision", a.workers, log)
    STAGE2.mkdir(parents=True, exist_ok=True)
    out = STAGE2 / "decision_cells.parquet"
    cells.to_parquet(out, index=False)
    report.update(rows=int(len(cells)), sha256=sha256(out), built_utc=pd.Timestamp.now("UTC").isoformat(), wall_seconds=time.time() - t0,
                  confirmation_commit=a.confirmation_commit, jumps_csv_sha256=sha256(FINAL / "jumps.csv"))
    (STAGE2 / "decision_build.json").write_text(json.dumps(report, indent=1, default=str))
    log(f"wrote {out} rows {len(cells)}; failed fires {len(report['failed'])}; terrain failed {len(report['terrain_failed'])}; npz verify worst {report['verify_worst']}")
    return 0


# ── score ────────────────────────────────────────────────────────────────────────────────────────
def score_cells(models: dict, cells: pd.DataFrame) -> pd.DataFrame:
    s = cells[["fire_key", "epoch", "cell_id"]].copy()
    s["DIST"] = L.score_rung1(models["DIST"], cells)
    s["RUNG2"] = L.score_rung2(models["RUNG2"], cells)
    s["HYBRID"] = L.score_rung2(models["HYBRID"], cells, offset=cells.m8_log_frozen.to_numpy(float))
    s["m8_log_frozen"] = cells.m8_log_frozen.to_numpy(float); s["b1"] = cells.b1.to_numpy(float)
    return s


def align_to_candidates(scored: pd.DataFrame, cand_loader, log) -> tuple[dict, dict]:
    """Attach our scores to v8's candidate arrays (same cell order). Any cell-set or M8 mismatch is a halt."""
    cands, issues = {}, dict(intervals=0, cell_set_mismatch=[], m8_mismatch=[], missing_candidate_file=[])
    for (fk, ep), g in scored.groupby(["fire_key", "epoch"], sort=False):
        z = cand_loader(fk, int(ep))
        if z is None:
            issues["missing_candidate_file"].append((fk, int(ep))); continue
        issues["intervals"] += 1
        ours = g.set_index("cell_id")
        if len(ours) != len(z["cell_id"]) or not np.array_equal(np.sort(ours.index.to_numpy()), np.sort(z["cell_id"])):
            issues["cell_set_mismatch"].append((fk, int(ep), int(len(ours)), int(len(z["cell_id"])))); continue
        ours = ours.loc[z["cell_id"]]
        d = float(np.max(np.abs(ours.m8_log_frozen.to_numpy() - np.log(np.maximum(z["score_M8"].astype(float), 1e-300)))))
        if not d <= 1e-4:
            issues["m8_mismatch"].append((fk, int(ep), d))
        arr = {k: np.asarray(z[k]) for k in z.files} if hasattr(z, "files") else dict(z)
        for m in ("DIST", "RUNG2", "HYBRID"):
            arr[f"score_{m}"] = ours[m].to_numpy(float)
        cands[(fk, str(int(ep)))] = arr
    return cands, issues


def pair_stats(pf: dict, m_a: str, m_b: str, seed_test=V.S_TEST, seed_boot=V.S_BOOT) -> dict:
    fires = sorted(f for f in pf if pf[f]["r"].get(m_a) and pf[f]["r"].get(m_b) and len(pf[f]["r"][m_a]) == len(pf[f]["r"][m_b]))
    if not fires:
        return dict(n_fires=0)
    region = np.array([pf[f]["region"] for f in fires])
    K = np.array([len(pf[f]["r"][m_a]) for f in fires], float)
    a = np.array([float(np.mean(np.array(pf[f]["r"][m_a]) - np.array(pf[f]["r"][m_b]))) for f in fires])
    w = V.weights(K, "S3")
    est = float((w * a).sum() / w.sum())
    out = dict(S3=est, p_signflip=V.signflip_p(a, w, region, seed=seed_test), boot95=V.boot_ci(a, w, region, seed=seed_boot),
               n_fires=len(fires), K=int(K.sum()), share_fires_positive=float(np.mean(a > 0)),
               by_region={r: float((w[region == r] * a[region == r]).sum() / w[region == r].sum()) for r in np.unique(region)},
               S1=float(a.mean()), S2=float((K * a).sum() / K.sum()))
    return out


def model_auc(pf: dict, m: str) -> dict:
    fires = sorted(f for f in pf if pf[f]["r"].get(m))
    region = np.array([pf[f]["region"] for f in fires]); K = np.array([len(pf[f]["r"][m]) for f in fires], float)
    auc = np.array([float(np.mean(pf[f]["r"][m])) for f in fires]); w = V.weights(K, "S3")
    return dict(AUC=float((w * auc).sum() / w.sum()), by_region={r: float((w[region == r] * auc[region == r]).sum() / w[region == r].sum()) for r in np.unique(region)},
                n_fires=len(fires), K=int(K.sum()))


def strata_tables(cands: dict, jumps: pd.DataFrame, intervals: pd.DataFrame | None, pairs) -> dict:
    """Per-jump ranks for every model, then S3-style pair differences by gap class, distance band and active-share tercile."""
    rows = []
    for j in jumps.itertuples():
        if V.final_class(j.out_final_m) != "positive":
            continue
        c = cands.get((j.fire_key, str(j.interval)))
        if c is None:
            continue
        pos = np.where(c["cell_id"] == j.cell_id)[0]
        if not len(pos):
            continue
        r = dict(fire_key=j.fire_key, region=j.region, interval=str(j.interval), cell_id=int(j.cell_id), dist_m=float(j.dist_m), gap=j.gap, year=int(j.year))
        for m in MODELS + ["ELMFIRE"]:
            sc = c.get(f"score_{m}")
            r[f"r_{m}"] = float(V.pct_rank(sc, sc[pos[:1]])[0]) if sc is not None else np.nan
        rows.append(r)
    R = pd.DataFrame(rows)
    if intervals is not None and len(intervals):
        iv = intervals.assign(interval=intervals.epoch.astype(int).astype(str))[["fire_key", "interval", "act_share", "gap_h"]]
        R = R.merge(iv, on=["fire_key", "interval"], how="left")
        try:
            R["act_share_tercile"] = pd.qcut(R.act_share.rank(method="first"), 3, labels=["low", "mid", "high"]).astype(str)
        except ValueError:
            R["act_share_tercile"] = "n/a"
    R["dist_band_km"] = pd.cut(R.dist_m / 1000.0, [0, 3, 7, 15.5], labels=["1-3", "3-7", "7-15"]).astype(str)
    out = dict(n_ranked=int(len(R)))
    for strat in [c for c in ("gap", "dist_band_km", "act_share_tercile", "region", "year") if c in R]:
        out[strat] = {}
        for val, g in R.groupby(strat):
            d = {"n": int(len(g))}
            for m in MODELS + ["ELMFIRE"]:
                col = f"r_{m}"
                if col in g and g[col].notna().any():
                    d[f"AUC_{m}"] = float(g[col].mean())
            for m_a, m_b in pairs:
                if f"r_{m_a}" in g and f"r_{m_b}" in g:
                    dd = (g[f"r_{m_a}"] - g[f"r_{m_b}"]).dropna()
                    d[f"{m_a}-{m_b}"] = float(dd.mean()) if len(dd) else None
            out[strat][str(val)] = d
    return out, R


def evaluate(cands: dict, jumps_rows: list, counts, intervals: pd.DataFrame | None, label: str) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    models = MODELS + (["ELMFIRE"] if any("score_ELMFIRE" in c for c in cands.values()) else [])
    acc = {}
    pf = V.per_fire(cands, jumps_rows, models, accounting=acc)
    res = dict(label=label, models=models, accounting=V.accounting_summary(acc), auc={m: model_auc(pf, m) for m in models},
               deciding={}, descriptive={}, reference_elmfire={})
    for rq, (m_a, m_b) in DECIDING.items():
        st = pair_stats(pf, m_a, m_b)
        st["pass"] = bool(st.get("n_fires", 0) > 0 and st["S3"] > 0 and st["p_signflip"] < V.ALPHA)
        res["deciding"][rq] = dict(comparison=f"{m_a} - {m_b}", **st)
    for m_a, m_b in DESCRIPTIVE:
        res["descriptive"][f"{m_a}-{m_b}"] = pair_stats(pf, m_a, m_b)
    if "ELMFIRE" in models:
        for m_a in ("RUNG2", "HYBRID", "M8"):
            res["reference_elmfire"][f"{m_a}-ELMFIRE"] = pair_stats(pf, m_a, "ELMFIRE")
    jumps_df = pd.DataFrame(jumps_rows, columns=["fire_key", "region", "interval", "cell_id", "out_final_m"])
    jx = pd.DataFrame(jumps_rows, columns=["fire_key", "region", "interval", "cell_id", "out_final_m"])
    res["strata"], R = strata_tables(cands, _jumps_with_meta(jumps_df, counts), intervals, list(DECIDING.values()) + DESCRIPTIVE)
    per_fire = pd.DataFrame([dict(fire_key=f, region=pf[f]["region"], K=len(pf[f]["r"]["M8"]),
                                  **{f"auc_{m}": float(np.mean(pf[f]["r"][m])) if pf[f]["r"].get(m) else np.nan for m in models})
                             for f in sorted(pf)])
    del jx
    return res, R, per_fire


def _jumps_with_meta(jumps_df: pd.DataFrame, meta_df) -> pd.DataFrame:
    """Join dist_m, gap, year from the full jumps table (meta_df) when present; else fill."""
    if isinstance(meta_df, pd.DataFrame) and {"dist_m", "gap", "year"} <= set(meta_df.columns):
        m = meta_df[["fire_key", "interval", "cell_id", "dist_m", "gap", "year"]].copy(); m["interval"] = m.interval.astype(str)
        j = jumps_df.copy(); j["interval"] = j.interval.astype(str)
        return j.merge(m, on=["fire_key", "interval", "cell_id"], how="left")
    j = jumps_df.copy(); j["dist_m"] = np.nan; j["gap"] = "n/a"; j["year"] = 0
    return j


def load_models(expect_sha: str | None) -> dict:
    pk = STAGE2 / "models.pkl"
    sha = sha256(pk)
    rec = json.loads((STAGE2 / "models.json").read_text())["models_pkl_sha256"]
    if sha != rec:
        raise SystemExit(f"models.pkl sha256 {sha} != models.json {rec}: refused")
    if expect_sha and sha != expect_sha:
        raise SystemExit(f"models.pkl sha256 {sha} != registered {expect_sha}: refused")
    return pickle.loads(pk.read_bytes())


def cmd_score(a) -> int:
    t0 = time.time(); log = log_factory(t0)
    if not a.confirmation_commit:
        raise SystemExit("score: --confirmation-commit <sha> is required (PREREG_ML_EMBER section 4)")
    out_json = STAGE2 / "confirmation.json"
    if out_json.exists() and not a.force_rescore:
        raise SystemExit(f"{out_json} exists: the confirmation has run once; --force-rescore is recorded if used")
    guard = Guard(confirmation_commit=a.confirmation_commit)
    models = load_models(a.expect_models_sha256)
    cells = pd.read_parquet(STAGE2 / "decision_cells.parquet")
    build = json.loads((STAGE2 / "decision_build.json").read_text())
    if build.get("confirmation_commit") != a.confirmation_commit:
        raise SystemExit("decision_cells.parquet was built under a different confirmation commit: refused")
    log(f"scoring {len(cells)} decision cells in {cells.groupby(['fire_key', 'epoch']).ngroups} intervals")
    scored = score_cells(models, cells)
    def cand_loader(fk, ep):
        p = FINAL / "candidates" / f"{fk}__{ep}.npz"
        if not p.exists():
            return None
        return np.load(guard.check(p))
    cands, issues = align_to_candidates(scored, cand_loader, log)
    halt = bool(issues["cell_set_mismatch"] or issues["m8_mismatch"] or issues["missing_candidate_file"])
    (STAGE2 / "alignment.json").write_text(json.dumps(issues, indent=1, default=str))
    if halt:
        log("HALT before any S3: candidate alignment failed", {k: (v if isinstance(v, int) else len(v)) for k, v in issues.items()})
        return 2
    J = read_jumps(guard, FINAL)
    jumps_rows = [(r.fire_key, r.region, str(r.interval), int(r.cell_id), float(r.out_final_m)) for r in J.itertuples()]
    intervals = pd.DataFrame(build["intervals"]) if build.get("intervals") else None
    res, R, per_fire = evaluate(cands, jumps_rows, J, intervals, label="stage-2 confirmation (one shot)")
    res.update(confirmation_commit=a.confirmation_commit, scored_utc=pd.Timestamp.now("UTC").isoformat(), force_rescore=bool(a.force_rescore),
               models_pkl_sha256=sha256(STAGE2 / "models.pkl"), decision_cells_sha256=sha256(STAGE2 / "decision_cells.parquet"),
               jumps_csv_sha256=sha256(FINAL / "jumps.csv"), alignment=dict(intervals=issues["intervals"]), wall_seconds=time.time() - t0,
               seeds=dict(S_TEST=V.S_TEST, S_BOOT=V.S_BOOT, flips=V.N_FLIPS, boot=V.N_BOOT))
    out_json.write_text(json.dumps(res, indent=1, default=float))
    R.to_csv(STAGE2 / "ranks_per_jump.csv", index=False); per_fire.to_csv(STAGE2 / "per_fire.csv", index=False)
    for rq, d in res["deciding"].items():
        log(f"{rq} {d['comparison']}: S3 {d.get('S3', float('nan')):+.4f} p {d.get('p_signflip', float('nan')):.2e} 95% {d.get('boot95')} -> {'PASS' if d['pass'] else 'FAIL'}")
    log("M8 - B1 (should reproduce [R70] +0.147):", res["descriptive"]["M8-B1"].get("S3"))
    return 0


# ── dry run (training fires, in sample) ─────────────────────────────────────────────────────────
def cmd_dryrun(a) -> int:
    t0 = time.time(); log = log_factory(t0)
    fires = cv.table("fires")
    keys = a.fires or sorted(fires.fire_key[fires.n_jumps > 0])[:5]
    guard = Guard()
    for k in keys:
        guard.assert_training(k)
    models = load_models(None)
    # labels: the frozen training catalogue matched to candidate cells (the builder's own matching, cells_eval y)
    ev = cv.table("cells_eval", ["row_id", "fire_key", "epoch", "y"])
    ev = ev[ev.fire_key.isin(keys) & (ev.y == 1)]
    wanted = {k: set(int(e) for e in ev.epoch[ev.fire_key == k]) for k in keys}
    cells, report = build_cells(wanted, None, "training", a.workers, log)
    coords = cv.table("coords_control", ["row_id", "fire_key", "epoch", "bi", "bj"])
    # positives -> cell_id through the control coordinates (bnx from the meta grid)
    rows = []
    for k in keys:
        meta = guard.read_json(P.POOL / "fires" / k.replace(":", "_") / "meta.json"); bnx = meta["grid"]["nx"] // C.CAND_F
        reg = "CAN" if int(fires.region[fires.fire_key == k].iloc[0]) == F.REGION_CODE["CAN"] else "US"
        pc = coords.merge(ev[ev.fire_key == k], on=["row_id", "fire_key", "epoch"])
        for r in pc.itertuples():
            rows.append((k, reg, str(int(r.epoch)), int(r.bj) * bnx + int(r.bi), 0.0))
    scored = score_cells(models, cells)
    def cand_loader(fk, ep):
        g = scored[(scored.fire_key == fk) & (scored.epoch == ep)]
        return dict(cell_id=g.cell_id.to_numpy(), score_M8=np.exp(g.m8_log_frozen.to_numpy()), score_B1=g.b1.to_numpy())
    cands, issues = align_to_candidates(scored, cand_loader, log)
    assert not issues["cell_set_mismatch"] and not issues["m8_mismatch"], issues
    res, R, per_fire = evaluate(cands, rows, None, pd.DataFrame(report["intervals"]) if report["intervals"] else None,
                                label="DRY RUN on training fires, in sample: a smoke test, never a result")
    res.update(fires=keys, build=dict(ok=report["ok"], failed=report["failed"], verify_worst=report["verify_worst"], terrain_failed=report["terrain_failed"]),
               wall_seconds=time.time() - t0)
    d = STAGE2 / "dryrun"; d.mkdir(parents=True, exist_ok=True)
    (d / "dryrun.json").write_text(json.dumps(res, indent=1, default=float)); R.to_csv(d / "ranks_per_jump.csv", index=False)
    log("dry run done:", {rq: (round(v.get("S3", float("nan")), 3), v["pass"]) for rq, v in res["deciding"].items()},
        "ranked", res["accounting"]["total"]["ranked"], "verify", report["verify_worst"])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fit")
    d = sub.add_parser("dryrun"); d.add_argument("--fires", nargs="*", default=None); d.add_argument("--workers", type=int, default=1)
    f = sub.add_parser("features"); f.add_argument("--confirmation-commit", default=None); f.add_argument("--workers", type=int, default=2)
    s = sub.add_parser("score"); s.add_argument("--confirmation-commit", default=None); s.add_argument("--expect-models-sha256", default=None)
    s.add_argument("--force-rescore", action="store_true")
    a = ap.parse_args(argv)
    return dict(fit=cmd_fit, dryrun=cmd_dryrun, features=cmd_features, score=cmd_score)[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
