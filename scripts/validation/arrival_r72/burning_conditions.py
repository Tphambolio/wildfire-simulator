"""[R72] candidate fix: an ISI-conditioned burning period ("burning conditions").

FireSim's operational burning period (``spread/diurnal.burning_period_schedule``) multiplies ROS by
1 inside 10-20 h local and by 0 outside it, whatever the weather. Prometheus / W.I.S.E. instead
let a fire spread outside the daily window when weather "burning conditions" hold (Tymstra et al.
2010, Inf. Rep. NOR-X-417). This candidate keeps the 10-20 h window and, outside it, lets an hour
spread (ROS x 1) when FireSim's own hourly Initial Spread Index for that hour, from the hourly FFMC
the weather schedule already carries and the hour's 10 m wind (ST-X-3 eq 52-53,
``fbp.calculator.calculate_isi``), is at least ``isi_min``; other hours outside the window stay at
ROS x 0. Inside the window nothing changes.

Implemented in analysis code (a schedule transform applied to ``Simulator.weather_schedule()`` of a
config run without a burning period, whose periods are whole clock hours from a 06:00 start), so
no engine code changes for the diagnosis or the pre-registered test.
"""
from __future__ import annotations

from dataclasses import replace

from firesim.fbp.calculator import calculate_isi


def conditioned_schedule(schedule, start_hour: float, window=(10.0, 20.0), isi_min: float = 12.0):
    """Schedule with ROS x 0 outside ``window`` except in periods whose hourly ISI >= isi_min."""
    b0, b1 = window
    out = []
    for t, c in schedule:
        clock = (start_hour + t / 60.0) % 24.0
        inside = b0 <= clock < b1
        burn = inside or calculate_isi(c.ffmc, c.wind_speed) >= isi_min
        out.append((t, c if burn else replace(c, ros_multiplier=0.0)))
    return out
