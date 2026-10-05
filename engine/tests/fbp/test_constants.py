"""Tests for FBP fuel type constants.

Validates that all 18 fuel types are defined with correct parameters
and that lookup functions work correctly.
"""

import pytest

from firesim.fbp.constants import FuelType, FuelTypeSpec, FUEL_TYPES, get_fuel_spec


class TestFuelTypes:
    """Validate fuel type definitions."""

    def test_all_18_fuel_types_defined(self):
        """All 18 Canadian FBP fuel types must be defined."""
        assert len(FUEL_TYPES) == 18
        assert len(FuelType) == 18

    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_every_fuel_type_has_spec(self, fuel_type):
        """Every FuelType enum member must have a FuelTypeSpec entry."""
        assert fuel_type in FUEL_TYPES
        spec = FUEL_TYPES[fuel_type]
        assert isinstance(spec, FuelTypeSpec)
        assert spec.code == fuel_type

    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_ros_parameters_positive(self, fuel_type):
        """ROS parameters a, b, c should be non-negative."""
        spec = FUEL_TYPES[fuel_type]
        # M1 and M2 have a=0 (special case: calculated from C2/D1 blend)
        assert spec.a >= 0.0
        assert spec.b >= 0.0
        assert spec.c >= 0.0

    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_bui_parameters_valid(self, fuel_type):
        """BUI parameters q and bui0 should be in valid ranges."""
        spec = FUEL_TYPES[fuel_type]
        assert 0.0 < spec.q <= 1.0, f"{fuel_type}: q={spec.q} out of range"
        assert spec.bui0 >= 0.0, f"{fuel_type}: bui0={spec.bui0} negative"

    @pytest.mark.parametrize("fuel_type", list(FuelType))
    def test_sfc_positive(self, fuel_type):
        """Surface fuel consumption (computed from BUI/FFMC) must be positive at BUI 80."""
        from firesim.fbp.calculator import calculate_sfc

        assert calculate_sfc(FUEL_TYPES[fuel_type], ffmc=90.0, bui=80.0) > 0.0

    def test_conifer_types_have_canopy(self):
        """Conifer fuel types should have positive CBH and CFL."""
        conifers = [ft for ft in FuelType if FUEL_TYPES[ft].group == "conifer"]
        for ft in conifers:
            spec = FUEL_TYPES[ft]
            assert spec.cbh > 0.0, f"{ft}: CBH should be > 0 for conifers"
            assert spec.cfl > 0.0, f"{ft}: CFL should be > 0 for conifers"

    def test_grass_types_no_canopy(self):
        """Grass types should have zero CBH and CFL."""
        for ft in (FuelType.O1a, FuelType.O1b):
            spec = FUEL_TYPES[ft]
            assert spec.cbh == 0.0
            assert spec.cfl == 0.0

    def test_deciduous_types_no_canopy(self):
        """Deciduous types should have zero CBH and CFL."""
        for ft in (FuelType.D1, FuelType.D2):
            spec = FUEL_TYPES[ft]
            assert spec.cbh == 0.0
            assert spec.cfl == 0.0


class TestGetFuelSpec:
    """Test fuel type lookup function."""

    def test_lookup_by_enum(self):
        """Should look up by FuelType enum."""
        spec = get_fuel_spec(FuelType.C2)
        assert spec.code == FuelType.C2
        assert spec.name == "Boreal Spruce"

    def test_lookup_by_string(self):
        """Should look up by string code."""
        spec = get_fuel_spec("C2")
        assert spec.code == FuelType.C2

    def test_invalid_string_raises(self):
        """Invalid fuel type string should raise ValueError."""
        with pytest.raises(ValueError):
            get_fuel_spec("XX")

    def test_specs_are_frozen(self):
        """FuelTypeSpec should be immutable."""
        spec = get_fuel_spec(FuelType.C2)
        with pytest.raises(AttributeError):
            spec.a = 999  # type: ignore[misc]
