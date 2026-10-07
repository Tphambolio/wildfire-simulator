"""The vectorised fire ellipse (``fbp_ellipse_arrays``) must reproduce ``calculate_fbp``.

The grid model evaluates FBP for thousands of (fuel, slope, aspect) cell types every weather
hour through ``fbp_ellipse_arrays``; it is a performance change only, so every output it
returns must equal the scalar ``calculate_fbp`` to floating-point rounding for every fuel type,
the slope and wind branches (no slope, slope >= 70 %, ISI wind cap above 40 km/h), green aspen
below BUI 80, low FFMC and canopy overrides.
"""

import itertools

import numpy as np
import pytest

from firesim.fbp.calculator import calculate_fbp, fbp_ellipse_arrays
from firesim.fbp.constants import FuelType

SLOPES = np.array([0.0, 1.0, 5.0, 12.0, 30.0, 69.0, 70.0, 95.0])
ASPECTS = np.array([0.0, 45.0, 90.0, 133.0, 180.0, 270.0, 359.0])
WEATHER = [
    # wind km/h, from deg, FFMC, DMC, DC
    (0.0, 0.0, 88.0, 30.0, 250.0),
    (15.0, 270.0, 91.0, 45.0, 320.0),
    (45.0, 200.0, 94.0, 80.0, 500.0),  # above the eq 53a wind cap, BUI > 80
    (25.0, 10.0, 60.0, 10.0, 40.0),  # wet fine fuel, low BUI (D-2 does not carry)
]
FIELDS = {"ros": "ros_final", "bros": "back_ros", "fros": "flank_ros", "raz": "raz",
          "cfb": "cfb", "lb": "lb", "sfc": "sfc", "cfl": "cfl", "rso": "rso"}


@pytest.mark.parametrize("fuel", list(FuelType))
@pytest.mark.parametrize("weather", WEATHER)
def test_matches_scalar_fbp(fuel, weather):
    ws, wd, ffmc, dmc, dc = weather
    slope, aspect = (np.array(v) for v in zip(*itertools.product(SLOPES, ASPECTS)))
    kw = dict(pc=60.0, grass_cure=85.0, fmc=97.0, pdf=40.0, gfl=0.4)
    vec = fbp_ellipse_arrays(fuel, ws, wd, ffmc, dmc, dc, slope, aspect, **kw)
    for k in range(len(slope)):
        f = calculate_fbp(fuel, ws, ffmc, dmc, dc, float(slope[k]), wind_direction=wd,
                          slope_aspect=float(aspect[k]), **kw)
        for key, attr in FIELDS.items():
            got = vec[key] if np.isscalar(vec[key]) else vec[key][k]
            want = getattr(f, attr)
            assert got == pytest.approx(want, rel=1e-12, abs=1e-12), (key, slope[k], aspect[k])


@pytest.mark.parametrize("cbh,cfl", [(None, None), (2.0, 0.5), (8.0, 0.0), (0.0, 1.2)])
def test_canopy_overrides(cbh, cfl):
    slope = np.array([0.0, 20.0, 40.0])
    aspect = np.array([0.0, 90.0, 225.0])
    for fuel in (FuelType.C2, FuelType.C6, FuelType.M1, FuelType.C3):
        vec = fbp_ellipse_arrays(fuel, 30.0, 250.0, 93.0, 60.0, 400.0, slope, aspect, cbh=cbh,
                                 cfl=cfl)
        for k in range(3):
            f = calculate_fbp(fuel, 30.0, 93.0, 60.0, 400.0, float(slope[k]), wind_direction=250.0,
                              slope_aspect=float(aspect[k]), cbh=cbh, cfl=cfl)
            for key, attr in FIELDS.items():
                got = vec[key] if np.isscalar(vec[key]) else vec[key][k]
                assert got == pytest.approx(getattr(f, attr), rel=1e-12, abs=1e-12), key
