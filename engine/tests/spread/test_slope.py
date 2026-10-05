"""Tests for the FBP slope factor (ST-X-3 eq 39)."""

import math

import pytest

from firesim.spread.slope import calculate_slope_factor


class TestSlopeFactor:
    """SF = exp(3.533 (GS/100)^1.2), 10 at GS >= 70 %."""

    def test_flat_no_effect(self):
        assert calculate_slope_factor(0.0) == 1.0

    def test_negative_slope_no_effect(self):
        assert calculate_slope_factor(-10.0) == 1.0

    def test_moderate_slope(self):
        assert calculate_slope_factor(30.0) == pytest.approx(math.exp(3.533 * 0.3**1.2))

    def test_no_2x_cap(self):
        """The old Butler (2007) cap of 2.0 is not part of FBP."""
        assert calculate_slope_factor(50.0) > 2.0

    def test_steep_slope_is_10(self):
        assert calculate_slope_factor(70.0) == 10.0
        assert calculate_slope_factor(120.0) == 10.0

    def test_increases_with_slope(self):
        values = [calculate_slope_factor(s) for s in range(0, 70, 5)]
        assert all(a < b for a, b in zip(values, values[1:]))
