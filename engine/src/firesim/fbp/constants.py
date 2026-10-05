"""Single source of truth for all FBP fuel type parameters.

All fuel type data is defined here as frozen dataclasses. Every other module
that needs fuel parameters imports from this file. This eliminates the V2
problem of having fuel params duplicated in 5 separate locations.

Parameters from:
    Forestry Canada Fire Danger Group (1992).
    Development and Structure of the Canadian Forest Fire Behavior
    Prediction System. Information Report ST-X-3.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FuelType(str, Enum):
    """Canadian FBP fuel type codes."""

    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    C5 = "C5"
    C6 = "C6"
    C7 = "C7"
    D1 = "D1"
    D2 = "D2"
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    O1a = "O1a"
    O1b = "O1b"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


@dataclass(frozen=True)
class FuelTypeSpec:
    """Complete specification for a single FBP fuel type.

    Attributes:
        code: FBP fuel type code (e.g., "C2")
        name: Full descriptive name
        group: Fuel group ("conifer", "deciduous", "mixedwood", "grass", "slash")
        a: ROS equation parameter a (m/min)
        b: ROS equation parameter b
        c: ROS equation parameter c
        q: BUI effect parameter q (dimensionless)
        bui0: BUI effect parameter BUI_0
        cbh: Default crown base height (m) — 0 for non-crown fuel types (ST-X-3 Table 8)
        cfl: Default crown fuel load (kg/m2) — 0 for non-crown fuel types (ST-X-3 Table 8)

    Surface fuel consumption is not a constant: it depends on BUI (and FFMC for
    C-1/C-7, percent conifer for M-1/M-2, grass fuel load for O-1), so it is
    computed by ``calculate_sfc`` in ``firesim.fbp.calculator``.
    """

    code: FuelType
    name: str
    group: str
    a: float
    b: float
    c: float
    q: float
    bui0: float
    cbh: float
    cfl: float


# All 18 Canadian FBP fuel types — the single source of truth.
# Parameters from ST-X-3 Tables 6-8 with the Wotton et al. (2009, GLC-X-10)
# revisions, matching the reference implementation (cffdrs).
FUEL_TYPES: dict[FuelType, FuelTypeSpec] = {
    FuelType.C1: FuelTypeSpec(
        code=FuelType.C1, name="Spruce-Lichen Woodland", group="conifer",
        a=90, b=0.0649, c=4.5, q=0.90, bui0=72,
        cbh=2.0, cfl=0.75,
    ),
    FuelType.C2: FuelTypeSpec(
        code=FuelType.C2, name="Boreal Spruce", group="conifer",
        a=110, b=0.0282, c=1.5, q=0.70, bui0=64,
        cbh=3.0, cfl=0.80,
    ),
    FuelType.C3: FuelTypeSpec(
        code=FuelType.C3, name="Mature Jack or Lodgepole Pine", group="conifer",
        a=110, b=0.0444, c=3.0, q=0.75, bui0=62,
        cbh=8.0, cfl=1.15,
    ),
    FuelType.C4: FuelTypeSpec(
        code=FuelType.C4, name="Immature Jack or Lodgepole Pine", group="conifer",
        a=110, b=0.0293, c=1.5, q=0.80, bui0=66,
        cbh=4.0, cfl=1.20,
    ),
    FuelType.C5: FuelTypeSpec(
        code=FuelType.C5, name="Red and White Pine", group="conifer",
        a=30, b=0.0697, c=4.0, q=0.80, bui0=56,
        cbh=18.0, cfl=1.20,
    ),
    FuelType.C6: FuelTypeSpec(
        code=FuelType.C6, name="Conifer Plantation", group="conifer",
        a=30, b=0.0800, c=3.0, q=0.80, bui0=62,
        cbh=7.0, cfl=1.80,
    ),
    FuelType.C7: FuelTypeSpec(
        code=FuelType.C7, name="Ponderosa Pine/Douglas-fir", group="conifer",
        a=45, b=0.0305, c=2.0, q=0.85, bui0=106,
        cbh=10.0, cfl=0.50,
    ),
    FuelType.D1: FuelTypeSpec(
        code=FuelType.D1, name="Leafless Aspen", group="deciduous",
        a=30, b=0.0232, c=1.6, q=0.90, bui0=32,
        cbh=0.0, cfl=0.0,
    ),
    FuelType.D2: FuelTypeSpec(
        code=FuelType.D2, name="Green Aspen (with BUI threshold)", group="deciduous",
        a=30, b=0.0232, c=1.6, q=0.90, bui0=32,
        cbh=0.0, cfl=0.0,
    ),
    FuelType.M1: FuelTypeSpec(
        code=FuelType.M1, name="Boreal Mixedwood - Leafless", group="mixedwood",
        a=0, b=0.0, c=0.0, q=0.80, bui0=50,
        cbh=6.0, cfl=0.80,
    ),
    FuelType.M2: FuelTypeSpec(
        code=FuelType.M2, name="Boreal Mixedwood - Green", group="mixedwood",
        a=0, b=0.0, c=0.0, q=0.80, bui0=50,
        cbh=6.0, cfl=0.80,
    ),
    FuelType.M3: FuelTypeSpec(
        code=FuelType.M3, name="Dead Balsam Fir Mixedwood - Leafless", group="mixedwood",
        a=120, b=0.0572, c=1.4, q=0.80, bui0=50,
        cbh=6.0, cfl=0.80,
    ),
    FuelType.M4: FuelTypeSpec(
        code=FuelType.M4, name="Dead Balsam Fir Mixedwood - Green", group="mixedwood",
        a=100, b=0.0404, c=1.48, q=0.80, bui0=50,
        cbh=6.0, cfl=0.80,
    ),
    FuelType.O1a: FuelTypeSpec(
        code=FuelType.O1a, name="Matted Grass", group="grass",
        a=190, b=0.0310, c=1.4, q=1.0, bui0=1,
        cbh=0.0, cfl=0.0,
    ),
    FuelType.O1b: FuelTypeSpec(
        code=FuelType.O1b, name="Standing Grass", group="grass",
        a=250, b=0.0350, c=1.7, q=1.0, bui0=1,
        cbh=0.0, cfl=0.0,
    ),
    FuelType.S1: FuelTypeSpec(
        code=FuelType.S1, name="Jack or Lodgepole Pine Slash", group="slash",
        a=75, b=0.0297, c=1.3, q=0.75, bui0=38,
        cbh=0.0, cfl=0.0,
    ),
    FuelType.S2: FuelTypeSpec(
        code=FuelType.S2, name="White Spruce/Balsam Slash", group="slash",
        a=40, b=0.0438, c=1.7, q=0.75, bui0=63,
        cbh=0.0, cfl=0.0,
    ),
    FuelType.S3: FuelTypeSpec(
        code=FuelType.S3, name="Coastal Cedar/Hemlock/Douglas-fir Slash", group="slash",
        a=55, b=0.0829, c=3.2, q=0.75, bui0=31,
        cbh=0.0, cfl=0.0,
    ),
}


def get_fuel_spec(fuel_type: FuelType | str) -> FuelTypeSpec:
    """Look up fuel type specification.

    Args:
        fuel_type: FuelType enum or string code (e.g., "C2")

    Returns:
        FuelTypeSpec for the given fuel type

    Raises:
        KeyError: If fuel type is not found
    """
    if isinstance(fuel_type, str):
        fuel_type = FuelType(fuel_type)
    return FUEL_TYPES[fuel_type]
