"""Tests for the FBP calculator.

Validates fire behavior predictions for all 18 Canadian fuel types
against expected ranges from ST-X-3 tables and field observations.
"""

import math

import pytest

from firesim.fbp.calculator import (
    calculate_bui,
    calculate_bui_effect,
    calculate_fbp,
    calculate_grass_curing_factor,
    calculate_isi,
    calculate_surface_ros,
)
from firesim.fbp.constants import FuelType, FUEL_TYPES, get_fuel_spec
from firesim.types import FireType


class TestISI:
    """Test Initial Spread Index calculation."""

    def test_isi_zero_wind(self):
        """ISI at zero wind should be driven by FFMC only."""
        isi = calculate_isi(ffmc=90.0, wind_speed=0.0)
        assert isi > 0.0
        assert isi < 10.0

    def test_isi_increases_with_wind(self):
        """ISI should increase with wind speed."""
        isi_low = calculate_isi(ffmc=90.0, wind_speed=10.0)
        isi_high = calculate_isi(ffmc=90.0, wind_speed=30.0)
        assert isi_high > isi_low

    def test_isi_increases_with_ffmc(self):
        """Higher FFMC (drier fuel) should produce higher ISI."""
        isi_wet = calculate_isi(ffmc=70.0, wind_speed=20.0)
        isi_dry = calculate_isi(ffmc=95.0, wind_speed=20.0)
        assert isi_dry > isi_wet

    def test_isi_known_value(self):
        """ISI for FFMC=90, wind=20 should be in expected range."""
        isi = calculate_isi(ffmc=90.0, wind_speed=20.0)
        assert 8.0 < isi < 15.0


class TestBUI:
    """Test Buildup Index calculation."""

    def test_bui_zero_inputs(self):
        """BUI should be 0 when both DMC and DC are 0."""
        assert calculate_bui(0.0, 0.0) == 0.0

    def test_bui_low_dmc(self):
        """When DMC << DC, the lower formula applies."""
        bui = calculate_bui(dmc=20.0, dc=200.0)
        assert bui > 0.0
        assert bui < 40.0

    def test_bui_increases_with_dmc(self):
        """BUI should increase as DMC increases."""
        bui_low = calculate_bui(dmc=20.0, dc=200.0)
        bui_high = calculate_bui(dmc=60.0, dc=200.0)
        assert bui_high > bui_low

    def test_bui_never_negative(self):
        """BUI should never be negative."""
        bui = calculate_bui(dmc=1.0, dc=1.0)
        assert bui >= 0.0


class TestBUIEffect:
    """Test BUI effect on rate of spread."""

    def test_bui_effect_at_bui0(self):
        """BUI effect should be ~1.0 at BUI = BUI0."""
        be = calculate_bui_effect(bui=64.0, q=0.70, bui0=64.0)
        assert abs(be - 1.0) < 0.01

    def test_bui_effect_above_bui0(self):
        """BUI effect should be >1.0 above BUI0."""
        be = calculate_bui_effect(bui=100.0, q=0.70, bui0=64.0)
        assert be > 1.0

    def test_bui_effect_below_bui0(self):
        """BUI effect should be <1.0 below BUI0."""
        be = calculate_bui_effect(bui=30.0, q=0.70, bui0=64.0)
        assert be < 1.0

    def test_bui_effect_q_equals_one(self):
        """When q=1.0, BUI has no effect (grass types)."""
        be = calculate_bui_effect(bui=50.0, q=1.0, bui0=1.0)
        assert be == 1.0


class TestGrassCuring:
    """Test grass curing factor for O1a/O1b types."""

    def test_green_grass_no_spread(self):
        """Fully green grass (0% curing) should not spread."""
        cf = calculate_grass_curing_factor(0.0)
        assert cf == 0.0

    def test_moderate_curing(self):
        """60% curing should allow moderate spread."""
        cf = calculate_grass_curing_factor(60.0)
        assert 0.1 < cf < 0.5

    def test_high_curing(self):
        """90% curing should allow significant spread."""
        cf = calculate_grass_curing_factor(90.0)
        assert cf > 0.5

    def test_full_curing(self):
        """100% cured grass spreads at the full O-1 rate (Wotton et al. 2009 eq 35b)."""
        assert calculate_grass_curing_factor(100.0) == pytest.approx(1.0)

    def test_below_threshold_exponential(self):
        """Below 58.8% the curing factor is 0.005 * (exp(0.061 C) - 1) (eq 35a)."""
        cf = calculate_grass_curing_factor(50.0)
        assert cf == pytest.approx(0.005 * (math.exp(0.061 * 50.0) - 1.0))
        # The two branches meet at 58.8 % (within rounding of the published constants)
        assert calculate_grass_curing_factor(58.79) == pytest.approx(0.176, abs=0.001)

    def test_linear_above_threshold(self):
        """Above 58.8% the curing factor rises linearly at 0.02 per percent (eq 35b)."""
        assert calculate_grass_curing_factor(80.0) - calculate_grass_curing_factor(70.0) == (
            pytest.approx(0.2)
        )


