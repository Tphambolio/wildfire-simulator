"""Date-aware default for grass curing (O-1a/O-1b), Edmonton (decision M1, 2026-10-10).

O-1 spread scales with the curing factor CF (Wotton et al. 2009, GLC-X-10, eqs 35a/35b, p.9):
CF = 0.176 + 0.02 (C - 58.8) for C >= 58.8, so CF is 0.20 at 60 %, 0.80 at 90 %, 0.90 at
95 % and 1.00 at 100 %. The old fixed 60 % default therefore ran grass at a fifth of its fully
cured rate.

Rule:

- **Pre-green-up window** (day of year ``SPRING_WINDOW_DOY[0]`` to ``SPRING_WINDOW_DOY[1] - 1``,
  i.e. 1 March to 29 May in a common year): default ``SPRING_CURING_DEFAULT`` = 95 %. Grass
  between snow-melt and green-up is last season's dead, cured growth; Pickell et al. (2017,
  Sci. Rep. 7:14190) describe this as the period of increased ignition and spread potential
  "from snow melt to vegetation green-up" (abstract), and Beverly & Schroeder (2025, Can. J.
  For. Res. 55, p.14: fire-prone weather "following snow-melt but before green-up") and NRC
  (2021, National Guide for WUI Fires, p.25) name the same pre-green-up hazard. 95 % (CF 0.90) sits between GLC-X-26's generic default for
  unobserved curing, 90 % (CFS 2021, Information Report GLC-X-26, p.24), and fully cured
  (100 %, CF 1.0), allowing for the first green shoots late in the window.
- **Outside the window** there is no default: the user must enter curing (``None`` here).

The window dates are FireSim's choice [H]: the start (1 March) follows the earliest dates
Pickell et al. (2017) counted for spring human-caused ignitions (26 February, DOY 57); the end
(DOY 150) is the green-up date the validation harness already uses for Alberta's boreal
(``RunOptions.greenup_doy``) and lies inside the green-up range Pickell et al. observed across
Alberta's forested natural sub-regions in 2000-2016 (DOY 97-164). It is a climatological
window, not an observation: early or late springs move green-up by weeks, so the value stays
editable.
"""

from __future__ import annotations

# Day-of-year window [start, end) in which the spring default applies (FireSim choice [H]).
SPRING_WINDOW_DOY: tuple[int, int] = (60, 150)

# Default degree of curing (%) inside the window (FireSim choice [H] between GLC-X-26's
# 90 % unobserved default and 100 % fully cured spring grass).
SPRING_CURING_DEFAULT: float = 95.0


def in_spring_curing_window(day_of_year: int | None) -> bool:
    """True when ``day_of_year`` lies in the pre-green-up window (snow-melt to green-up)."""
    if day_of_year is None:
        return False
    start, end = SPRING_WINDOW_DOY
    return start <= int(day_of_year) < end


def default_grass_cure(day_of_year: int | None) -> float | None:
    """Default degree of curing (%) for ``day_of_year``, or None where the user must enter it.

    Returns ``SPRING_CURING_DEFAULT`` in the pre-green-up window and None outside it or when
    the date is unknown: outside the window FireSim has no defensible silent default.
    """
    return SPRING_CURING_DEFAULT if in_spring_curing_window(day_of_year) else None


def curing_required_message(day_of_year: int | None) -> str:
    """Validation message when curing is missing and O-1 grass can burn."""
    start, end = SPRING_WINDOW_DOY
    when = (f"day of year {day_of_year} is outside" if day_of_year is not None
            else "no date was given (start_time or fuel_modifiers.day_of_year), so it cannot "
                 "be placed in")
    return (
        "fuel_modifiers.grass_cure is required when O-1a/O-1b grass can burn: "
        f"{when} the pre-green-up window (day of year {start}-{end - 1}, about 1 March to "
        f"29 May) where FireSim defaults to {SPRING_CURING_DEFAULT:g} % cured. Enter the "
        "observed or estimated degree of curing (0-100 %)."
    )
