"""Canadian Fire Behavior Prediction (FBP) System Calculator.

Implements equations from:
    Forestry Canada Fire Danger Group (1992).
    Development and Structure of the Canadian Forest Fire Behavior
    Prediction System. Information Report ST-X-3.
"""

from __future__ import annotations

import math

from firesim.fbp.constants import FuelType, FuelTypeSpec, FUEL_TYPES, get_fuel_spec
from firesim.fbp.crown_fire import calculate_crown_fire, calculate_crown_ros
from firesim.types import FBPResult, FireType


def calculate_isi(ffmc: float, wind_speed: float) -> float:
    """Calculate Initial Spread Index from FFMC and wind speed.

    Args:
        ffmc: Fine Fuel Moisture Code (0-101)
        wind_speed: 10-m open wind speed (km/h)

    Returns:
        ISI value (dimensionless)
    """
    m = 147.2 * (101.0 - ffmc) / (59.5 + ffmc)
    f_f = 91.9 * math.exp(-0.1386 * m) * (1.0 + m**5.31 / 4.93e7)
    f_w = math.exp(0.05039 * wind_speed)
    return 0.208 * f_f * f_w


def calculate_bui(dmc: float, dc: float) -> float:
    """Calculate Buildup Index from DMC and DC.

    Args:
        dmc: Duff Moisture Code
        dc: Drought Code

    Returns:
        BUI value (dimensionless)
    """
    if dmc == 0.0 and dc == 0.0:
        return 0.0
    if dmc <= 0.4 * dc:
        bui = 0.8 * dmc * dc / (dmc + 0.4 * dc)
    else:
        bui = dmc - (1.0 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + (0.0114 * dmc) ** 1.7)
    return max(0.0, bui)


def calculate_bui_effect(bui: float, q: float, bui0: float) -> float:
    """Calculate BUI effect on rate of spread.

    ST-X-3: BE = exp(50 * ln(q) * (1/BUI - 1/BUI0))

    Args:
        bui: Buildup Index
        q: BUI effect parameter (fuel-type specific)
        bui0: BUI threshold parameter (fuel-type specific)

    Returns:
        BUI effect multiplier (dimensionless)
    """
    if bui <= 0.0 or q >= 1.0:
        return 1.0
    return math.exp(50.0 * math.log(q) * (1.0 / bui - 1.0 / bui0))


def calculate_grass_curing_factor(grass_cure: float) -> float:
    """Calculate grass curing factor for O-1a/O-1b fuel types.

    ST-X-3 curing factor:
        if PC < 58.8: CF = 0.176 + 0.020 * (PC - 58.8)
        else: CF = 0.176 + 0.020 * (PC - 58.8) * (1 - 0.008 * (PC - 58.8))

    Args:
        grass_cure: Percent curing (0-100). 0=green, 100=fully cured/dead.

    Returns:
        Curing factor (0.0 to ~0.714)
    """
    pc = float(grass_cure)
    if pc < 58.8:
        cf = 0.176 + 0.020 * (pc - 58.8)
    else:
        delta = pc - 58.8
        cf = 0.176 + 0.020 * delta * (1.0 - 0.008 * delta)
    return max(0.0, min(1.0, cf))


def _calculate_surface_ros(
    spec: FuelTypeSpec,
    isi: float,
    bui: float,
    pc: float = 50.0,
    grass_cure: float = 60.0,
) -> float:
    """Calculate surface rate of spread (m/min).

    Args:
        spec: Fuel type specification
        isi: Initial Spread Index
        bui: Buildup Index
        pc: Percent conifer (0-100, for M1/M2 types)
        grass_cure: Percent curing (0-100, for O1a/O1b types)

    Returns:
        Surface ROS in m/min
    """
    fuel = spec.code

    # M1/M2 mixedwood: weighted blend of C2 and D1
    if fuel in (FuelType.M1, FuelType.M2):
        c2 = FUEL_TYPES[FuelType.C2]
        d1 = FUEL_TYPES[FuelType.D1]

        ros_c = c2.a * (1.0 - math.exp(-c2.b * isi)) ** c2.c
        ros_d = d1.a * (1.0 - math.exp(-d1.b * isi)) ** d1.c

        # Apply BUI effect to conifer component only
        be = calculate_bui_effect(bui, c2.q, c2.bui0)
        ros_c *= be

        # M2 (green) applies a greenup reduction to the deciduous component
        if fuel == FuelType.M2:
            ros_d *= 0.2

        ros = (pc / 100.0) * ros_c + (1.0 - pc / 100.0) * ros_d
        return ros

    # Standard fuel types: ROS = a * (1 - exp(-b * ISI))^c
    ros = spec.a * (1.0 - math.exp(-spec.b * isi)) ** spec.c

    # Apply BUI effect for conifer and slash types
    if spec.group in ("conifer", "slash", "mixedwood"):
        be = calculate_bui_effect(bui, spec.q, spec.bui0)
        ros *= be

    # Grass curing factor for O1a/O1b
    if fuel in (FuelType.O1a, FuelType.O1b):
        cf = calculate_grass_curing_factor(grass_cure)
        ros *= cf

    return ros


