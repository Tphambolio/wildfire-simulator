"""Probabilistic ensemble scores (validation/ensemble_scores.py) and the harness ensemble hook."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from firesim.spread.ensemble import EnsembleConfig
from firesim.validation.ensemble_scores import (
    crps_ensemble,
    log_area,
    score_ensemble_day,
    spread_skill,
    summarize_ensemble,
)

DATA = Path(__file__).parent / "data"
INF = np.inf


def test_crps_one_member_is_absolute_error_and_spread_helps():
    assert crps_ensemble(np.array([3.0]), 1.0) == pytest.approx(2.0)
    # an ensemble that brackets the observation beats its own mean's absolute error
    assert crps_ensemble(np.array([0.0, 2.0]), 1.0) == pytest.approx(0.5)
    assert crps_ensemble(np.array([1.0, 1.0]), 1.0) == pytest.approx(0.0)


def test_crps_is_the_empirical_cdf_integral():
    # Gneiting & Raftery (2007) eq. 20 (p. 367): CRPS = integral of (F(y) - 1{y >= x})^2 dy;
    # eq. 21's kernel form over all N^2 member pairs must equal it for the empirical F
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=7), 0.3
    grid = np.linspace(-10, 10, 200001)
    F = (x[None, :] <= grid[:, None]).mean(axis=1)
    g = (F - (grid >= y)) ** 2
    integral = float(np.sum(0.5 * (g[1:] + g[:-1]) * np.diff(grid)))  # trapezoid rule
    assert crps_ensemble(x, y) == pytest.approx(integral, abs=1e-3)


def test_spread_skill_follows_fortin_eq15():
    # Members and observation drawn from the same distribution each day (exchangeable): the
    # finite-ensemble ratio of Fortin et al. (2014) eq. 15 is 1; without the (N + 1) / N
    # correction it would be sqrt(6 / 5) = 1.095 for N = 5
    rng = np.random.default_rng(20261010)
    t, n = 20000, 5
    centre = rng.normal(0.0, 2.0, size=t)
    scale = rng.uniform(0.2, 2.0, size=t)  # spread varies from day to day
    members = [c + s * rng.normal(size=n) for c, s in zip(centre, scale)]
    obs = centre + scale * rng.normal(size=t)
    ss = spread_skill(members, obs)
    assert ss["ratio"] == pytest.approx(1.0, abs=0.02)
    assert ss["rmse"] / ss["rms_spread"] == pytest.approx(math.sqrt(6 / 5), abs=0.03)
    # average spread is sqrt(mean variance) (eq. 16); the mean sd is biased low (p. 1711)
    assert ss["mean_sd"] < ss["rms_spread"]
    # a constant bias inflates the ratio; the debiased ratio is unaffected
    biased = spread_skill(members, obs - 1.5)
    assert biased["bias"] == pytest.approx(1.5, abs=0.05)
    assert biased["ratio"] > 1.3
    assert biased["ratio_debiased"] == pytest.approx(ss["ratio_debiased"], abs=1e-9)
    assert biased["ratio_debiased"] == pytest.approx(1.0, abs=0.02)


def _day(member_masks, det_mask, obs_mask, prior=None):
    shape = obs_mask.shape
    dob = np.zeros(shape, dtype=np.int16)
    dob[obs_mask] = 200
    if prior is not None:
        dob[prior] = 199
    members = np.stack([np.where(m, 60.0, INF) for m in member_masks]).astype(np.float32)
    det = np.where(det_mask, 60.0, INF)
    return score_ensemble_day(dob, 200, members, det, 1e4, 17.0)


def test_score_day_counts_and_footprints():
    obs = np.zeros((4, 4), bool)
    obs[0, :2] = True  # 2 observed cells
    a = np.zeros((4, 4), bool)
    a[0, 0] = True
    b = a.copy()
    b[0, 1] = True
    b[3, 3] = True
    r = _day([a, b], a, obs)
    assert r["n_members"] == 2 and r["obs_ha"] == pytest.approx(2.0)
    assert r["member_ha"] == [1.0, 3.0]
    # k: (0,0)=2, (0,1)=1, (3,3)=1, rest 0
    assert r["k_cells"] == [13, 2, 1] and r["k_obs"] == [0, 1, 1]
    # P10 footprint (>= 1 member) holds every observed cell; P90 (2 members) only (0,0)
    assert r["p10"]["recall"] == pytest.approx(1.0)
    assert r["p90"]["precision"] == pytest.approx(1.0) and r["p90"]["recall"] == pytest.approx(0.5)
    # Brier over the 3 cells someone burned: (1-1)^2 + (0.5-1)^2 + (0.5-0)^2
    assert r["brier_cells"] == 3 and r["brier_sum"] == pytest.approx(0.5)
    assert r["brier_sum_det"] == pytest.approx(1.0)
    # over the whole 4x4 working area: ensemble 0.5 / 16, deterministic (one miss) 1 / 16
    s = summarize_ensemble([{"ens": {"17h": r}}])
    assert s["brier"] == pytest.approx(0.5 / 16) and s["brier_det"] == pytest.approx(1 / 16)
    assert s["bss_vs_det"] == pytest.approx(0.5)


def test_starting_area_is_excluded():
    obs = np.zeros((3, 3), bool)
    obs[1, 1] = True
    prior = np.zeros((3, 3), bool)
    prior[0, 0] = True
    m = obs | prior  # members also "burn" the prior cell
    r = _day([m, m], m, obs, prior)
    assert r["member_ha"] == [1.0, 1.0] and r["p50"]["f1"] == pytest.approx(1.0)


def test_summary_of_a_perfect_ensemble_and_coverage():
    obs = np.zeros((5, 5), bool)
    obs[2, :3] = True
    exact = _day([obs] * 4, obs, obs)
    s = summarize_ensemble([{"ens": {"17h": exact}}])
    assert s["brier"] == pytest.approx(0.0) and s["coverage_p10_p90"] == 1.0
    assert s["footprints"]["p50"]["f1"] == pytest.approx(1.0)
    assert s["frac_obs_at_p_ge"]["0.9"] == pytest.approx(1.0)
    # members all too small: the observed area is above P90 and the rank is N
    small = np.zeros((5, 5), bool)
    small[2, 0] = True
    s2 = summarize_ensemble([{"ens": {"17h": _day([small] * 4, small, obs)}}])
    assert s2["above_p90"] == 1.0 and s2["rank_hist"][-1] == 1
    assert s2["crps_log10_area"] == pytest.approx(float(log_area(3.0) - log_area(1.0)))


def test_harness_ensemble_runs_and_zero_spread_reproduces_det():
    from firesim.validation.cfsds import FireDomain
    from firesim.validation.harness import RunOptions, build_case, simulate, simulate_ensemble
    from firesim.validation.weather import HourRecord
    from datetime import datetime

    dom = FireDomain.load(DATA / "cfsds_fireday_fixture.npz")
    meta = json.loads((DATA / "cfsds_fireday_fixture.json").read_text())
    groups = {int(k): v for k, v in meta["groups"].items()}
    records = [HourRecord(datetime.fromisoformat(t), *vals) for t, *vals in meta["hours"]]
    case = build_case(dom, meta["day"], groups, records, start_hour=6, hours=24, margin_m=3000.0)
    opts = RunOptions(windows_h=(4.0,), oracle_max_h=4)
    zero = EnsembleConfig(n_members=2, wind_dir_sd_deg=0, wind_speed_log_sd=0, ffmc_sd=0,
                          dmc_dc_log_sd=0, curing_sd=0, fmc_sd=0, ros_log_sd=0)
    res = simulate_ensemble(case, opts, zero)
    det = simulate(case, opts)["arrival"]
    # FMC is pinned to the computed value in members; arrival is unchanged
    assert np.array_equal(np.isfinite(res["det"]), np.isfinite(det))
    for m in res["members"]:
        assert np.array_equal(np.isfinite(m), np.isfinite(det))
    spread = simulate_ensemble(case, opts, EnsembleConfig(n_members=3, seed=4))
    assert len(spread["records"]) == 3
    sc = score_ensemble_day(case.domain.dob, case.day, spread["members"], spread["det"],
                            case.domain.cell_area_m2, 4.0)
    assert sum(sc["k_cells"]) == int((~((case.domain.dob > 0) & (case.domain.dob < case.day))
                                      & ~(spread["members"] <= 0).any(0)
                                      & ~(spread["det"] <= 0)).sum())
    json.dumps(sc)
