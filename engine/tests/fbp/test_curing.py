"""Date-aware grass curing default (decision M1, 2026-10-10; firesim.fbp.curing)."""

import pytest

from firesim.fbp.calculator import calculate_grass_curing_factor
from firesim.fbp.curing import (
    SPRING_CURING_DEFAULT,
    SPRING_WINDOW_DOY,
    curing_required_message,
    default_grass_cure,
    in_spring_curing_window,
)
from firesim.validation.harness import RunOptions, grass_cure_for


def test_window_edges():
    start, end = SPRING_WINDOW_DOY
    assert (start, end) == (60, 150)
    assert default_grass_cure(start - 1) is None
    assert default_grass_cure(start) == SPRING_CURING_DEFAULT == 95.0
    assert default_grass_cure(end - 1) == 95.0
    assert default_grass_cure(end) is None


@pytest.mark.parametrize("doy", [1, 59, 150, 196, 258, 300, 366, None])
def test_no_default_outside_the_window(doy):
    assert default_grass_cure(doy) is None
    assert not in_spring_curing_window(doy)


def test_curing_factor_of_the_defaults():
    """GLC-X-10 eq 35b (p.9): the old 60 % default ran grass at a fifth of fully cured."""
    assert calculate_grass_curing_factor(60.0) == pytest.approx(0.200, abs=1e-9)
    assert calculate_grass_curing_factor(90.0) == pytest.approx(0.800, abs=1e-9)
    assert calculate_grass_curing_factor(SPRING_CURING_DEFAULT) == pytest.approx(0.900, abs=1e-9)
    assert calculate_grass_curing_factor(100.0) == pytest.approx(1.000, abs=1e-9)


def test_required_message_names_the_window():
    msg = curing_required_message(196)
    assert "grass_cure is required" in msg and "196" in msg and "95 %" in msg
    assert "no date" in curing_required_message(None)


def test_harness_rule_unchanged_unless_spring_value_set():
    opts = RunOptions()
    assert grass_cure_for(120, opts) == 90.0  # original rule: dormant value in spring
    assert grass_cure_for(200, opts) == 60.0
    assert grass_cure_for(300, opts) == 90.0
    m1 = RunOptions(grass_cure_spring=95.0)
    assert grass_cure_for(120, m1) == 95.0
    assert grass_cure_for(59, m1) == 90.0  # before the window: dormant value
    assert grass_cure_for(200, m1) == 60.0
    assert grass_cure_for(300, m1) == 90.0
