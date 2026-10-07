#!/usr/bin/env python3
"""Regenerate the tiny CFSDS fire-day fixture used by test_harness.py.

Observed progression: CFSDS v1.1 fire 2016_106 (NW Alberta, July 2016), DOB raster cropped to
the burned area + 2 km at 90 m (Barber et al. 2024, doi:10.17605/OSF.IO/F48RY, CC BY 4.0).
Weather: ERA5 hourly via the Open-Meteo archive API (CC BY 4.0), 16-17 July 2016, local time.
FWI codes: the CFSDS summary rows for DOB 198-199.

Fuel and terrain are SYNTHETIC: uniform C-2 (2014b code 102) with a non-fuel lake (code 118) and
a plane rising 2 % toward the south (north-facing). The national FBP fuel grid is licensed for
internal use only, so it is not committed.

    PYTHONPATH=engine/src python engine/tests/validation/data/make_fixture.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np

from firesim.validation.cfsds import FireDomain, read_groups
from firesim.validation.harness import crop_for_day
from firesim.validation.weather import fetch_era5_hourly

ROOT = Path("/home/rpas/dev/wildfire/validation-data")
HERE = Path(__file__).parent
FIRE, DAY = "2016_106", 199

dom = FireDomain.load(ROOT / f"domains/{FIRE}.npz")
dom = crop_for_day(dom, DAY + 1, margin_m=2000.0)
fuel = np.full(dom.dob.shape, 102, dtype=np.int32)
rr, cc = np.mgrid[0:dom.rows, 0:dom.cols]
fuel[(rr - 0.8 * dom.rows) ** 2 + (cc - 0.2 * dom.cols) ** 2 < (0.08 * dom.rows) ** 2] = 118
elevation = (600.0 - 0.02 * dom.dy * (dom.rows - rr)).astype(np.float32)  # rises toward the south
fix = FireDomain(fire_id=FIRE, year=2016, dob=dom.dob, fuel=fuel, elevation=elevation,
                 lat_max=dom.lat_max, lng_min=dom.lng_min, cell_lat=dom.cell_lat,
                 cell_lng=dom.cell_lng, fuel_scheme="cfs_national_2014",
                 meta={"note": "CFSDS DOB (CC BY 4.0) with synthetic fuel and terrain"})
fix.save(HERE / "cfsds_fireday_fixture.npz")

groups = read_groups(ROOT / "cfsds/groups/Firegrowth_groups_v1_1_2016.csv")[FIRE]
lat = dom.lat_max - 0.5 * dom.rows * dom.cell_lat
lng = dom.lng_min + 0.5 * dom.cols * dom.cell_lng
recs = fetch_era5_hourly(lat, lng, date(2016, 7, 16), date(2016, 7, 18), ROOT / "weather")
keep = ("DOB", "fireday", "ffmc", "dmc", "dc", "bui", "isi", "fwi", "ws", "rh")
(HERE / "cfsds_fireday_fixture.json").write_text(json.dumps({
    "fire_id": FIRE, "day": DAY,
    "groups": {str(d): {k: groups[d][k] for k in keep} for d in (DAY - 1, DAY) if d in groups},
    "hours": [[r.time.isoformat(), r.temperature, r.relative_humidity, r.wind_speed,
               r.wind_direction, r.precipitation] for r in recs],
    "sources": "CFSDS v1.1 (Barber et al. 2024, CC BY 4.0); ERA5 via Open-Meteo (CC BY 4.0); "
               "fuel and terrain synthetic",
}, indent=0))
print(fix.rows, fix.cols, {int(k): int(v) for k, v in zip(*np.unique(fix.dob, return_counts=True))})
