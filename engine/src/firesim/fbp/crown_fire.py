"""Crown fire initiation and behavior (FBP System).

Implements the crown fire equations of the Canadian FBP System:
Van Wagner (1977) critical surface intensity, the critical surface rate of
spread RSO, crown fraction burned CFB, and the C-6 crown rate of spread.

References:
    Van Wagner, C.E. (1977). Conditions for the start and spread of crown fire.
    Canadian Journal of Forest Research, 7(1), 23-34.
    Forestry Canada Fire Danger Group (1992). ST-X-3, eqs 56-58, 64-66.
    Wotton, B.M., Alexander, M.E., Taylor, S.W. (2009). GLC-X-10.
"""

from __future__ import annotations

import math

from firesim.types import FireType


def calculate_critical_surface_intensity(cbh: float, fmc: float = 100.0) -> float:
    """Critical surface fire intensity for crown fire initiation (ST-X-3 eq 56).

    CSI = 0.001 * CBH^1.5 * (460 + 25.9 * FMC)^1.5

    Args:
        cbh: Crown base height (m)
        fmc: Foliar moisture content (%)

    Returns:
        Critical surface intensity (kW/m). Returns 0 if CBH is 0 (no canopy).
    """
    if cbh <= 0.0:
        return 0.0
    return 0.001 * cbh**1.5 * (460.0 + 25.9 * fmc) ** 1.5


def calculate_critical_surface_ros(csi: float, sfc: float) -> float:
    """Critical surface rate of spread for crowning (ST-X-3 eq 57).

    RSO = CSI / (300 * SFC)

    Args:
        csi: Critical surface intensity (kW/m)
        sfc: Surface fuel consumption (kg/m2)

    Returns:
        RSO (m/min). Infinite when SFC is zero (no surface fire can crown).
    """
    if sfc <= 0.0:
        return math.inf
    return csi / (300.0 * sfc)


def calculate_crown_fraction_burned(ros: float, rso: float) -> float:
    """Crown fraction burned (ST-X-3 eq 58).

    CFB = 1 - exp(-0.23 * (ROS - RSO)) when ROS > RSO, else 0.

    Args:
        ros: Surface rate of spread (m/min)
        rso: Critical surface rate of spread (m/min)

    Returns:
        Crown fraction burned (0.0 to 1.0)
    """
    if ros > rso:
        return 1.0 - math.exp(-0.23 * (ros - rso))
    return 0.0


def calculate_crown_ros_c6(isi: float, fmc: float) -> float:
    """C-6 crown fire rate of spread (ST-X-3 eqs 64-66).

    RSC = 60 * (1 - exp(-0.0497 * ISI)) * FME / 0.778,
    FME = 1000 * (1.5 - 0.00275 * FMC)^4 / (460 + 25.9 * FMC)

    Args:
        isi: Initial Spread Index
        fmc: Foliar moisture content (%)

    Returns:
        Crown rate of spread (m/min)
    """
    fme = 1000.0 * (1.5 - 0.00275 * fmc) ** 4.0 / (460.0 + 25.9 * fmc)
    return 60.0 * (1.0 - math.exp(-0.0497 * isi)) * fme / 0.778


def classify_fire_type(cfb: float) -> FireType:
    """Classify fire type from crown fraction burned.

    ST-X-3 classes: surface (CFB < 0.1), intermittent crown (0.1-0.9),
    continuous crown (>= 0.9). Surface fires with any crown involvement
    (0 < CFB < 0.1) are labelled SURFACE_WITH_TORCHING.

    Args:
        cfb: Crown fraction burned (0-1)

    Returns:
        FireType classification
    """
    if cfb >= 0.9:
        return FireType.ACTIVE_CROWN
    elif cfb >= 0.1:
        return FireType.PASSIVE_CROWN
    elif cfb > 0.0:
        return FireType.SURFACE_WITH_TORCHING
    else:
        return FireType.SURFACE