# FBP validation for each fuel type.
# Expected ROS ranges based on ST-X-3 Table 6 and field observations.
# Conditions: FFMC=90, DMC=45, DC=300, wind=20 km/h, flat terrain.
_STANDARD_CONDITIONS = {
    "ffmc": 90.0,
    "dmc": 45.0,
    "dc": 300.0,
    "wind_speed": 20.0,
}


class TestFBPAllFuelTypes:
    """Validate FBP output for all 18 fuel types under standard conditions."""

    @pytest.mark.parametrize(
        "fuel_type,min_ros,max_ros",
        [
            (FuelType.C1, 1.0, 15.0),
            (FuelType.C2, 3.0, 25.0),
            (FuelType.C3, 3.0, 20.0),
            (FuelType.C4, 3.0, 25.0),
            (FuelType.C5, 0.5, 10.0),
            (FuelType.C6, 0.5, 12.0),
            (FuelType.C7, 0.5, 10.0),
            (FuelType.D1, 0.5, 8.0),
            (FuelType.D2, 0.0, 0.0),  # green aspen: no spread below BUI 80
            (FuelType.M1, 1.0, 18.0),
            (FuelType.M2, 0.5, 12.0),
            (FuelType.M3, 3.0, 60.0),
            (FuelType.M4, 1.0, 15.0),
            (FuelType.O1a, 1.0, 30.0),
            (FuelType.O1b, 1.0, 40.0),
            (FuelType.S1, 1.0, 25.0),
            (FuelType.S2, 0.5, 15.0),
            (FuelType.S3, 1.0, 25.0),
        ],
    )
    def test_ros_within_expected_range(self, fuel_type, min_ros, max_ros):
        """Surface ROS should be within expected range for standard conditions."""
        result = calculate_fbp(
            fuel_type=fuel_type,
            wind_speed=_STANDARD_CONDITIONS["wind_speed"],
            ffmc=_STANDARD_CONDITIONS["ffmc"],
            dmc=_STANDARD_CONDITIONS["dmc"],
            dc=_STANDARD_CONDITIONS["dc"],
        )
        assert min_ros <= result.ros_surface <= max_ros, (
            f"{fuel_type.value}: ROS {result.ros_surface:.2f} outside [{min_ros}, {max_ros}]"
        )

    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_all_fuel_types_produce_output(self, fuel_type):
        """Every fuel type should produce a valid FBPResult without errors."""
        result = calculate_fbp(
            fuel_type=fuel_type,
            wind_speed=20.0,
            ffmc=90.0,
            dmc=45.0,
            dc=300.0,
        )
        assert result.ros_surface >= 0.0
        assert result.ros_final >= 0.0
        assert result.hfi >= 0.0
        assert 0.0 <= result.cfb <= 1.0
        assert result.fire_type in FireType
        assert result.flame_length >= 0.0
        assert result.tfc >= 0.0

    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_ros_increases_with_wind(self, fuel_type):
        """ROS should generally increase with wind speed for all types."""
        ros_low = calculate_fbp(fuel_type, 5.0, 90.0, 45.0, 300.0).ros_surface
        ros_high = calculate_fbp(fuel_type, 40.0, 90.0, 45.0, 300.0).ros_surface
        assert ros_high >= ros_low, (
            f"{fuel_type.value}: ROS at 40 km/h ({ros_high:.2f}) < ROS at 5 km/h ({ros_low:.2f})"
        )


