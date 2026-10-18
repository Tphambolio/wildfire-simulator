"""Noon-LST hour per CWFIS station where a province has more than one (``services/cwfis_tz.py``).

CWFIS uses each station's fixed ``tz_correct`` (cwfis_allstn2026.csv): Saskatchewan is -6 in the
south and east and -7 in the north-west; BC is -8 except the eastern Mountain-time stations (-7).
Checked against ECCC hourly records on 17 Oct 2026 (Saskatoon/Regina 18 UTC; Waskesiu, Buffalo
Narrows 19 UTC; Kamloops, Prince George 20 UTC; Cranbrook, Dawson Creek 19 UTC).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from firesim_api.routers import weather as w
from firesim_api.services import cwfis_tz
from firesim_api.services.cwfis_tz_data import SOURCE


def _u(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


@pytest.mark.parametrize("prov, lat, lng, tz", [
    ("SA", 53.92, -106.07, -7),   # Waskesiu Lake
    ("SA", 55.83, -108.42, -7),   # Buffalo Narrows
    ("SA", 56.55, -109.42, -7),   # La Loche
    ("SA", 54.41, -108.64, -7),   # Meadow Lake Park
    ("SA", 54.13, -108.52, -6),   # Meadow Lake A (irregular boundary: not a longitude rule)
    ("SA", 52.17, -106.70, -6),   # Saskatoon
    ("SA", 50.43, -104.67, -6),   # Regina
    ("SK", 53.92, -106.07, -7),   # postal code accepted
    ("BC", 50.70, -120.45, -8),   # Kamloops
    ("BC", 53.89, -122.68, -8),   # Prince George
    ("BC", 49.61, -115.78, -7),   # Cranbrook
    ("BC", 55.74, -120.18, -7),   # Dawson Creek
    ("ON", 49.79, -94.36, -6),    # Kenora
    ("ON", 46.49, -80.99, -5),    # Sudbury
])
def test_nearest_station_offset(prov, lat, lng, tz):
    assert cwfis_tz.station_offset(prov, lat, lng) == tz


def test_single_value_provinces_and_missing_lat_fall_back_to_table():
    assert cwfis_tz.station_offset("MB", 49.9, -97.1) is None
    assert cwfis_tz.station_offset("SA", None, -106.0) is None
    assert w._std_offset_hours("SA", -106.07) == -6.0            # no lat: table
    assert w._std_offset_hours("SA", -106.07, lat=53.92) == -7.0  # Waskesiu
    assert w._std_offset_hours("YT", -135.0) == -8.0             # CWFIS tz_correct, not legal UTC-7
    assert w._std_offset_hours("MB", -97.1, lat=49.9) == -6.0
    assert w._std_offset_hours("AB", -113.5, lat=53.5, at=_u("2026-07-15T18:00:00Z")) == -7.0


def test_source_table_counts():
    assert SOURCE["counts"]["SA"] == {-7: 38, -6: 96}
    assert SOURCE["counts"]["BC"] == {-8: 392, -7: 75}
    assert len(SOURCE["sha256"]) == 64


def test_codes_age_uses_the_stations_own_noon():
    """18:30 UTC: before noon at a -7 station (11:30 LST), after it at a -6 station (12:30)."""
    now = _u("2026-07-16T18:30:00Z")
    rep = "2026-07-15T12:00:00Z"
    _, st, label = w._codes_age(rep, now, "SA", -106.07, 53.92)    # Waskesiu
    assert st == "yesterday" and "computed after noon LST" in label
    _, st, label = w._codes_age(rep, now, "SA", -106.70, 52.17)    # Saskatoon
    assert st == "yesterday" and "not yet published" in label


def test_gem_noon_hour_follows_the_point():
    """The GEM noon hour for a point near Buffalo Narrows is 19 UTC, near Regina 18 UTC."""
    hours = [int(_u(f"2026-07-{d:02d}T{h:02d}:00:00Z").timestamp()) for d in (14, 15, 16) for h in range(24)]
    data = {"timezone": "America/Regina", "hourly": {
        "time": hours, "temperature_2m": [float(i % 24) for i in range(72)],
        "relative_humidity_2m": [40.0] * 72, "wind_speed_10m": [10.0] * 72,
        "wind_direction_10m": [270.0] * 72, "precipitation": [0.0] * 72,
    }}
    now = _u("2026-07-16T21:00:00Z")
    nw = w._noon_weather(data, now, "SA", -108.42, 55.83)
    assert nw["time_utc"] == "2026-07-16T19:00:00Z"
    se = w._noon_weather(data, now, "SA", -104.67, 50.43)
    assert se["time_utc"] == "2026-07-16T18:00:00Z"
