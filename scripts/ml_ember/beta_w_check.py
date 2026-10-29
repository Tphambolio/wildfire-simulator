"""PREREG_ML_EMBER_DEV section 9: the β_w check (RQ2b), training split only; plus positive control 7 of section 8.

Fits only M8's own coefficients (b0, b_w, b_L; optionally a receptor-group factor κ_g) on the sufficient
statistics of every valid training interval, exactly fit.py's penalised Poisson likelihood (same priors
b_w ~ N(1,1), b_L ~ N(ln 0.5, 1), b_m fixed) re-expressed in vectorised form and checked against fit.py's own
objective at the fitted point. Steps (section 9 order):
  1. unbounded refit: b_w free in (-5, 5) with the v8 priors, and without priors; profile-likelihood 95 % interval;
     then κ_g free per receptor fuel group (grass reference) with b_w free;
  2. between-interval count model: n_jumps_i ~ Poisson(exp(c + γ ln(1 + U10_i)) Λ_i), Λ_i = Σ_c λ_c at b_w = 0;
     overall and by gap class (short < 4 h / long), interval length (< 8 h vs 8-12 h) and region;
  3. label sensitivities: 2 km minimum distance; "possible jumps" (line-check-removed clusters within 1-15 km
     re-included); the strict rule cannot be formed (the catalogue has no never_detected column: reported as such);
  4. ERA5 wind arm: not run (no ERA5 series in the pool; reported as pending);
  5. positive control 7: synthetic counts from the training intervals' own A arrays with b_w = 1, b0 set so the
     expected total equals the observed positives, 5 replicates (seed S_CTRL + r), fit.py unchanged
     (bounded, with priors) must return b_w ≈ 1 within its profile interval;
  6. (ML read-out: after the ladder, not here.)
Output: reports/data/ml-ember-cast-2026-10-29/beta_w/beta_w_check.json; cache table/m8_suffstats.parquet.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "engine" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ml_ember import cv  # noqa: E402
from scripts.ml_ember import paths as P  # noqa: E402
from scripts.ml_ember.loader import default_guard  # noqa: E402
from scripts.validation.v8 import common as C  # noqa: E402
from scripts.validation.v8 import fit as F  # noqa: E402
from scripts.validation.v8 import intervals as Iv  # noqa: E402

OUT = P.DATA_FOLDER / "beta_w"
WAF = Iv.WAF.astype(float)
B_M = F.B_M
GROUPS = (-1, 0, 1, 2)


# ── receptor fuel group of every candidate cell (the §4 fuel_group) ─────────────────────────────
def fuel_group_fn(guard):
    from scripts.ml_ember import features as Fe
    cache = {}
    def fn(fire_key, z):
        if fire_key not in cache:
            ctx = Fe.load_fire(fire_key, guard, with_texture=False)
            cache.clear(); cache[fire_key] = ctx
        ctx = cache[fire_key]
        cj = np.clip(z["bj"].astype(int) * C.CAND_F + 1, 0, ctx.code.shape[0] - 1)
        ci = np.clip(z["bi"].astype(int) * C.CAND_F + 1, 0, ctx.code.shape[1] - 1)
        us = None if ctx.us_group is None else ctx.us_group[cj, ci]
        return Iv.waf_class(ctx.code[cj, ci], us)
    return fn


# ── vectorised likelihood ─────────────────────────────────────────────────────────────────────────
class Lik:
    """Rows: suffstats per (interval, zone, group): S[n,2,3], u[n], m[n], g[n]; positives A[p,2,3], u_p, m_p, g_p."""

    def __init__(self, ss: pd.DataFrame, pos: pd.DataFrame):
        self.S = np.stack([ss[["S_s0", "S_s1", "S_s2"]].to_numpy(float), ss[["S_l0", "S_l1", "S_l2"]].to_numpy(float)], axis=1)
        self.u, self.m, self.g = ss.u10.to_numpy(float), ss.m_ff.to_numpy(float), ss.fuel_group.to_numpy(int)
        self.A = np.stack([pos[["A_s0", "A_s1", "A_s2"]].to_numpy(float), pos[["A_l0", "A_l1", "A_l2"]].to_numpy(float)], axis=1)
        self.up, self.mp, self.gp = pos.u10_ms.to_numpy(float), pos.m_ff_pct.to_numpy(float), pos.fuel_group.to_numpy(int)

    @staticmethod
    def _lam(b_w, b_L, A, u, m, kappa, g):
        t = (np.exp(b_w * np.log1p(u[:, None] * WAF[None, :])) * (A[:, 0, :] + math.exp(b_L) * A[:, 1, :])).sum(1)
        k = np.zeros(len(g)) if kappa is None else np.array([kappa.get(int(x), 0.0) for x in g])
        return t * np.exp(B_M * (m - 21.0) + k)

    def nll(self, b0, b_w, b_L, kappa=None, prior=True):
        lam_p = self._lam(b_w, b_L, self.A, self.up, self.mp, kappa, self.gp) * math.exp(b0)
        tot = self._lam(b_w, b_L, self.S, self.u, self.m, kappa, self.g).sum() * math.exp(b0)
        ll = np.log(np.maximum(lam_p, 1e-300)).sum() - tot
        if prior:
            ll -= 0.5 * ((b_w - F.PRIOR["b_w"][0]) / F.PRIOR["b_w"][1]) ** 2
            ll -= 0.5 * ((b_L - F.PRIOR["b_L"][0]) / F.PRIOR["b_L"][1]) ** 2
        return -ll

    def fit(self, b_w_bounds=(0.0, 10.0), prior=True, kappa_groups=(), fixed_bw=None):
        kg = list(kappa_groups)
        def unpack(v):
            b0, bw, bL = v[0], (fixed_bw if fixed_bw is not None else v[1]), v[2]
            kap = {g: v[3 + i] for i, g in enumerate(kg)} if kg else None
            return b0, bw, bL, kap
        x0 = [-20.0, 1.0 if fixed_bw is None else fixed_bw, math.log(0.5)] + [0.0] * len(kg)
        bounds = [(None, None), b_w_bounds if fixed_bw is None else (fixed_bw, fixed_bw), (-10.0, 0.0)] + [(-6, 6)] * len(kg)
        res = minimize(lambda v: self.nll(*unpack(v), prior=prior), x0, method="L-BFGS-B", bounds=bounds)
        b0, bw, bL, kap = unpack(res.x)
        return dict(b0=float(b0), b_w=float(bw), b_L=float(bL), kappa={str(k): float(v) for k, v in (kap or {}).items()},
                    nll=float(res.fun), converged=bool(res.success), message=str(res.message))

    def profile_bw(self, grid, prior=True, kappa_groups=()):
        """Profile negative log-likelihood over b_w; 95 % interval where 2(nll - min) <= 3.841."""
        prof = []
        for bw in grid:
            r = self.fit(prior=prior, kappa_groups=kappa_groups, fixed_bw=float(bw))
            prof.append(r["nll"])
        prof = np.array(prof); lo = prof.min()
        inside = grid[2 * (prof - lo) <= 3.841]
        return dict(grid=[float(x) for x in grid], nll=[float(x) for x in prof], bw_hat=float(grid[prof.argmin()]),
                    ci95=(float(inside.min()), float(inside.max())), at_bound=bool(inside.min() <= grid[0] or inside.max() >= grid[-1]))


def collect_catalogue(J: pd.DataFrame, ss: pd.DataFrame, guard, fuel_group) -> pd.DataFrame:
    """Positives (A, u10, m_ff, fuel_group) for an arbitrary catalogue variant, matched like fit.collect."""
    rows = []; by_end = {(r.fire_key, r.t_end): (r.epoch) for r in ss.drop_duplicates(["fire_key", "epoch"]).itertuples()}
    for fk, g in J.groupby("fire_key"):
        if fk not in set(ss.fire_key):
            continue
        fd = C.FIRES / fk.replace(":", "_"); meta = guard.read_json(fd / "meta.json")
        for r in g.itertuples():
            ep = by_end.get((fk, pd.Timestamp(r.t_end)))
            if ep is None:
                continue
            z = guard.load_npz(fd / "intervals" / f"{ep:05d}.npz")
            bj, bi = Iv.block_of(meta, r.x_utm, r.y_utm)
            hit = np.where((z["bj"] == bj) & (z["bi"] == bi))[0]
            if not len(hit):
                continue
            h = hit[0]; zi = int(z["zone"][h]); A = z["A"][:, :, h].astype(float); grp = int(fuel_group(fk, z)[h])
            rows.append(dict(fire_key=fk, epoch=ep, A_s0=A[0, 0], A_s1=A[0, 1], A_s2=A[0, 2], A_l0=A[1, 0], A_l1=A[1, 1], A_l2=A[1, 2],
                             u10_ms=float(z["u10"][zi]), m_ff_pct=float(z["m_ff"][zi]), fuel_group=grp))
    return pd.DataFrame(rows)


def main() -> int:
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True)
    log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
    guard = default_guard(); fg = fuel_group_fn(guard)
    fires = cv.table("fires"); keys = fires.fire_key.tolist()
    sp = cv.suffstats_path()
    if sp.exists():
        ss = pd.read_parquet(sp)
    else:
        log("building sufficient statistics for", len(keys), "fires")
        ss = cv.build_suffstats(guard, keys, fg); ss.to_parquet(sp, index=False)
    log("suffstats rows", len(ss), "intervals", ss.groupby(["fire_key", "epoch"]).ngroups)
    pos = cv.table("cells_fit"); pos = pos[pos.y == 1][["fire_key", "epoch", "A_s0", "A_s1", "A_s2", "A_l0", "A_l1", "A_l2", "u10_ms", "m_ff_pct", "fuel_group", "d_all_km"]].reset_index(drop=True)
    L = Lik(ss, pos)
    out = dict(started_utc=pd.Timestamp.utcnow().isoformat(), n_fires=len(keys), n_positives=int(len(pos)),
               n_intervals=int(ss.groupby(["fire_key", "epoch"]).ngroups), prior=F.PRIOR, b_m=B_M)

    # consistency with fit.py at the v8 configuration
    S_rows, P_ = cv.to_fit_inputs(ss, pos)
    b_v8, res = F.fit(S_rows, P_, full=True)
    nll_fitpy = float(res.fun); nll_vec = L.nll(b_v8["b0"], b_v8["b_w"], b_v8["b_L"], prior=True)
    out["v8_replica"] = dict(beta=b_v8, nll_fitpy=nll_fitpy, nll_vectorised=nll_vec, abs_diff=abs(nll_fitpy - nll_vec),
                             frozen_fit_beta=guard.read_json(C.DATA / "fit" / "fit_beta.json")["beta"])
    log("v8 replica", b_v8, "nll diff", abs(nll_fitpy - nll_vec))

    # 1 unbounded refits + profiles
    grid = np.round(np.arange(-5.0, 5.0001, 0.1), 2)
    out["step1_unbounded_prior"] = dict(fit=L.fit(b_w_bounds=(-5, 5), prior=True), profile=L.profile_bw(grid, prior=True))
    out["step1_unbounded_noprior"] = dict(fit=L.fit(b_w_bounds=(-5, 5), prior=False), profile=L.profile_bw(grid, prior=False))
    log("step1", out["step1_unbounded_prior"]["fit"], out["step1_unbounded_prior"]["profile"]["ci95"])
    kg = [g for g in GROUPS if g != 0 and (ss.fuel_group == g).any()]
    out["step1_kappa_free"] = dict(fit=L.fit(b_w_bounds=(-5, 5), prior=True, kappa_groups=kg),
                                   profile=L.profile_bw(np.round(np.arange(-5.0, 5.0001, 0.25), 2), prior=True, kappa_groups=kg),
                                   kappa_reference="group 0 (grass / unsheltered) = 0; groups: -1 non-fuel centre, 1 D-M, 2 C-timber")
    log("kappa", out["step1_kappa_free"]["fit"])

    # 2 between-interval count model
    b_fixed = L.fit(prior=True, fixed_bw=0.0)
    iv = cv.table("intervals")
    lam0 = Lik._lam(0.0, b_fixed["b_L"], L.S, L.u, L.m, None, L.g) * math.exp(b_fixed["b0"])
    Lam = pd.Series(lam0).groupby([ss.fire_key.to_numpy(), ss.epoch.to_numpy()]).sum()
    U = ss.groupby(["fire_key", "epoch"]).apply(lambda g: float((g.u10 * g.S_s0.add(g.S_s1).add(g.S_s2).add(g.S_l0).add(g.S_l1).add(g.S_l2)).sum() / max(g[["S_s0", "S_s1", "S_s2", "S_l0", "S_l1", "S_l2"]].sum().sum(), 1e-300)))
    d = iv.set_index(["fire_key", "epoch"]).join(Lam.rename("Lam")).join(U.rename("U10w")).dropna(subset=["Lam"]).reset_index()
    d["gap_class"] = np.where(d.gap_h < 4, "short(<4h)", "long(>=4h)"); d["len_class"] = np.where(d.gap_h < 8, "<8h", "8-12h")
    def count_model(df):
        n = df.n_jumps.to_numpy(float); off = np.log(np.maximum(df.Lam.to_numpy(float), 1e-300)); x = np.log1p(df.U10w.to_numpy(float))
        def nll(v):
            eta = off + v[0] + v[1] * x; return float((np.exp(eta) - n * eta).sum())
        r = minimize(nll, [0.0, 0.0], method="L-BFGS-B")
        c, gam = r.x; prof = []
        gg = np.arange(-3, 3.001, 0.05)
        for g_ in gg:
            prof.append(minimize(lambda v: nll([v[0], g_]), [c], method="L-BFGS-B").fun)
        prof = np.array(prof); ins = gg[2 * (prof - prof.min()) <= 3.841]
        return dict(n_intervals=int(len(df)), n_jumps=int(n.sum()), expected_at_bw0=float(np.exp(off).sum()), c=float(c), gamma=float(gam),
                    gamma_ci95=(float(ins.min()), float(ins.max())), converged=bool(r.success))
    s2 = dict(all=count_model(d))
    for col in ("gap_class", "len_class", "region"):
        for v, g in d.groupby(col):
            if g.n_jumps.sum() >= 5:
                s2[f"{col}={v}"] = count_model(g)
    out["step2_count_model"] = s2
    log("step2", s2["all"])

    # 3 label sensitivities
    J = guard.read_csv(C.RULE_OUT / "jumps_training.csv", parse_dates=["t_start", "t_end"])
    base = J.inside_final & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 1000) & (J.dist_m <= 15000)
    variants = {"min_2km": J[base & J.line_ok & (J.dist_m >= 2000)], "possible_jumps_line_check_removed": J[base & (J.dist_m <= 15000)]}
    s3 = {"strict_no_never_detected": "not formable: the catalogue has no never_detected column; the primary rule already requires inside_final"}
    for name, Jv in variants.items():
        pv = collect_catalogue(Jv, ss, guard, fg)
        Lv = Lik(ss, pv)
        s3[name] = dict(n_positives=int(len(pv)), fit_bounded=Lv.fit(prior=True), fit_unbounded=Lv.fit(b_w_bounds=(-5, 5), prior=True),
                        profile_unbounded_ci95=Lv.profile_bw(np.round(np.arange(-5, 5.001, 0.25), 2), prior=True)["ci95"])
        log("step3", name, s3[name]["fit_unbounded"])
    out["step3_label_sensitivities"] = s3
    out["step4_era5"] = "not run: no ERA5 series in the v8 pool; the ERA5 arm (section 10) is pending a disclosed data fetch"

    # 5 positive control 7: planted b_w = 1 through the physics
    log("control 7")
    reps = []
    base_terms = Lik._lam(1.0, b_v8["b_L"], L.S, L.u, L.m, None, L.g)  # per suffstat row at b0 = 0
    b0_plant = math.log(len(pos) / base_terms.sum())
    for r in range(5):
        rng = np.random.default_rng(cv.S_CTRL + r)
        # synthetic positives per (interval, zone, group) row ~ Poisson(e^b0 * terms); their A = the row's mean A per cell
        n = rng.poisson(math.exp(b0_plant) * base_terms)
        rows = np.repeat(np.arange(len(n)), n)
        ncell = ss.n_cells.to_numpy()[rows]
        A_mean = L.S[rows] / ncell[:, None, None]
        pv = pd.DataFrame(dict(fire_key=ss.fire_key.to_numpy()[rows], epoch=ss.epoch.to_numpy()[rows], A_s0=A_mean[:, 0, 0], A_s1=A_mean[:, 0, 1], A_s2=A_mean[:, 0, 2],
                               A_l0=A_mean[:, 1, 0], A_l1=A_mean[:, 1, 1], A_l2=A_mean[:, 1, 2], u10_ms=L.u[rows], m_ff_pct=L.m[rows], fuel_group=L.g[rows]))
        S_rows, P_ = cv.to_fit_inputs(ss, pv)
        b, res = F.fit(S_rows, P_, full=True)
        Lr = Lik(ss, pv); prof = Lr.profile_bw(np.round(np.arange(0.0, 3.001, 0.1), 2), prior=True)
        reps.append(dict(replicate=r, n_synthetic=int(n.sum()), fit_py=b, profile_ci95=prof["ci95"], recovers_1=bool(prof["ci95"][0] <= 1.0 <= prof["ci95"][1])))
        log("c7 rep", r, b, prof["ci95"])
    out["control7_planted_bw1"] = dict(b0_plant=b0_plant, planted=dict(b_w=1.0, b_L=b_v8["b_L"]), replicates=reps,
                                       passed=sum(x["recovers_1"] for x in reps) >= 4,
                                       note="synthetic positives carry the mean A of their (interval, zone, group) row; a planted cell-level draw is the stage-2 refinement if this coarse version fails")
    out["finished_utc"] = pd.Timestamp.utcnow().isoformat(); out["wall_seconds"] = time.time() - t0
    (OUT / "beta_w_check.json").write_text(json.dumps(out, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
