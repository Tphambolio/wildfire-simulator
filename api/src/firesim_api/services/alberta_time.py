"""Alberta's clock and the fire-weather observation hour (one place for both).

Two different "times" matter for FireSim and Pyra, and Alberta's 2026 clock change moves only one
of them:

1. **The legal wall clock** (``America/Edmonton``). Alberta's Official Time Act puts the province
   on UTC-6 all year (in force 2026-06-18; IANA tzdata 2026c, July 2026): the clocks did not fall
   back on 2026-11-01 02:00 MDT. Before that instant the usual MST (UTC-7) / MDT (UTC-6) rule
   applies, so historical replays (e.g. Slave Lake, 15 May 2011, MDT) keep their old offsets.
   :func:`edmonton_offset_hours` derives the offset from ``zoneinfo`` and, if the environment's
   tz data predates 2026c (it would say UTC-7 after 2026-11-01), applies the dated rule instead
   and logs a warning once. Display labels: "MST"/"MDT" before the change, "MT" after (owner's
   choice, 2026-10-18; the IANA abbreviation "CST" reads as Central time to Albertans).

2. **The daily fire-weather observation hour** ("noon LST", the CFFDRS day; Van Wagner 1987;
   Lawson & Armitage 2008). This is NOT taken from the legal clock. CWFIS assigns each station
   a fixed "UTC-LST" correction "estimated from coordinate ranges and maps" (``tz_correct`` in
   cwfis_allstn2026.csv; every Alberta station but one is -7), so Alberta's daily values are the
   19 UTC observation all year; Saskatchewan, legally on UTC-6 since the 1960s, is split -6/-7
   by longitude. Alberta Wildfire's PM Actual is defined in MST (12:00 MST = 19 UTC) and ACIS
   time-stamps in MST. Checked 2026-10-18 by matching CWFIS daily values to ECCC hourly records
   (report "Alberta permanent UTC-6 switch 2026-10-18"). No agency has said what it will do
   after 2026-11-01, so the offset from that date is ONE configurable value,
   ``FIRESIM_AB_FWI_LST_OFFSET_H`` (default 7, i.e. 19 UTC = 13:00 MT; set 6 for 18 UTC = 12:00
   MT if CWFIS/Alberta Wildfire move). Before 2026-11-01 it is always 7.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

EDMONTON_TZ_NAME = "America/Edmonton"
_EDMONTON = ZoneInfo(EDMONTON_TZ_NAME)

# 2026-11-01 02:00 MDT: the fall-back that did not happen; UTC-6 all year from this instant.
ALBERTA_FIXED_UTC6_FROM = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
# First CFFDRS day on the new rule (the fire-weather offset below is keyed on the date).
ALBERTA_FIXED_UTC6_FROM_DATE = date(2026, 11, 1)
ALBERTA_FIXED_OFFSET_H = -6.0
# Label for Alberta wall-clock times from ALBERTA_FIXED_UTC6_FROM on (owner's choice).
ALBERTA_TIME_LABEL = "MT"

# Fire-weather "LST" offset (hours behind UTC) for Alberta CFFDRS days before 2026-11-01.
AB_FWI_LST_OFFSET_H_BEFORE = 7


def _fwi_offset_after() -> int:
    raw = os.environ.get("FIRESIM_AB_FWI_LST_OFFSET_H", "7").strip()
    try:
        v = int(raw)
    except ValueError:
        logger.warning("FIRESIM_AB_FWI_LST_OFFSET_H=%r is not 6 or 7; using 7", raw)
        return 7
    if v not in (6, 7):
        logger.warning("FIRESIM_AB_FWI_LST_OFFSET_H=%r is not 6 or 7; using 7", raw)
        return 7
    return v


# Fire-weather "LST" offset for CFFDRS days from 2026-11-01 (configurable; see the module doc).
AB_FWI_LST_OFFSET_H_AFTER = _fwi_offset_after()

_warned_stale = False


def _utc(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("a timezone-aware datetime is required")
    return at.astimezone(timezone.utc)


def tzdata_has_alberta_utc6() -> bool:
    """True if this environment's tz data has Alberta's permanent UTC-6 (IANA 2026c or later)."""
    probe = datetime(2026, 12, 1, 18, tzinfo=timezone.utc).astimezone(_EDMONTON)
    return probe.utcoffset() == timedelta(hours=-6)


def edmonton_offset_hours(at: datetime) -> float:
    """UTC offset (hours) of Alberta's wall clock at ``at``: -7 (MST) or -6 (MDT) before
    2026-11-01 08:00 UTC from the tz data, -6 from then on whatever the tz data says."""
    global _warned_stale
    u = _utc(at)
    off = u.astimezone(_EDMONTON).utcoffset() or timedelta(0)
    hours = off.total_seconds() / 3600.0
    if u >= ALBERTA_FIXED_UTC6_FROM and hours != ALBERTA_FIXED_OFFSET_H:
        if not _warned_stale:
            logger.warning(
                "tz data predates IANA 2026c (America/Edmonton gives UTC%+.0f after 2026-11-01); "
                "using Alberta's permanent UTC-6. Update tzdata.", hours)
            _warned_stale = True
        return ALBERTA_FIXED_OFFSET_H
    return hours


def to_edmonton(at: datetime) -> datetime:
    """``at`` on Alberta's wall clock, as an aware datetime with a fixed offset."""
    off = timedelta(hours=edmonton_offset_hours(at))
    return _utc(at).astimezone(timezone(off))


def edmonton_label(at: datetime) -> str:
    """"MST" / "MDT" before 2026-11-01 02:00 MDT, "MT" from then on."""
    if _utc(at) >= ALBERTA_FIXED_UTC6_FROM:
        return ALBERTA_TIME_LABEL
    return "MDT" if edmonton_offset_hours(at) == -6.0 else "MST"


def fwi_lst_offset_on(d: date) -> int:
    """Hours behind UTC of Alberta's fire-weather "LST" for the CFFDRS day ``d``."""
    return AB_FWI_LST_OFFSET_H_AFTER if d >= ALBERTA_FIXED_UTC6_FROM_DATE else AB_FWI_LST_OFFSET_H_BEFORE


def fwi_lst_offset_at(at: datetime) -> int:
    """Fire-weather "LST" offset (hours behind UTC) in force at the instant ``at``.

    The CFFDRS day 2026-11-01 starts at 00:00 under the old offset (07:00 UTC); from that
    instant the new day's offset applies.
    """
    u = _utc(at)
    start_new = datetime(2026, 11, 1, AB_FWI_LST_OFFSET_H_BEFORE, tzinfo=timezone.utc)
    return AB_FWI_LST_OFFSET_H_AFTER if u >= start_new else AB_FWI_LST_OFFSET_H_BEFORE


def fwi_lst(at: datetime) -> datetime:
    """``at`` as a naive datetime on Alberta's fire-weather LST clock."""
    return (_utc(at) - timedelta(hours=fwi_lst_offset_at(at))).replace(tzinfo=None)


def fwi_lst_date(at: datetime) -> date:
    """The CFFDRS day (fire-weather LST date) at ``at``."""
    return fwi_lst(at).date()


def fwi_noon_utc_hour(d: date) -> int:
    """UTC hour of the daily (noon LST) observation for Alberta's CFFDRS day ``d``: 19 before
    2026-11-01; from then 19 by default (``FIRESIM_AB_FWI_LST_OFFSET_H``)."""
    return 12 + fwi_lst_offset_on(d)


def fwi_noon_utc(d: date) -> datetime:
    """The instant of the daily observation for the CFFDRS day ``d``."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=fwi_noon_utc_hour(d))
