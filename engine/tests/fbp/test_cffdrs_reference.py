"""FBP outputs against the cffdrs reference implementation.

``data/cffdrs_reference.json`` holds outputs computed by cffdrs (Python), an
independent implementation of ST-X-3 and Wotton et al. (2009), for all 18
fuel types over six weather/slope cases (including wind above 40 km/h and
slopes above 70 %). Regenerate with ``data/generate_cffdrs_reference.py``.
"""

import json
import math
from pathlib import Path

import pytest

from firesim.fbp.calculator import calculate_fbp
from firesim.fbp.constants import FuelType

_ROWS = json.loads((Path(__file__).parent / "data" / "cffdrs_reference.json").read_text())["rows"]

_FIELDS = {
    "ros": "ros_final",
    "bros": "back_ros",
    "fros": "flank_ros",
    "lb": "lb",
    "wsv": "wsv",
    "isi": "isi",
    "sfc": "sfc",
    "cfb": "cfb",
    "cfc": "cfc",
    "tfc": "tfc",
    "hfi": "hfi",
}


def _dmc_dc_for_bui(bui: float) -> tuple[float, float]:
    """DMC/DC pair whose BUI equals ``bui``.

    With DMC <= 0.4 DC, BUI = 0.8 * DMC * DC / (DMC + 0.4 * DC); solve for DMC at DC = 1000.
    """
    dc = 1000.0
    dmc = bui * 0.4 * dc / (0.8 * dc - bui)
    return dmc, dc


@pytest.mark.parametrize(
    "row", _ROWS, ids=[f"{r['fuel']}-ws{r['ws']:g}-gs{r['gs']:g}" for r in _ROWS]
)
def test_matches_cffdrs(row):
    dmc, dc = _dmc_dc_for_bui(row["bui"])
    result = calculate_fbp(
        FuelType(row["fuel"]),
        wind_speed=row["ws"],
        ffmc=row["ffmc"],
        dmc=dmc,
        dc=dc,
        slope=row["gs"],
        pc=row["pc"],
        grass_cure=row["cc"],
        fmc=row["fmc"],
        wind_direction=row["wd"],
        slope_aspect=row["upslope"],
        pdf=row["pdf"],
        gfl=row["gfl"],
    )
    assert result.bui == pytest.approx(row["bui"], rel=1e-9)
    for ref_key, attr in _FIELDS.items():
        assert getattr(result, attr) == pytest.approx(row[ref_key], rel=1e-7, abs=1e-9), ref_key
    # Spread direction (degrees, 0=N), compared on the circle
    assert abs((result.raz - row["raz"] + 180.0) % 360.0 - 180.0) < 1e-6


def test_reference_covers_all_fuel_types():
    assert {r["fuel"] for r in _ROWS} == {f.value for f in FuelType}


def test_reference_exercises_wind_cap_and_steep_slope():
    assert any(r["ws"] >= 40.0 for r in _ROWS)
    assert any(r["gs"] >= 70.0 for r in _ROWS)
    assert not any(math.isnan(r["ros"]) for r in _ROWS)
