"""Tests for crown fire initiation and behavior."""

import math

import pytest

from firesim.fbp.constants import FuelType, FUEL_TYPES
from firesim.fbp.crown_fire import (
    calculate_critical_surface_intensity,
    calculate_critical_surface_ros,
    calculate_crown_fraction_burned,
    classify_fire_type,
)
from firesim.types import FireType


class TestCriticalSurfaceIntensity:
    """Test Van Wagner (1977) critical intensity calculation."""

    def test_zero_cbh_returns_zero(self):
        """No canopy means no crown fire threshold."""
        csi = calculate_critical_surface_intensity(cbh=0.0)
        assert csi == 0.0

    def test_low_cbh_lower_threshold(self):
        """Lower crown base height should have lower CSI."""
        csi_low = calculate_critical_surface_intensity(cbh=2.0)
        csi_high = calculate_critical_surface_intensity(cbh=10.0)
        assert csi_low < csi_high

    def test_c2_csi_reasonable(self):
        """C2 (CBH=3m) should have CSI between 200-1500 kW/m."""
        csi = calculate_critical_surface_intensity(cbh=3.0, fmc=100.0)
        assert 200.0 < csi < 1500.0

    def test_c5_high_cbh_high_csi(self):
        """C5 (CBH=18m) should have very high CSI (hard to crown)."""
        csi = calculate_critical_surface_intensity(cbh=18.0, fmc=100.0)
        assert csi > 5000.0

    def test_fmc_effect(self):
        """Higher FMC should increase CSI (wetter canopy harder to ignite)."""
        csi_dry = calculate_critical_surface_intensity(cbh=5.0, fmc=80.0)
        csi_wet = calculate_critical_surface_intensity(cbh=5.0, fmc=120.0)
        assert csi_wet > csi_dry


class TestCrownFractionBurned:
    """Test CFB (ST-X-3 eq 58): CFB = 1 - exp(-0.23 (ROS - RSO))."""

    def test_below_threshold_no_crown(self):
        """ROS at or below RSO gives zero CFB."""
        assert calculate_crown_fraction_burned(ros=2.0, rso=3.0) == 0.0

    def test_above_threshold_positive_cfb(self):
        """ROS above RSO gives positive CFB matching eq 58."""
        cfb = calculate_crown_fraction_burned(ros=10.0, rso=3.0)
        assert cfb == pytest.approx(1.0 - math.exp(-0.23 * 7.0))

    def test_cfb_bounded_zero_to_one(self):
        """CFB should always be between 0 and 1."""
        cfb = calculate_crown_fraction_burned(ros=500.0, rso=0.1)
        assert 0.0 <= cfb <= 1.0

    def test_rso_from_csi_and_sfc(self):
        """RSO = CSI / (300 SFC) (eq 57); no surface fuel means no crowning."""
        assert calculate_critical_surface_ros(900.0, 1.5) == pytest.approx(2.0)
        assert calculate_critical_surface_ros(900.0, 0.0) == math.inf


class TestFireTypeClassification:
    """Test fire type classification from CFB."""

    def test_surface_fire(self):
        assert classify_fire_type(0.0) == FireType.SURFACE

    def test_torching(self):
        assert classify_fire_type(0.05) == FireType.SURFACE_WITH_TORCHING

    def test_passive_crown(self):
        assert classify_fire_type(0.5) == FireType.PASSIVE_CROWN

    def test_active_crown(self):
        assert classify_fire_type(0.95) == FireType.ACTIVE_CROWN
