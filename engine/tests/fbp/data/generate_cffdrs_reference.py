"""Regenerate cffdrs_reference.json from the cffdrs Python package.

The fixture holds FBP outputs computed by cffdrs (an independent
implementation of ST-X-3 / Wotton et al. 2009), so the engine tests can check
against it without cffdrs installed. cffdrs uses the exact FFMC coefficient
(250 * 59.5 / 101); it is patched to the ST-X-3 eq 46 value 147.2 that the
engine uses, so agreement is to floating-point precision.

Usage:
    CFFDRS_PY_PATH=/path/to/cffdrs_py python generate_cffdrs_reference.py
"""

import importlib
import itertools
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("CFFDRS_PY_PATH", os.path.expanduser("~/survey_clones/cffdrs_py")))

for _mod in ("cffdrs.constants", "cffdrs.fwi", "cffdrs.back_rate_of_spread", "cffdrs.slope_calc"):
    _m = importlib.import_module(_mod)
    if hasattr(_m, "FFMC_COEFFICIENT"):
        _m.FFMC_COEFFICIENT = 147.2

from cffdrs.constants import FUEL_TYPE_CODES  # noqa: E402
from cffdrs.crown_base_height import CBH_DEFAULT  # noqa: E402
from cffdrs.crown_fuel_load import CFL_DEFAULT  # noqa: E402
from cffdrs.fire_behaviour_prediction import _fire_behaviour_prediction  # noqa: E402

FUELS = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "D1", "D2", "M1", "M2", "M3", "M4",
         "O1a", "O1b", "S1", "S2", "S3"]
# (ffmc, bui, wind km/h, wind FROM deg, slope %, upslope azimuth deg, grass cure %)
WEATHER = [
    (85.0, 40.0, 10.0, 270.0, 0.0, 0.0, 80.0),
    (90.0, 60.0, 20.0, 270.0, 0.0, 0.0, 100.0),
    (92.0, 90.0, 35.0, 225.0, 0.0, 0.0, 95.0),
    (94.0, 120.0, 50.0, 300.0, 0.0, 0.0, 100.0),
    (90.0, 60.0, 15.0, 270.0, 40.0, 30.0, 70.0),
    (93.0, 100.0, 25.0, 180.0, 80.0, 200.0, 90.0),
]
PC, PDF, GFL, FMC = 50.0, 35.0, 0.35, 97.0


def main() -> None:
    rows = []
    for fuel, (ffmc, bui, ws, wd, gs, upslope, cc) in itertools.product(FUELS, WEATHER):
        code = FUEL_TYPE_CODES[fuel.upper()]
        aspect = (upslope + 180.0) % 360.0  # cffdrs takes the downslope aspect
        r = _fire_behaviour_prediction(
            code, ffmc, bui, ws, math.radians(wd), gs, math.radians(aspect), PC, PDF, cc, GFL,
            CBH_DEFAULT[code], CFL_DEFAULT[code], FMC, 0, 53.5, 113.5, 0, 180, 0, 0, 0, 1, 0, 0, 1,
        )
        rows.append({
            "fuel": fuel, "ffmc": ffmc, "bui": bui, "ws": ws, "wd": wd, "gs": gs,
            "upslope": upslope, "cc": cc, "pc": PC, "pdf": PDF, "gfl": GFL, "fmc": FMC,
            "ros": r.ros, "bros": r.bros, "fros": r.fros, "lb": r.lb, "wsv": r.wsv, "raz": r.raz,
            "isi": r.isi, "sfc": r.sfc, "cfb": r.cfb, "cfc": r.cfc, "tfc": r.tfc, "hfi": r.hfi,
        })
    out = Path(__file__).with_name("cffdrs_reference.json")
    out.write_text(json.dumps({"source": "cffdrs (Python), FFMC coefficient patched to 147.2",
                               "rows": rows}, indent=1))
    print(f"wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
