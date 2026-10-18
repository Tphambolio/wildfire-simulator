"""Observed vs modelled timing of day growth from VIIRS overpasses (backlog A7; descriptive).

CFSDS (Barber et al. 2024) assigns hotspots seen 19:00-08:00 LST to the previous day, so a
CFSDS burn day n covers roughly 08:00 LST on day n to 08:00 LST on day n+1 and includes the
night that follows. Within that window the VIIRS 375 m sensors (S-NPP; NOAA-20 from 2018) pass
about 13:00-14:30 LST (day pass) and about 01:00-02:30 LST (night pass).

For each fire-day this script labels every observed growth cell (CFSDS DOB == n, outside the
start area) by its first VIIRS detection within ``--radius-m`` inside the day-n window:

- ``day``: first seen on the day pass of day n (burning by about 14:00 LST);
- ``night``: first seen on a later pass in the window (burning between the day pass and about
  08:00 LST on day n+1, i.e. afternoon peak, evening and night);
- ``none``: no detection in the window (cool, short or obscured burning).

``day / (day + night)`` is the observed share of growth already burning at the day pass. It is
a lower bound on the share burned before the pass (a cell that burned out before 13:30 may be
seen only at night, or not at all). The modelled share is the fraction of the 17 h modelled
growth (diagnostic run of ``front_overspread_diag.py``, re-run here) whose arrival is before the
fire-day's median day-pass time.

Inputs: FIRMS VIIRS 375 m yearly country archives (``viirs-snpp_<year>_Canada.csv``,
``viirs-jpss1_<year>_Canada.csv``; NASA FIRMS, https://firms.modaps.eosdis.nasa.gov/country/),
the harness domains and manifest. Clock: the harness runs on America/Edmonton local time (MDT,
UTC-6 in the fire season); CFSDS uses LST (UTC-7 in Alberta).

    PYTHONPATH=engine/src python3 scripts/validation/viirs_diurnal_split.py \
        --domains-dir DIR --viirs-dir DIR --diag DIAG.jsonl --out OUT.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LST_UTC = -7  # Alberta local standard time (CFSDS convention)
MDT_UTC = -6  # harness clock in the fire season


def load_viirs(viirs_dir: Path, year: int) -> pd.DataFrame:
    parts = []
    for sat in ("viirs-snpp", "viirs-jpss1"):
        p = viirs_dir / f"{sat}_{year}_Canada.csv"
        if p.exists():
            df = pd.read_csv(p, usecols=["latitude", "longitude", "acq_date", "acq_time",
                                         "confidence", "daynight"])
            df = df[(df.longitude > -121) & (df.longitude < -109) & (df.latitude > 48.9)
                    & (df.latitude < 60.1) & (df.confidence != "l")]
            parts.append(df)
    df = pd.concat(parts, ignore_index=True)
    t = pd.to_datetime(df.acq_date) + pd.to_timedelta(df.acq_time // 100, "h") + \
        pd.to_timedelta(df.acq_time % 100, "min")
    df["t_lst"] = t + pd.Timedelta(hours=LST_UTC)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains-dir", required=True)
    ap.add_argument("--viirs-dir", required=True)
    ap.add_argument("--diag", required=True, help="front_overspread_diag.py run file (factors {})")
    ap.add_argument("--out", required=True)
    ap.add_argument("--radius-m", type=float, default=375.0)
    a = ap.parse_args()
    os.environ["FIRESIM_VALIDATION_DOMAINS"] = a.domains_dir
    import validate as V

    from firesim.validation.weather import doy_to_date

    diag = {(r["fire_id"], r["day"]): r for r in map(json.loads, open(a.diag)) if "error" not in r}
    viirs_cache: dict[int, pd.DataFrame] = {}
    rows = []
    for (fid, day), r in sorted(diag.items()):
        dom = V.load_domain(fid)
        if dom.year not in viirs_cache:
            viirs_cache[dom.year] = load_viirs(Path(a.viirs_dir), dom.year)
        vi = viirs_cache[dom.year]
        d = doy_to_date(dom.year, day)
        w0 = datetime(d.year, d.month, d.day, 8)
        w1 = w0 + timedelta(days=1)
        v = vi[(vi.t_lst >= w0) & (vi.t_lst < w1) & (vi.latitude <= dom.lat_max)
               & (vi.latitude >= dom.lat_min) & (vi.longitude >= dom.lng_min)
               & (vi.longitude <= dom.lng_max)]
        growth = dom.dob == day
        prior = (dom.dob > 0) & (dom.dob < day)
        growth &= ~prior
        rr, cc = np.nonzero(growth)
        lat = dom.lat_max - (rr + 0.5) * dom.cell_lat
        lng = dom.lng_min + (cc + 0.5) * dom.cell_lng
        kx = np.cos(np.radians(0.5 * (dom.lat_max + dom.lat_min))) * 111320.0
        cells = np.column_stack((lng * kx, lat * 111320.0))
        label = np.full(len(rr), "none", dtype=object)
        day_pass = v[(v.daynight == "D") & (v.t_lst.dt.date == d)]
        day_pass_mdt = None
        if len(v):
            tree = cKDTree(np.column_stack((v.longitude.values * kx, v.latitude.values * 111320.0)))
            hits = tree.query_ball_point(cells, a.radius_m)
            is_day = ((v.daynight.values == "D") & (v.t_lst.dt.date.values == d))
            for k, h in enumerate(hits):
                if h:
                    label[k] = "day" if is_day[h].any() else "night"
        if len(day_pass):
            med = day_pass.t_lst.sort_values().iloc[len(day_pass) // 2]
            day_pass_mdt = (med.hour + med.minute / 60.0) + (MDT_UTC - LST_UTC)
        cell_ha = dom.cell_area_m2 / 1e4
        n_day, n_night, n_none = ((label == x).sum() * cell_ha for x in ("day", "night", "none"))
        hours = np.array(r["model_growth_by_clock_hour_ha"])  # index = clock hour (MDT)
        tp = day_pass_mdt if day_pass_mdt is not None else 14.5
        h_int = int(tp)
        model_before = hours[:h_int].sum() + (tp - h_int) * hours[h_int]
        model_tot = hours.sum()
        rows.append({
            "fire_id": fid, "day": day, "date": d.isoformat(), "res_m": round(dom.dy),
            "obs_growth_ha": growth.sum() * cell_ha, "obs_day_ha": n_day, "obs_night_ha": n_night,
            "obs_undetected_ha": n_none, "n_viirs": len(v), "n_viirs_day_pass": len(day_pass),
            "day_pass_mdt_h": day_pass_mdt,
            "obs_day_share": n_day / (n_day + n_night) if n_day + n_night else np.nan,
            "model_growth_ha": model_tot, "model_before_pass_ha": model_before,
            "model_day_share": model_before / model_tot if model_tot else np.nan,
            "model_after_pass_ha": model_tot - model_before,
        })
        print(fid, day, rows[-1]["obs_day_share"], rows[-1]["model_day_share"], flush=True)
    pd.DataFrame(rows).to_csv(a.out, index=False)


if __name__ == "__main__":
    main()
