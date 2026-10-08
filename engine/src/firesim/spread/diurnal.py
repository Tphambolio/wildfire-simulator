"""Diurnal burning: the burning period (opt-in).

FireSim's hourly weather stream already gives a diurnal rate of spread: wind changes hour by
hour, and the fine fuel moisture follows the hourly FFMC model (Van Wagner 1977), so ISI and
the FBP rates fall overnight and peak in the afternoon. That is the approach of Beck et al.
(2002), who forecast diurnal fire intensity from hourly FFMC (Lawson et al. 1996) and hourly
wind through the FBP System. For it to work the hourly FFMC must start from a realistic
morning value: the daily FFMC describes mid-afternoon conditions (about 16:00 LST, Lawson et
al. 1996; Van Wagner 1987), so starting an early-morning run from it overstates the morning
rates. Pass hourly records from the previous afternoon with negative ``hours_from_start``;
``Simulator.weather_schedule`` uses them only to advance FFMC to the start ("spin-up").

A **burning period** is the operational alternative used in Canadian fire growth modelling
(Prometheus / W.I.S.E. "burning conditions": fire spreads only within set daily hours and
weather limits; Tymstra et al. 2010). ``burning_period_schedule`` multiplies each weather
period's ROS by 1 inside the daily window and by ``off_factor`` (default 0: no spread)
outside it. Both are opt-in; with neither, behaviour is unchanged.

References:
    Beck, J.A., Alexander, M.E., Harvey, S.D., Beaver, A.K. (2002). Forecasting diurnal
        variations in fire intensity to enhance wildland firefighter safety. Int. J. Wildland
        Fire 11, 173-182.
    Lawson, B.D., Armitage, O.B., Hoskins, W.D. (1996). Diurnal variation in the Fine Fuel
        Moisture Code: tables and computer source code. FRDA Report 245.
    Tymstra, C., Bryce, R.W., Wotton, B.M., Taylor, S.W., Armitage, O.B. (2010). Development
        and structure of Prometheus: the Canadian wildland fire growth simulation model.
        Inf. Rep. NOR-X-417.
    Van Wagner, C.E. (1977). A method of computing fine fuel moisture content throughout the
        diurnal cycle. Inf. Rep. PS-X-69.
    Van Wagner, C.E. (1987). Development and structure of the Canadian Forest Fire Weather
        Index System. Forestry Tech. Rep. 35.
"""

from __future__ import annotations

import math
from dataclasses import replace

from firesim.spread.huygens import SpreadConditions
from firesim.types import HourlyWeather

# The daily FFMC describes mid-afternoon moisture (about 16:00 LST = 17:00 local daylight /
# Alberta's year-round UTC-6 clock; Lawson et al. 1996), so the hourly FFMC spin-up starts at
# this local clock hour: the most recent one at or before the start.
SPINUP_FROM_HOUR = 17.0

_EPS_H = 0.05  # hours: tolerance when matching record times to the spin-up start (3 min)


def spinup_hours(start_hour: float, from_hour: float = SPINUP_FROM_HOUR) -> float:
    """Hours from the most recent ``from_hour`` (local clock) at or before the start to the
    start: 13 for a 06:00 start, 20.5 for 13:30, 0 for exactly 17:00."""
    return (start_hour - from_hour) % 24.0