class TestFBPCrownFire:
    """Test crown fire behavior in FBP results."""

    def test_c2_high_intensity_crowns(self):
        """C2 Boreal Spruce at high intensity should produce crown fire."""
        result = calculate_fbp("C2", 40.0, 95.0, 80.0, 500.0)
        assert result.cfb > 0.0
        assert result.fire_type in (FireType.PASSIVE_CROWN, FireType.ACTIVE_CROWN)

    def test_d1_never_crowns(self):
        """D1 Leafless Aspen has no canopy, should never crown."""
        result = calculate_fbp("D1", 40.0, 95.0, 80.0, 500.0)
        assert result.cfb == 0.0
        assert result.fire_type == FireType.SURFACE

    def test_grass_never_crowns(self):
        """Grass types have no canopy, should never crown."""
        result = calculate_fbp("O1b", 40.0, 95.0, 80.0, 500.0)
        assert result.cfb == 0.0
        assert result.fire_type == FireType.SURFACE

    def test_crown_fire_does_not_change_c2_rate(self):
        """Outside C-6, FBP's final ROS is the surface rate; crowning adds intensity."""
        result = calculate_fbp("C2", 40.0, 95.0, 80.0, 500.0)
        assert result.cfb > 0.0
        assert result.ros_final == pytest.approx(result.ros_surface)
        assert result.hfi > result.sfi

    def test_cfb_equation_58(self):
        """CFB = 1 - exp(-0.23 (ROS - RSO)), RSO = CSI / (300 SFC)."""
        result = calculate_fbp("C3", 30.0, 92.0, 80.0, 500.0, fmc=100.0)
        assert result.rso == pytest.approx(result.csi / (300.0 * result.sfc))
        assert result.cfb == pytest.approx(1.0 - math.exp(-0.23 * (result.ros_final - result.rso)))

    def test_c6_crown_rate_exceeds_surface(self):
        """C-6 is the one fuel type with a separate crown rate (eqs 64-66)."""
        result = calculate_fbp("C6", 40.0, 94.0, 80.0, 500.0)
        assert result.cfb > 0.0
        assert result.ros_final > result.ros_surface


class TestFBPSlope:
    """Test slope integration in FBP."""

    def test_upslope_increases_ros(self):
        """Upslope should increase ROS."""
        flat = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0, slope=0.0)
        slope = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0, slope=50.0)
        assert slope.ros_surface > flat.ros_surface

    def test_slope_enters_as_equivalent_wind(self):
        """Slope raises the net effective wind speed WSV (ST-X-3 eqs 39-50)."""
        flat = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0, slope=0.0)
        steep = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0, slope=60.0)
        assert flat.wsv == pytest.approx(20.0)
        assert steep.wsv > flat.wsv
        assert steep.isi > flat.isi

    def test_cross_slope_turns_spread_direction(self):
        """Upslope to the north with wind toward the east turns the head between them."""
        result = calculate_fbp(
            "C2", 15.0, 90.0, 45.0, 300.0, slope=40.0, wind_direction=270.0, slope_aspect=0.0
        )
        assert 0.0 < result.raz < 90.0

    def test_steep_slope_not_capped_at_2x(self):
        """There is no 2x cap: SF = exp(3.533 (GS/100)^1.2) up to 10 at 70 % (eq 39)."""
        flat = calculate_fbp("C2", 0.0, 90.0, 45.0, 300.0, slope=0.0)
        steep = calculate_fbp("C2", 0.0, 90.0, 45.0, 300.0, slope=60.0)
        assert steep.ros_final / flat.ros_final > 2.5


class TestFBPMixedwood:
    """Test M1/M2 mixedwood blending."""

    def test_m1_pc100_matches_c2(self):
        """M1 at 100% conifer should approximate C2 behavior."""
        m1 = calculate_fbp("M1", 20.0, 90.0, 45.0, 300.0, pc=100.0)
        c2 = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0)
        assert abs(m1.ros_surface - c2.ros_surface) / c2.ros_surface < 0.1

    def test_m1_pc0_matches_d1(self):
        """M1 at 0% conifer should approximate D1 behavior."""
        m1 = calculate_fbp("M1", 20.0, 90.0, 45.0, 300.0, pc=0.0)
        d1 = calculate_fbp("D1", 20.0, 90.0, 45.0, 300.0)
        assert abs(m1.ros_surface - d1.ros_surface) / max(d1.ros_surface, 0.1) < 0.15

    def test_m1_intermediate_blends(self):
        """M1 at 50% conifer should be between C2 and D1."""
        m1 = calculate_fbp("M1", 20.0, 90.0, 45.0, 300.0, pc=50.0)
        c2 = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0)
        d1 = calculate_fbp("D1", 20.0, 90.0, 45.0, 300.0)
        assert d1.ros_surface <= m1.ros_surface <= c2.ros_surface


