"""Regenerate cffdrs_fwi_reference.json: daily FWI System oracle from the cffdrs Python package.

The fixture holds daily FWI outputs computed by cffdrs (``cffdrs.fwi``; Wang et al. 2017), an
independent implementation of Van Wagner & Pickett (1985) / Van Wagner (1987), so the engine
tests can check the daily FWI chain without cffdrs installed. Two cases sets are written:

- ``sequence``: the cffdrs 48-day test sequence (``cffdrs/tests/data/fwi_test_data.csv``, the R
  package's ``test_fwi`` data at lat 40, April-June 1985, start 85 / 6 / 15), chained day to day.
  The CSV's own one-decimal values (cffdrs R output, "within one decimal place of the test data
  published back in 1985", per the cffdrs test docstring) are copied as ``published``.
- ``edge``: single-day cases (cold days with T <= -2.8 C in several months, heavy and threshold
  rain, zero wind, RH 0 / 100, start codes at 0 and 101).

Each case is computed twice: with the FFMC coefficient patched to 147.2 (Van Wagner 1987 eq 2;
ST-X-3 eq 46; what FireSim uses) and with cffdrs's own 250 * 59.5 / 101 = 147.27723. Latitude
adjustment is off (FireSim uses the Canadian day-length tables only).

Usage:
    CFFDRS_PY_PATH=/path/to/cffdrs_py python generate_cffdrs_fwi_reference.py
"""

import csv
import importlib
import json
import os
import sys
from pathlib import Path

CFFDRS = os.environ.get("CFFDRS_PY_PATH", os.path.expanduser("~/survey_clones/cffdrs_py"))
sys.path.insert(0, CFFDRS)

import cffdrs.fwi as F  # noqa: E402

EXACT = 250.0 * 59.5 / 101.0

# (label, temp C, rh %, wind km/h, rain mm, month, ffmc_prev, dmc_prev, dc_prev)
EDGE = [
    ("cold -5 C May", -5.0, 60.0, 10.0, 0.0, 5, 85.0, 20.0, 200.0),
    ("cold -3 C July", -3.0, 70.0, 10.0, 0.0, 7, 85.0, 30.0, 300.0),
    ("cold -10 C April", -10.0, 50.0, 15.0, 0.0, 4, 80.0, 10.0, 100.0),
    ("cold -2.8 C exactly October", -2.8, 60.0, 5.0, 0.0, 10, 80.0, 15.0, 350.0),
    ("cold -20 C January", -20.0, 70.0, 10.0, 0.0, 1, 70.0, 5.0, 300.0),
    ("cold -4 C with 5 mm rain, September", -4.0, 90.0, 10.0, 5.0, 9, 85.0, 40.0, 400.0),
    ("DMC cutoff -1.1 C June", -1.1, 50.0, 10.0, 0.0, 6, 85.0, 25.0, 250.0),
    ("heavy rain 25 mm", 15.0, 90.0, 10.0, 25.0, 7, 92.0, 60.0, 450.0),
    ("very heavy rain 80 mm", 12.0, 95.0, 5.0, 80.0, 8, 94.0, 120.0, 700.0),
    ("rain 0.5 mm (below FFMC threshold)", 22.0, 40.0, 15.0, 0.5, 7, 90.0, 40.0, 300.0),
    ("rain 0.6 mm", 22.0, 40.0, 15.0, 0.6, 7, 90.0, 40.0, 300.0),
    ("rain 1.5 mm (below DMC threshold)", 22.0, 40.0, 15.0, 1.5, 7, 90.0, 40.0, 300.0),
    ("rain 2.8 mm (below DC threshold)", 22.0, 40.0, 15.0, 2.8, 7, 90.0, 40.0, 300.0),
    ("rain 2.9 mm", 22.0, 40.0, 15.0, 2.9, 7, 90.0, 40.0, 300.0),
    ("zero wind, dry", 28.0, 25.0, 0.0, 0.0, 7, 90.0, 50.0, 400.0),
    ("calm, RH 100", 10.0, 100.0, 0.0, 0.0, 6, 85.0, 20.0, 200.0),
    ("hot, dry and windy (FFMC ~97)", 35.0, 10.0, 40.0, 0.0, 8, 95.0, 80.0, 600.0),
    ("RH 0", 30.0, 0.0, 20.0, 0.0, 7, 92.0, 60.0, 500.0),
    ("start codes at 0", 20.0, 40.0, 10.0, 0.0, 6, 0.0, 0.0, 0.0),
    ("start FFMC 101", 25.0, 30.0, 20.0, 0.0, 7, 101.0, 70.0, 500.0),
    ("winter month, mild", 5.0, 50.0, 10.0, 0.0, 12, 80.0, 10.0, 300.0),
]


