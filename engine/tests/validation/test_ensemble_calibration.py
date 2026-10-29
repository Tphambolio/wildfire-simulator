"""Tests for the opt-in log-area post-processing of the ensemble (B2)."""

import numpy as np
import pytest

from firesim.validation.ensemble_calibration import (
    LOG_FLOOR,
    best_shift,
    calibrate_member_areas,
    fit_coverage_target,
    fit_min_crps,
    footprint_for_area,
    mean_scores,
    score_day,
    transform_log_area,
)
from firesim.validation.ensemble_scores import crps_ensemble, log_area


def test_identity_transform_is_bit_identical():
    ha = np.array([0.0, 12.5, 300.0, 4021.7, 88.0])
    assert np.array_equal(transform_log_area(log_area(ha), 0.0, 1.0), log_area(ha))
    np.testing.assert_allclose(calibrate_member_areas(ha, 0.0, 1.0), ha, rtol=0, atol=1e-9)


def test_shift_and_scale_act_about_the_mean_and_keep_order():
    y = np.array([2.0, 2.5, 3.0, 3.5])
    out = transform_log_area(y, -0.25, 2.0)
    assert np.isclose(out.mean(), y.mean() - 0.25)
    assert np.isclose(out.std(), 2.0 * y.std())
    assert np.all(np.diff(out) > 0)


def test_floor_is_zero_area():
    out = transform_log_area(np.array([1.0, 1.2, 1.4]), -2.0, 1.0)
    assert np.all(out == LOG_FLOOR)
    assert np.all(calibrate_member_areas([0.0, 5.0, 15.0], -2.0, 1.0) == 0.0)


def test_score_day_matches_ensemble_scores_conventions():
    y = np.linspace(2.0, 3.0, 20)
    s = score_day(y, 2.5)
    assert s.inside and not s.below and not s.above
    assert s.crps == pytest.approx(crps_ensemble(y, 2.5))
    assert s.rank == 10
    assert score_day(y, 1.5).below and score_day(y, 1.5).rank == 0
    assert score_day(y, 3.5).above and score_day(y, 3.5).rank == 20


def _biased_days(n=60, bias=0.4, sd_obs=0.5, sd_mem=0.2, seed=3):
    rng = np.random.default_rng(seed)
    days = []
    for _ in range(n):
        truth = rng.uniform(2, 4)
        obs = truth + rng.normal(0, sd_obs)
        mem = truth + bias + rng.normal(0, sd_mem, 20)
        days.append((mem, float(obs)))
    return days


def test_best_shift_recovers_a_known_bias():
    days = _biased_days()
    d = best_shift(days, 1.0)
    assert -0.6 < d < -0.2


def test_coverage_fit_reaches_target_and_widens_a_narrow_ensemble():
    days = _biased_days()
    fit = fit_coverage_target(days)
    assert fit["reached_target"]
    assert fit["coverage"] >= 0.8
    assert fit["scale"] > 1.0
    assert fit["shift"] < 0
    base = mean_scores(days, 0.0, 1.0)
    assert fit["crps"] < base["crps"]
    assert base["coverage"] < 0.5


def test_min_crps_fit_is_no_worse_than_coverage_fit_on_its_own_data():
    days = _biased_days()
    assert fit_min_crps(days)["crps"] <= fit_coverage_target(days)["crps"] + 1e-12


def test_footprint_for_area_picks_the_closest_member_count():
    # 3 members; k_cells[k] = cells reached by exactly k members
    k_cells = [100, 30, 20, 10]
    k_obs = [5, 5, 10, 10]
    f = footprint_for_area(k_cells, k_obs, cell_ha=1.0, target_ha=31.0)
    assert f["k"] == 2 and f["area_ha"] == 30.0
    assert f["recall"] == pytest.approx(20 / 30)
    assert footprint_for_area(k_cells, k_obs, 1.0, 1e6)["k"] == 1


def test_vectorised_scores_equal_the_per_day_loop():
    days = _biased_days(n=12, seed=7)
    # include a floor-clipped day and a zero-growth observation
    days.append((np.full(20, 1.0), 1.0))
    for shift, scale in ((0.0, 1.0), (-0.37, 1.8), (0.2, 0.6)):
        per = [score_day(transform_log_area(y, shift, scale), o) for y, o in days]
        ms = mean_scores(days, shift, scale)
        assert ms["crps"] == pytest.approx(np.mean([d.crps for d in per]), abs=1e-12)
        assert ms["coverage"] == pytest.approx(np.mean([d.inside for d in per]))
        assert ms["below_p10"] == pytest.approx(np.mean([d.below for d in per]))
