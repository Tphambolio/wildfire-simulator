"""Hamada urban fire spread rates (docs/structure-spread-spec.md §4).

Sources: Purnomo et al. (2026) Fire Safety J. 161: 104651, eq 2 (p.3) and supplementary
material; Qin (2025) PhD, Univ. Maryland, eqs 2.66-2.80, Table 2.2 (pp.38-40); Himoto & Tanaka
(2008) Fire Safety J. 43: 477-494, eqs 42-43 (m/min). No paper prints a worked T or U value
that the published equations reproduce (spec §4.5), so the check values below are the
equations evaluated by hand, plus the properties the papers state.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from firesim.spread.ellipse import calculate_ros_at_theta
from firesim.structures.hamada import (
    HAMADA_COEFFS,
    crossing_time_min,
    ellipse_rate,
    hamada_axis_rates,
    hamada_time_min,
    rate_toward,
)


def test_coefficients_match_the_published_table():
    assert HAMADA_COEFFS == {
        "d": (1.6, 0.1, 0.007, 25.0, 2.5),
        "s": (1.0, 0.0, 0.005, 5.0, 0.25),
        "u": (1.0, 0.0, 0.002, 5.0, 0.2),
    }


def test_downwind_time_by_hand_17_8_m_s():
    # a0 = d = 10 m, fb = 1, V = 17.8 m/s (Qin 2025 Table 6.1 baseline):
    # C_d = 1.6 (1 + 0.1*17.8 + 0.007*17.8^2) = 1.6 * 4.99788 = 7.996608   (c3 on V^2, spec C6)
    # T_d = (5 + 0.625*10 + 16*10/(25 + 2.5*17.8)) / C_d = (11.25 + 160/69.5) / 7.996608
    expected = (11.25 + 160.0 / 69.5) / 7.996608
    assert hamada_time_min("d", 10.0, 10.0, 17.8) == pytest.approx(expected, rel=1e-9)
    assert expected == pytest.approx(1.6947, abs=1e-4)


@pytest.mark.parametrize(
    "v, t_d, t_s, t_u",
    [(0.0, 11.0312, 43.25, 43.25), (10.0, 3.3449, 21.7222, 28.4226), (17.8, 1.6947, 10.9052, 18.3277)],
)
def test_check_table_in_the_spec(v, t_d, t_s, t_u):
    """docs/structure-spread-spec.md §4.5, a0 = d = 10 m, fb = 1."""
    assert hamada_time_min("d", 10, 10, v) == pytest.approx(t_d, abs=1e-4)
    assert hamada_time_min("s", 10, 10, v) == pytest.approx(t_s, abs=1e-4)
    assert hamada_time_min("u", 10, 10, v) == pytest.approx(t_u, abs=1e-4)


def test_fb_zero_has_no_wind_term():
    # (1 - fb) bracket only: 3 + 0.375 a0 + 8 d / (c4 + c5 V), wind enters only via c5 V
    assert hamada_time_min("d", 10, 10, 0.0, fb=0.0) == pytest.approx(3 + 3.75 + 80 / 25.0)
    # spread slows as fb falls at a strong wind (spec C9)
    assert hamada_time_min("d", 10, 10, 17.8, fb=0.5) > hamada_time_min("d", 10, 10, 17.8, fb=1.0)


def test_rates_and_downwind_crossing_at_high_wind_equal_hamada():
    v_d, v_s, v_u = hamada_axis_rates(10, 10, 17.8)
    assert float(v_d) == pytest.approx(20.0 / 1.6947, rel=1e-4)  # 11.80 m/min = 0.197 m/s
    assert float(v_d) / 60.0 == pytest.approx(0.1967, abs=1e-4)
    # V >= 10 m/s: no Hazus blend; downwind crossing time is T_d
    assert float(crossing_time_min(10, 10, 17.8, 1.0)) == pytest.approx(
        float(hamada_time_min("d", 10, 10, 17.8)), rel=1e-9)
    assert float(crossing_time_min(10, 10, 17.8, -1.0)) == pytest.approx(
        float(hamada_time_min("u", 10, 10, 17.8)), rel=1e-9)


def test_hazus_correction_makes_zero_wind_isotropic():
    """The Hazus correction exists because Hamada predicts downwind elongation with no wind
    (Purnomo et al. 2026 SI)."""
    u_d, u_s, u_u = hamada_axis_rates(10, 10, 0.0, hazus=False)
    assert float(u_d) > 3 * float(u_s)  # uncorrected: elongated at zero wind
    v_d, v_s, v_u = hamada_axis_rates(10, 10, 0.0)
    g = math.sqrt((float(u_d) + float(u_u)) / 2 * float(u_s))
    for r in (v_d, v_s, v_u):
        assert float(r) == pytest.approx(g, rel=1e-4)
    assert g == pytest.approx(0.725, abs=1e-3)
    for ct in np.linspace(-1, 1, 9):
        assert float(rate_toward(10, 10, 0.0, ct)) == pytest.approx(g, rel=1e-6)


def test_hazus_blend_is_continuous_at_10_m_s():
    below = np.array(hamada_axis_rates(12, 6, 10.0 - 1e-9))
    at = np.array(hamada_axis_rates(12, 6, 10.0))
    np.testing.assert_allclose(below, at, rtol=1e-8)


def test_monotone_in_wind():
    winds = np.linspace(0.0, 30.0, 61)
    for a0, d in [(10, 3), (10, 10), (15, 25)]:
        t = crossing_time_min(a0, d, winds, 1.0)
        assert np.all(np.diff(t) < 0), (a0, d)


def test_rate_increases_but_crossing_slows_with_separation():
    """Qin (2025) p.177: Hamada 'predicts an increasing ROS with increasing separation
    distance'; the time to cross a wider gap still grows."""
    d = np.array([2.5, 5, 10, 20, 30])
    v_d = hamada_axis_rates(10, d, 17.8)[0]
    assert np.all(np.diff(v_d) > 0)
    assert np.all(np.diff(crossing_time_min(10, d, 17.8, 1.0)) > 0)


def test_downwind_is_fastest():
    ct = np.cos(np.radians(np.arange(0, 181, 15)))
    for v in (1.0, 3.0, 10.0, 20.0):
        t = crossing_time_min(10, 8, v, ct)
        assert np.argmin(t) == 0, v
        assert t[-1] >= t[0]


def test_ellipse_matches_the_fbp_ellipse_construction():
    rng = np.random.default_rng(3)
    for _ in range(200):
        v_u = rng.uniform(0.1, 5)
        v_d = v_u + rng.uniform(0, 20)
        v_s = rng.uniform(0.1, 5)
        th = rng.uniform(0, math.pi)
        assert float(ellipse_rate(v_d, v_s, v_u, math.cos(th))) == pytest.approx(
            calculate_ros_at_theta(v_d, v_s, v_u, th), rel=1e-9)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        hamada_time_min("d", 10, 10, -1.0)
    with pytest.raises(ValueError):
        hamada_time_min("d", 10, 10, 5.0, fb=1.5)


@pytest.mark.xfail(strict=True, reason=(
    "Qin (2025) pp.159, 161, 167 reports a Hamada rate of 0.34 m/s for a = d = 10 m, fully "
    "combustible, 17.8 m/s wind; the published equations give 0.197 m/s. Unexplained "
    "(spec §4.5, C11)."))
def test_reproduces_qin_0_34_m_s():
    assert float(hamada_axis_rates(10, 10, 17.8)[0]) / 60.0 == pytest.approx(0.34, rel=0.05)