def _day(coef, temp, rh, ws, prec, mon, ffmc0, dmc0, dc0):
    F.FFMC_COEFFICIENT = coef
    ffmc = F.fine_fuel_moisture_code(ffmc0, temp, rh, ws, prec)
    dmc = F.duff_moisture_code(dmc0, temp, rh, prec, 46.0, mon, lat_adjust=False)
    dc = F.drought_code(dc0, temp, rh, prec, 46.0, mon, lat_adjust=False)
    isi = F.initial_spread_index(ffmc, ws)
    bui = F.buildup_index(dmc, dc)
    fwi = F.fire_weather_index(isi, bui)
    return {"ffmc": ffmc, "dmc": dmc, "dc": dc, "isi": isi, "bui": bui, "fwi": fwi}


def main() -> None:
    rows = list(csv.DictReader(open(Path(CFFDRS) / "cffdrs" / "tests" / "data" / "fwi_test_data.csv")))
    sequence = []
    state = {147.2: (85.0, 6.0, 15.0), EXACT: (85.0, 6.0, 15.0)}
    for r in rows:
        inp = {"temp": float(r["TEMP"]), "rh": float(r["RH"]), "wind": float(r["WS"]),
               "rain": float(r["PREC"]), "month": int(r["MON"])}
        out = {}
        for coef, key in ((147.2, "coef_147_2"), (EXACT, "coef_exact")):
            res = _day(coef, inp["temp"], inp["rh"], inp["wind"], inp["rain"], inp["month"], *state[coef])
            state[coef] = (res["ffmc"], res["dmc"], res["dc"])
            out[key] = res
        published = {k.lower(): float(r[k]) for k in ("FFMC", "DMC", "DC", "ISI", "BUI", "FWI")}
        sequence.append({"date": f'{r["YR"]}-{int(r["MON"]):02d}-{int(r["DAY"]):02d}', "inputs": inp,
                         "published": published, **out})
    edge = []
    for label, t, rh, ws, p, mon, f0, d0, c0 in EDGE:
        edge.append({
            "label": label,
            "inputs": {"temp": t, "rh": rh, "wind": ws, "rain": p, "month": mon,
                       "ffmc_prev": f0, "dmc_prev": d0, "dc_prev": c0},
            "coef_147_2": _day(147.2, t, rh, ws, p, mon, f0, d0, c0),
            "coef_exact": _day(EXACT, t, rh, ws, p, mon, f0, d0, c0),
        })
    out = Path(__file__).with_name("cffdrs_fwi_reference.json")
    out.write_text(json.dumps({
        "source": "cffdrs (Python) cffdrs.fwi, lat_adjust=False; coef_147_2 = FFMC coefficient patched "
                  "to 147.2, coef_exact = cffdrs default 250*59.5/101; sequence = cffdrs "
                  "tests/data/fwi_test_data.csv (start 85/6/15), 'published' = that CSV's 1-decimal values",
        "sequence": sequence, "edge": edge}, indent=1))
    print(f"wrote {len(sequence)} sequence days + {len(edge)} edge cases -> {out}")


if __name__ == "__main__":
    importlib.invalidate_caches()
    main()
