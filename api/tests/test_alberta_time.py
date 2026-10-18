"""Alberta's permanent UTC-6 clock (from 2026-11-01) and the fire-weather observation hour.

* Wall clock: MST/MDT before 2026-11-01 02:00 MDT (historical replays keep them), UTC-6 "MT"
  after, even where the environment's tz data predates IANA 2026c.
* Fire-weather day: the daily (noon LST) observation stays at 19 UTC (CWFIS tz_correct -7 for
  Alberta) unless FIRESIM_AB_FWI_LST_OFFSET_H says otherwise; always 19 UTC before 2026-11-01.
* The pinned pip ``tzdata`` package has the 2026c rule.
"""

from __future__ import annotations

import importlib.resources
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from firesim_api.services import alberta_time as at
from firesim_api.services import pyra


def _u(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


@pytest.mark.parametrize("iso, offset, label, clock", [
    ("2011-05-15T20:00:00Z", -6.0, "MDT", "14:00"),   # Slave Lake 2011 replay
    ("2011-01-15T19:00:00Z", -7.0, "MST", "12:00"),
    ("2026-10-31T18:00:00Z", -6.0, "MDT", "12:00"),
    ("2026-11-01T07:59:00Z", -6.0, "MDT", "01:59"),
    ("2026-11-01T08:00:00Z", -6.0, "MT", "02:00"),    # no fall-back
    ("2026-11-02T18:00:00Z", -6.0, "MT", "12:00"),
    ("2027-03-15T18:00:00Z", -6.0, "MT", "12:00"),    # no spring-forward either
    ("2027-07-15T18:00:00Z", -6.0, "MT", "12:00"),
])
def test_wall_clock_and_labels(iso, offset, label, clock):
    t = _u(iso)
    assert at.edmonton_offset_hours(t) == offset
    assert at.edmonton_label(t) == label
    assert at.to_edmonton(t).strftime("%H:%M") == clock


def test_stale_tz_data_is_overridden(monkeypatch):
    """tz data before 2026c would give UTC-7 in winter after the change; the law wins."""
    monkeypatch.setattr(at, "_EDMONTON", ZoneInfo("Etc/GMT+7"))
    assert at.edmonton_offset_hours(_u("2026-12-01T18:00:00Z")) == -6.0
    assert at.to_edmonton(_u("2026-12-01T18:00:00Z")).strftime("%H:%M") == "12:00"
    # before the change the tz data is used as is
    assert at.edmonton_offset_hours(_u("2026-01-15T18:00:00Z")) == -7.0


def test_pip_tzdata_has_alberta_2026c():
    """The pinned tzdata package (api/pyproject.toml) has no fall-back on 2026-11-01."""
    with importlib.resources.files("tzdata.zoneinfo.America").joinpath("Edmonton").open("rb") as f:
        z = ZoneInfo.from_file(f, key="America/Edmonton")
    for iso in ("2026-11-02T18:00:00Z", "2027-01-15T18:00:00Z", "2027-03-15T18:00:00Z"):
        assert _u(iso).astimezone(z).utcoffset().total_seconds() == -6 * 3600
    assert _u("2026-01-15T18:00:00Z").astimezone(z).utcoffset().total_seconds() == -7 * 3600


@pytest.mark.parametrize("d, noon", [
    (date(2011, 5, 15), 19),
    (date(2026, 10, 31), 19),
    (date(2026, 11, 1), 19),
    (date(2026, 11, 2), 19),
    (date(2027, 3, 15), 19),
])
def test_fwi_noon_hour_default_19_utc(d, noon):
    assert at.fwi_noon_utc_hour(d) == noon
    assert at.fwi_noon_utc(d) == datetime(d.year, d.month, d.day, noon, tzinfo=timezone.utc)


def test_fwi_noon_hour_configurable_after_change(monkeypatch):
    """FIRESIM_AB_FWI_LST_OFFSET_H=6 (12:00 MT = 18 UTC) applies only from 2026-11-01."""
    monkeypatch.setattr(at, "AB_FWI_LST_OFFSET_H_AFTER", 6)
    assert at.fwi_noon_utc_hour(date(2011, 5, 15)) == 19
    assert at.fwi_noon_utc_hour(date(2026, 10, 31)) == 19
    assert at.fwi_noon_utc_hour(date(2026, 11, 1)) == 18
    assert at.fwi_noon_utc_hour(date(2027, 3, 15)) == 18
    # CFFDRS day boundaries: 31 Oct ends at 07 UTC, 1 Nov runs 07 UTC -> 06 UTC on 2 Nov
    assert at.fwi_lst_date(_u("2026-11-01T06:59:00Z")) == date(2026, 10, 31)
    assert at.fwi_lst_date(_u("2026-11-01T07:00:00Z")) == date(2026, 11, 1)
    assert at.fwi_lst_date(_u("2026-11-02T06:00:00Z")) == date(2026, 11, 2)
    # Pyra tier: pre-noon until 18 UTC, not 19
    assert pyra.is_pre_noon(_u("2026-11-02T17:59:00Z"))
    assert not pyra.is_pre_noon(_u("2026-11-02T18:00:00Z"))


def test_env_value_parsing(monkeypatch):
    for raw, want in (("6", 6), ("7", 7), (" 6 ", 6), ("8", 7), ("x", 7)):
        monkeypatch.setenv("FIRESIM_AB_FWI_LST_OFFSET_H", raw)
        assert at._fwi_offset_after() == want


@pytest.mark.parametrize("iso, lst_day, pre_noon", [
    ("2011-05-15T18:59:00Z", date(2011, 5, 15), True),
    ("2026-10-31T19:00:00Z", date(2026, 10, 31), False),
    ("2026-11-02T18:30:00Z", date(2026, 11, 2), True),    # 12:30 MT, before the 19 UTC obs
    ("2026-11-02T19:30:00Z", date(2026, 11, 2), False),
    ("2027-03-15T06:30:00Z", date(2027, 3, 14), False),   # 00:30 MT: still the 14th's CFFDRS day (23:30 LST)
])
def test_pyra_tier_day_and_pre_noon(iso, lst_day, pre_noon):
    t = _u(iso)
    assert pyra.lst_date(t) == lst_day
    assert pyra.is_pre_noon(t) is pre_noon


def test_pyra_gem_noon_picks_the_19_utc_hour_after_the_change():
    hours = [f"2026-11-02T{h:02d}:00" for h in range(24)]
    data = {"hourly": {
        "time": hours, "temperature_2m": [float(h) for h in range(24)],
        "relative_humidity_2m": [40.0] * 24, "wind_speed_10m": [10.0] * 24,
        "wind_direction_10m": [270.0] * 24, "precipitation": [0.0] * 24,
    }}
    w = pyra.gem_noon(data, _u("2026-11-02T20:00:00Z"))
    assert w["time_utc"] == "2026-11-02T19:00:00Z"
    assert w["temperature"] == 19.0
    assert w["peak"]["temperature"] == 22.0   # 16:00 MT = 22 UTC


@pytest.mark.parametrize("now, status, before_noon_text", [
    ("2026-10-31T18:30:00Z", "yesterday", True),    # 11:30 LST
    ("2026-11-02T18:30:00Z", "yesterday", True),    # 12:30 MT, still before the 19 UTC obs
    ("2026-11-02T19:30:00Z", "yesterday", False),   # after it, today's not yet published
    ("2027-03-15T18:30:00Z", "yesterday", True),
])
def test_weather_codes_age_across_the_change(now, status, before_noon_text):
    from firesim_api.routers import weather as w

    t = _u(now)
    yday = (at.fwi_lst_date(t).toordinal() - 1)
    rep = date.fromordinal(yday).isoformat() + "T12:00:00Z"
    _, st, label = w._codes_age(rep, t, "AB", -113.5)
    assert st == status
    assert ("computed after noon LST" in label) is before_noon_text