class TestSTX3Defects:
    """Regression tests for departures from ST-X-3 / Wotton (2009) fixed in 2026-10."""

    def test_d1_has_buildup_effect(self):
        """D-1 carries a buildup effect (q = 0.9, BUI0 = 32)."""
        low = calculate_fbp("D1", 20.0, 90.0, 10.0, 100.0)
        high = calculate_fbp("D1", 20.0, 90.0, 80.0, 500.0)
        assert high.ros_surface > low.ros_surface

    def test_sfc_depends_on_bui(self):
        """Surface fuel consumption is a function of BUI, not a constant (ST-X-3 eqs 9-25)."""
        low = calculate_fbp("C2", 20.0, 90.0, 10.0, 100.0)
        high = calculate_fbp("C2", 20.0, 90.0, 80.0, 500.0)
        assert high.sfc > low.sfc
        assert high.sfc == pytest.approx(5.0 * (1.0 - math.exp(-0.0115 * high.bui)))

    def test_isi_wind_function_above_40(self):
        """Above 40 km/h FBP uses f(W) = 12 (1 - exp(-0.0818 (W - 28))) (eq 53a)."""
        isi_39 = calculate_isi(90.0, 39.9)
        isi_60 = calculate_isi(90.0, 60.0)
        f_f = isi_39 / (0.208 * math.exp(0.05039 * 39.9))
        assert isi_60 == pytest.approx(0.208 * f_f * 12.0 * (1.0 - math.exp(-0.0818 * 32.0)))

    def test_grass_length_to_breadth(self):
        """O-1 uses LB = 1.1 WSV^0.464 (Wotton et al. 2009 eq 80)."""
        grass = calculate_fbp("O1a", 20.0, 90.0, 45.0, 300.0, grass_cure=100.0)
        forest = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0)
        assert grass.lb == pytest.approx(1.1 * 20.0**0.464)
        assert grass.lb != pytest.approx(forest.lb)

    def test_flank_and_back_rates(self):
        """BROS from the back ISI, FROS = (ROS + BROS) / (2 LB) (eqs 75-89)."""
        r = calculate_fbp("C2", 20.0, 90.0, 45.0, 300.0)
        spec = get_fuel_spec("C2")
        bisi = 0.208 * math.exp(-0.05039 * 20.0) * r.isi / (0.208 * math.exp(0.05039 * 20.0))
        expected_back = calculate_surface_ros(spec, bisi, r.bui)
        assert r.back_ros == pytest.approx(expected_back)
        assert r.flank_ros == pytest.approx((r.ros_final + r.back_ros) / (2.0 * r.lb))

    def test_c4_buildup_q(self):
        """C-4 q is 0.80 (ST-X-3 Table 7)."""
        assert get_fuel_spec("C4").q == pytest.approx(0.80)

    def test_foliar_moisture_from_date(self):
        """FMC follows ST-X-3 eqs 1-8: minimum 85 % near the seasonal dip."""
        from firesim.fbp.calculator import calculate_foliar_moisture

        values = [calculate_foliar_moisture(53.5, -113.5, None, d) for d in range(100, 250)]
        assert min(values) == pytest.approx(85.0)
        assert max(values) == pytest.approx(120.0)

    def test_fmc_changes_crowning(self):
        """Lower foliar moisture lowers the crowning threshold."""
        dry = calculate_fbp("C3", 25.0, 92.0, 80.0, 500.0, fmc=85.0)
        wet = calculate_fbp("C3", 25.0, 92.0, 80.0, 500.0, fmc=120.0)
        assert dry.csi < wet.csi
        assert dry.cfb >= wet.cfb


class TestAcceleration:
    """Point-ignition acceleration (ST-X-3 eqs 70-73, 81)."""

    def test_open_fuel_alpha(self):
        from firesim.fbp.calculator import calculate_acceleration

        assert calculate_acceleration("O1a", 0.0) == pytest.approx(0.115)
        assert calculate_acceleration("C1", 0.9) == pytest.approx(0.115)

    def test_closed_canopy_alpha_falls_with_crowning(self):
        from firesim.fbp.calculator import calculate_acceleration

        cfb = 0.3125  # maximum of CFB^2.5 exp(-8 CFB)
        expected = 0.115 - 18.8 * cfb**2.5 * math.exp(-8 * cfb)
        assert calculate_acceleration("C2", cfb) == pytest.approx(expected)
        assert calculate_acceleration("C2", cfb) < calculate_acceleration("C2", 0.0)

    def test_distance_is_integral_of_rate(self):
        from firesim.fbp.calculator import calculate_distance_at_time, calculate_ros_at_time

        n, t = 20000, 30.0
        integral = sum(calculate_ros_at_time(10.0, 0.115, (i + 0.5) * t / n) for i in range(n)) * t / n
        assert calculate_distance_at_time(10.0, 0.115, t) == pytest.approx(integral, rel=1e-6)

    def test_lb_starts_round(self):
        from firesim.fbp.calculator import calculate_lb_at_time

        assert calculate_lb_at_time(3.0, 0.115, 0.0) == pytest.approx(1.0)
        assert calculate_lb_at_time(3.0, 0.115, 600.0) == pytest.approx(3.0, rel=1e-6)
