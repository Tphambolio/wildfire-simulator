"""Held-out (PREREG_ARRIVAL1) environment: point the frozen C1/C1b code at the SK + NT pool.

Importing this module (before anything else of the harness) sets
    FIRESIM_VALIDATION_DATA    = <validation-data>/arrival-underspread-r72/heldout  (manifest,
                                 cfsds -> shared CFSDS, weather = its own ERA5 cache)
    FIRESIM_VALIDATION_DOMAINS = .../heldout/domains
and replaces two functions of ``communities`` (the module the registered C1b metric
``communities_c1b`` calls) without changing their contracts:
    community_zones()  -> the SK + NT reach-zone layer built by heldout_pool.py communities
                          (already buffered by 500 m; same ``name`` column)
    _viirs_year(year)  -> the same FIRMS VIIRS 375 m files, columns and UTC-6 local clock, with
                          the SK + NT box (-136.5..-101.0 E, > 48.5 N) instead of Alberta's
Everything else of the metric (intervals, passes, error, start-mask rule) is the frozen C1b code.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

VD = Path("/home/rpas/dev/wildfire/validation-data")
HO = VD / "arrival-underspread-r72/heldout"
os.environ["FIRESIM_VALIDATION_DATA"] = str(HO)
os.environ["FIRESIM_VALIDATION_DOMAINS"] = str(HO / "domains")

import common as K  # noqa: E402,F401  (sets sys.path)
import communities as C  # noqa: E402

BOX = (-136.5, -101.0, 48.5)


@lru_cache(maxsize=1)
def community_zones():
    import geopandas as gpd

    return gpd.read_file(HO / "communities_sk_nt.geojson")[["name", "geometry"]]


@lru_cache(maxsize=4)
def _viirs_year(year: int):
    import pandas as pd

    frames = []
    for p in sorted(C.VIIRS_DIR.glob(f"viirs-*_{year}_Canada.csv")):
        f = pd.read_csv(p, usecols=["latitude", "longitude", "acq_date", "acq_time"])
        f = f[(f.longitude > BOX[0]) & (f.longitude < BOX[1]) & (f.latitude > BOX[2])]
        frames.append(f)
    if not frames:
        return None
    f = pd.concat(frames)
    t = pd.to_datetime(f["acq_date"]) + pd.to_timedelta(f["acq_time"] // 100, unit="h") + \
        pd.to_timedelta(f["acq_time"] % 100, unit="m")
    f = f.assign(local=t + pd.Timedelta(hours=C.UTC_OFFSET_H))
    return f[["latitude", "longitude", "local"]].reset_index(drop=True)


C.community_zones = community_zones
C._viirs_year = _viirs_year