def _calculate_flame_length(hfi: float) -> float:
    """Calculate flame length from head fire intensity.

    Byram (1959): L = 0.0775 * I^0.46

    Args:
        hfi: Head fire intensity (kW/m)

    Returns:
        Flame length in meters
    """
    if hfi <= 0.0:
        return 0.0
    return 0.0775 * hfi**0.46


def calculate_fbp(
    fuel_type: FuelType | str,
    wind_speed: float,
    ffmc: float,
    dmc: float,
    dc: float,
    slope: float = 0.0,
    pc: float = 50.0,
    grass_cure: float = 60.0,
    fmc: float = 100.0,
) -> FBPResult:
    """Calculate complete fire behavior prediction.

    This is the main entry point for FBP calculations.

    Args:
        fuel_type: FBP fuel type code (e.g., FuelType.C2 or "C2")
        wind_speed: 10-m open wind speed (km/h)
        ffmc: Fine Fuel Moisture Code (0-101)
        dmc: Duff Moisture Code
        dc: Drought Code
        slope: Percent slope (0-100+)
        pc: Percent conifer (0-100, for M1/M2 types)
        grass_cure: Percent curing (0-100, for O1a/O1b types)
        fmc: Foliar moisture content (%, for crown fire calculation)

    Returns:
        FBPResult with all fire behavior outputs
    """
    if isinstance(fuel_type, str):
        fuel_type = FuelType(fuel_type)
    spec = get_fuel_spec(fuel_type)

    # Calculate FWI sub-indices
    isi = calculate_isi(ffmc, wind_speed)
    bui = calculate_bui(dmc, dc)

    # Surface rate of spread
    ros_surface = _calculate_surface_ros(spec, isi, bui, pc, grass_cure)

    # Apply basic slope factor (directional slope is in spread/slope.py)
    if slope > 0:
        sf = math.exp(3.533 * (slope / 100.0) ** 1.2)
        sf = min(sf, 2.0)  # Butler (2007) cap
        ros_surface *= sf

    # Surface fuel consumption and intensity
    sfc = spec.sfc
    h = 18000.0  # Low heat of combustion (kJ/kg)
    sfi = h * sfc * ros_surface / 60.0

    # Crown fire assessment
    crown = calculate_crown_fire(spec, sfi, fmc)
    cfb = crown["cfb"]
    fire_type = crown["fire_type"]
    ros_crown = calculate_crown_ros(ros_surface, spec) if cfb > 0.0 else ros_surface

    # Final ROS combining surface and crown
    ros_final = ros_surface * (1.0 - cfb) + ros_crown * cfb

    # Total fuel consumption and head fire intensity
    cfc = cfb * spec.cfl
    tfc = sfc + cfc
    hfi = h * tfc * ros_final / 60.0

    # Flame length (Byram 1959)
    flame_length = _calculate_flame_length(hfi)

    return FBPResult(
        fuel_type=fuel_type.value,
        isi=isi,
        bui=bui,
        ros_surface=ros_surface,
        ros_final=ros_final,
        sfc=sfc,
        cfc=cfc,
        tfc=tfc,
        sfi=sfi,
        hfi=hfi,
        cfb=cfb,
        fire_type=fire_type,
        flame_length=flame_length,
    )