def hourly_for_run(
    records: list[HourlyWeather] | tuple[HourlyWeather, ...] | None,
    start_hour: float | None,
    ffmc_spin_up: bool,
    from_hour: float = SPINUP_FROM_HOUR,
) -> tuple[HourlyWeather, ...] | None:
    """``SimulationConfig.hourly_weather`` from a stream that may start before the run.

    Records with negative ``hours_from_start`` lie before the start. With ``ffmc_spin_up`` the
    stream must reach back to ``from_hour`` local on or before the start (the record in force
    then is moved to start exactly there) and must also cover the run; the hourly FFMC then
    starts from the daily FFMC at ``from_hour`` and runs through the night
    (``Simulator.weather_schedule``). Without it, earlier records are dropped and the one in
    force at the start is moved to hour 0, so the result does not depend on how far back the
    stream reaches.

    Raises:
        ValueError: spin-up without ``start_hour``, without a stream, without records back to
            ``from_hour``, or with no records for the run itself.
    """
    if not records:
        if ffmc_spin_up:
            raise ValueError("ffmc_spin_up needs hourly_weather (overnight and for the run)")
        return None
    recs = sorted(records, key=lambda r: r.hours_from_start)
    back = 0.0
    if ffmc_spin_up:
        if start_hour is None:
            raise ValueError("ffmc_spin_up needs the local start time (start_time)")
        back = spinup_hours(start_hour, from_hour)
    # The record in force at -back (the latest starting at or before it)
    first = None
    for i, r in enumerate(recs):
        if r.hours_from_start <= -back + _EPS_H:
            first = i
    if back > 0.0:
        if first is None:
            raise ValueError(
                f"ffmc_spin_up needs hourly_weather from {int(from_hour):02d}:00 local before "
                f"the start (hours_from_start <= {-back:.2f}); the earliest record is at "
                f"{recs[0].hours_from_start:.2f}"
            )
        if recs[-1].hours_from_start + 1.0 <= 0.0:
            raise ValueError("ffmc_spin_up needs hourly_weather for the run too "
                             "(records at or after hours_from_start 0)")
    if first is None:
        return tuple(recs)  # the stream starts after 0: the base weather applies until then
    kept = recs[first:]
    t0 = -back if back > 0.0 else 0.0
    if kept[0].hours_from_start != t0:
        kept[0] = replace(kept[0], hours_from_start=t0)
    return tuple(kept)


def burning_period_schedule(
    schedule: list[tuple[float, SpreadConditions]],
    start_hour: float,
    burning_period: tuple[float, float],
    duration_min: float,
    off_factor: float = 0.0,
) -> list[tuple[float, SpreadConditions]]:
    """Split ``schedule`` at the burning-period edges and scale ROS outside the period.

    Args:
        schedule: (start minute, conditions) periods in time order, first at minute 0.
        start_hour: Local clock hour at minute 0 (e.g. 6.0 for 06:00).
        burning_period: (first hour, last hour) of the daily burning period, local clock
            (e.g. (10, 20)); the end may be past 24 for a period over midnight.
        duration_min: Run length (minutes); periods are generated up to it.
        off_factor: ROS multiplier outside the burning period (0 = no spread).

    Returns:
        A new schedule; each period's ``ros_multiplier`` is its own value times 1 (inside) or
        ``off_factor`` (outside).
    """
    b0, b1 = burning_period
    if not b1 > b0:
        raise ValueError(f"burning period end {b1} must be after its start {b0}")
    if b1 - b0 >= 24.0:
        return list(schedule)
    edges: set[float] = set()
    n_days = int(math.ceil(duration_min / 1440.0)) + 2
    for day in range(-1, n_days):
        for h in (b0, b1):
            m = ((h - start_hour) + 24.0 * day) * 60.0
            if 0.0 < m < duration_min:
                edges.add(m)
    starts = sorted({s for s, _ in schedule} | edges)

    def conditions_at(m: float) -> SpreadConditions:
        cur = schedule[0][1]
        for s, c in schedule:
            if s <= m + 1e-9:
                cur = c
            else:
                break
        return cur

    def burning(m: float) -> bool:
        clock = (start_hour + m / 60.0 - b0) % 24.0
        return clock < (b1 - b0) - 1e-9

    out = []
    for s in starts:
        c = conditions_at(s)
        k = 1.0 if burning(s + 1e-6) else off_factor
        out.append((s, replace(c, ros_multiplier=c.ros_multiplier * k)))
    return out
