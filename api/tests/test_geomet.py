"""ECCC GEM point series from MSC GeoMet (services/geomet.py). No network: GeoMet is mocked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from firesim_api.services import geomet

H = timedelta(hours=1)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _caps_xml(layer: str, times: str, refs: str, ref_default: str) -> str:
    return (
        f'<WMS_Capabilities><Capability><Layer><Layer queryable="1"><Name>{layer}</Name>'
        f'<Dimension name="time" units="ISO8601" default="2026-10-30T05:00:00Z">{times}</Dimension>'
        f'<Dimension name="reference_time" units="ISO8601" default="{ref_default}" '
        f'multipleValues="1">{refs}</Dimension></Layer></Layer></Capability></WMS_Capabilities>'
    )


def _grid(value: float | None) -> str:
    v = "-9999" if value is None else repr(value)
    return ("--wcs\nContent-Type: image/x-aaigrid\n\nncols        1\nnrows        1\n"
            "xllcorner    -113.51\nyllcorner    53.54\ncellsize     0.02\nNODATA_value -9999\n"
            f"{v} \n\n--wcs\nContent-Type: application/octet-stream\n\n<PAMDataset/>\n--wcs--\n")


# ── Parsing ─────────────────────────────────────────────────────────────────
def test_time_dimension_ranges_and_lists():
    got = geomet.parse_time_dimension(
        "2026-10-30T00:00:00Z/2026-10-30T03:00:00Z/PT1H,2026-10-30T06:00:00Z")
    assert got == [_t("2026-10-30T00:00:00Z") + i * H for i in range(4)] + [_t("2026-10-30T06:00:00Z")]


def test_capabilities_parsed():
    xml = _caps_xml("HRDPS.CONTINENTAL_TT", "2026-10-30T00:00:00Z/2026-11-01T00:00:00Z/PT1H",
                    "2026-10-28T18:00:00Z/2026-10-30T00:00:00Z/PT6H", "2026-10-30T00:00:00Z")
    c = geomet.parse_capabilities(xml, "HRDPS.CONTINENTAL_TT")
    assert c.ref == _t("2026-10-30T00:00:00Z")
    assert len(c.refs) == 6 and c.refs[0] == _t("2026-10-28T18:00:00Z")
    assert len(c.times) == 49
    with pytest.raises(ValueError):
        geomet.parse_capabilities(xml, "RDPS_10km_AirTemp_2m")


def test_aaigrid_value_nodata_and_exception():
    assert geomet.parse_aaigrid(_grid(-9.19)) == pytest.approx(-9.19)
    assert geomet.parse_aaigrid(_grid(None)) is None
    assert geomet.parse_aaigrid('<ows:ExceptionReport><ows:ExceptionText>x</ows:ExceptionText>') is None


def test_coverage_request_pins_run_and_point():
    p = dict((k, v) for k, v in geomet.coverage_params(
        "HRDPS.CONTINENTAL_TT", _t("2026-10-30T00:00:00Z"), _t("2026-10-30T12:00:00Z"), 53.55, -113.5))
    assert p["TIME"] == "2026-10-30T12:00:00Z" and p["DIM_REFERENCE_TIME"] == "2026-10-30T00:00:00Z"
    assert p["format"] == "image/x-aaigrid" and p["SCALESIZE"] == "x(1),y(1)"
    subsets = [v for k, v in geomet.coverage_params(
        "X", _t("2026-10-30T00:00:00Z"), _t("2026-10-30T12:00:00Z"), 53.55, -113.5) if k == "subset"]
    assert subsets == ["x(-113.5100,-113.4900)", "y(53.5400,53.5600)"]


# ── Source choice ─────────────────────────────────────────────────────────────
def _caps(ref: str, refs_back: int, step_h: int, horizon_h: int, three_hourly_to: int | None = None):
    r = _t(ref)
    times = [r + i * H for i in range(horizon_h + 1)]
    if three_hourly_to:
        times += [r + i * H for i in range(horizon_h + 3, three_hourly_to + 1, 3)]
    return geomet.Caps(ref=r, refs=[r - k * step_h * H for k in range(refs_back, -1, -1)], times=times)


CAPS = {
    "hrdps": _caps("2026-10-30T00:00:00Z", 5, 6, 48),
    "rdps": _caps("2026-10-30T00:00:00Z", 5, 6, 84),
    "gdps": _caps("2026-10-30T00:00:00Z", 3, 12, 84, 240),
}


@pytest.mark.parametrize("hour,code,ref", [
    ("2026-10-30T01:00:00Z", "H", "2026-10-30T00:00:00Z"),
    ("2026-10-31T00:00:00Z", "H", "2026-10-30T00:00:00Z"),
    ("2026-11-01T00:00:00Z", "H", "2026-10-30T00:00:00Z"),     # +48 h
    ("2026-11-01T01:00:00Z", "R", "2026-10-30T00:00:00Z"),     # +49 h
    ("2026-11-02T12:00:00Z", "R", "2026-10-30T00:00:00Z"),     # +84 h
    ("2026-11-02T13:00:00Z", "g", "2026-10-30T00:00:00Z"),     # GDPS 3-hourly tail
    ("2026-10-30T00:00:00Z", "h", "2026-10-29T18:00:00Z"),     # run start: lead 6 of the run before
    ("2026-10-29T19:00:00Z", "h", "2026-10-29T18:00:00Z"),
    ("2026-10-28T19:00:00Z", "h", "2026-10-28T18:00:00Z"),     # oldest HRDPS run + 1 h
    ("2026-10-28T18:00:00Z", "b", "2026-10-28T12:00:00Z"),     # older: GDPS, lead 6
    ("2026-10-28T13:00:00Z", "b", "2026-10-28T12:00:00Z"),
])
def test_choose_source(hour, code, ref):
    s = geomet.choose_source(_t(hour), CAPS)
    assert (s.code, s.ref) == (code, _t(ref))


def test_choose_source_tail_brackets_and_limits():
    s = geomet.choose_source(_t("2026-11-02T14:00:00Z"), CAPS)
    assert (s.t0, s.t1) == (_t("2026-11-02T12:00:00Z"), _t("2026-11-02T15:00:00Z"))
    assert geomet.choose_source(_t("2026-10-28T12:00:00Z"), CAPS) is None   # before every run kept
    assert geomet.choose_source(_t("2026-11-09T03:00:00Z"), CAPS) is None   # beyond +240 h
    assert geomet.choose_source(_t("2026-10-30T05:00:00Z"), {"hrdps": None, "rdps": None, "gdps": None}) is None


def test_choose_source_without_hrdps_uses_rdps():
    caps = {**CAPS, "hrdps": None}
    assert geomet.choose_source(_t("2026-10-30T05:00:00Z"), caps).code == "R"
    assert geomet.choose_source(_t("2026-10-29T20:00:00Z"), caps).code == "r"


# ── Series (mocked GeoMet) ───────────────────────────────────────────────────
CAPS_XML = {
    "HRDPS.CONTINENTAL_TT": ("2026-10-30T00:00:00Z/2026-11-01T00:00:00Z/PT1H",
                             "2026-10-28T18:00:00Z/2026-10-30T00:00:00Z/PT6H"),
    "RDPS_10km_AirTemp_2m": ("2026-10-30T00:00:00Z/2026-11-02T12:00:00Z/PT1H",
                             "2026-10-28T18:00:00Z/2026-10-30T00:00:00Z/PT6H"),
    "GDPS_15km_AirTemp_2m": ("2026-10-30T00:00:00Z/2026-11-02T12:00:00Z/PT1H,"
                             "2026-11-02T15:00:00Z/2026-11-09T00:00:00Z/PT3H",
                             "2026-10-28T12:00:00Z/2026-10-30T00:00:00Z/PT12H"),
}


def _value(layer: str, ref: datetime, t: datetime) -> float | None:
    """Synthetic fields: temperature = lead hours, RH 150 (clamped), wind 2 m/s, direction 350/10,
    HRDPS PR accumulates 0.5 mm per hour, RDPS 1-h rain 0.2, GDPS 3-h rain 0.9."""
    lead = (t - ref) / H
    if layer.endswith("_TT") or "AirTemp" in layer:
        return float(lead)
    if layer.endswith("_HR") or "Humidity" in layer:
        return 150.0
    if layer.endswith("_WSPD") or "WindSpeed" in layer:
        return 2.0
    if layer.endswith("_WD") or "WindDir" in layer:
        return 350.0 if int(lead) % 2 == 0 else 10.0
    if layer == "HRDPS.CONTINENTAL_PR":
        return 0.5 * lead
    if layer.endswith("Precip-Accum1h"):
        return 0.2
    if layer.endswith("Precip-Accum3h"):
        return 0.9
    return None


def _handler(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if p.get("request") == "GetCapabilities":
            layer = p["layer"]
            times, refs = CAPS_XML[layer]
            return httpx.Response(200, text=_caps_xml(layer, times, refs, "2026-10-30T00:00:00Z"))
        layer, ref, t = p["coverageId"], _t(p["DIM_REFERENCE_TIME"]), _t(p["TIME"])
        seen.append((layer, ref, t))
        return httpx.Response(200, text=_grid(_value(layer, ref, t)))
    return handler


@respx.mock
async def test_hourly_series_conversions_and_sources():
    seen: list = []
    respx.get(url__startswith=geomet.GEOMET_URL).mock(side_effect=_handler(seen))
    h = await geomet.hourly(53.55, -113.5, _t("2026-10-29T22:00:00Z"), _t("2026-10-30T02:00:00Z"))
    assert h["time"] == ["2026-10-29T22:00", "2026-10-29T23:00", "2026-10-30T00:00",
                         "2026-10-30T01:00", "2026-10-30T02:00"]
    assert h["src"] == "hhhHH"
    # History hours: the 18Z run, leads 4, 5, 6; then the 00Z run, leads 1, 2
    assert h["temperature_2m"] == [4.0, 5.0, 6.0, 1.0, 2.0]
    assert h["relative_humidity_2m"] == [100.0] * 5            # clamped
    assert h["wind_speed_10m"] == pytest.approx([7.2] * 5)     # 2 m/s -> km/h
    # HRDPS PR differenced within its run (0.5 mm/h); the first hour of a run is PR itself
    assert h["precipitation"] == pytest.approx([0.5] * 5)
    assert h["model_runs"]["hrdps"] == "2026-10-30T00:00:00Z"
    assert all(layer.startswith("HRDPS") for layer, _, _ in seen)
    n = len(seen)
    again = await geomet.hourly(53.55, -113.5, _t("2026-10-29T22:00:00Z"), _t("2026-10-30T02:00:00Z"))
    assert again["temperature_2m"] == h["temperature_2m"] and len(seen) == n   # cached


@respx.mock
async def test_hourly_gdps_tail_interpolated():
    seen: list = []
    respx.get(url__startswith=geomet.GEOMET_URL).mock(side_effect=_handler(seen))
    h = await geomet.hourly(53.55, -113.5, _t("2026-11-02T12:00:00Z"), _t("2026-11-02T15:00:00Z"))
    assert h["src"] == "Rggg"
    assert h["temperature_2m"] == pytest.approx([84.0, 85.0, 86.0, 87.0])   # lerp 84 -> 87
    assert h["precipitation"] == pytest.approx([0.2, 0.3, 0.3, 0.3])         # Accum3h / 3
    # 350 -> 10 through north, not through south
    d = h["wind_direction_10m"][1]
    assert d > 350 or d < 10


@respx.mock
async def test_hourly_before_runs_kept_is_null_and_unreachable_is_none():
    respx.get(url__startswith=geomet.GEOMET_URL).mock(side_effect=_handler([]))
    h = await geomet.hourly(53.55, -113.5, _t("2026-10-28T11:00:00Z"), _t("2026-10-28T13:00:00Z"))
    assert h["src"] == "--b"
    assert h["temperature_2m"][:2] == [None, None] and h["temperature_2m"][2] == 1.0
    geomet.reset_cache()
    respx.get(url__startswith=geomet.GEOMET_URL).mock(return_value=httpx.Response(503))
    assert await geomet.hourly(53.55, -113.5, _t("2026-10-30T01:00:00Z"), _t("2026-10-30T02:00:00Z")) is None


async def test_hourly_rejects_long_windows():
    with pytest.raises(ValueError):
        await geomet.hourly(53.55, -113.5, _t("2026-10-01T00:00:00Z"), _t("2026-10-30T00:00:00Z"))


def test_src_summary():
    assert geomet.src_summary("hhhHHR") == "h3H2R1"
