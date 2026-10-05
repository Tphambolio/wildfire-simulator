"""Slope factor for fire spread (FBP System).

In the FBP System slope does not multiply the rate of spread directly. The
slope factor SF (ST-X-3 eq 39) is converted to a slope-equivalent wind speed,
which is added vectorially to the wind to give the net effective wind speed
WSV and spread direction RAZ (ST-X-3 eqs 40-50). That conversion lives in
``firesim.fbp.calculator.calculate_slope_adjustment``; the spread models
use the resulting head/flank/back rates and direction.
"""

from __future__ import annotations

import math


def calculate_slope_factor(slope_percent: float) -> float:
    """Spread factor for ground slope (ST-X-3 eq 39).

    SF = exp(3.533 * (GS/100)^1.2), with SF = 10 for GS >= 70 %.

    Args:
        slope_percent: Terrain slope (%)

    Returns:
        Slope factor multiplier (1.0 to 10.0)
    """
    if slope_percent <= 0.0:
        return 1.0
    if slope_percent >= 70.0:
        return 10.0
    return math.exp(3.533 * (slope_percent / 100.0) ** 1.2)
