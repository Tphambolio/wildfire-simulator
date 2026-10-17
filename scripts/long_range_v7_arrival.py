#!/usr/bin/env python3
"""Long-range embers v7, target A: calibrate FireSim's front first, then refit embers on top (PREREG_LR7).

Purpose
    v6 (PREREG_LR6) showed that FireSim's front, not the ember chain, dominates the error in when fire
    reaches communities: without long-range embers the model missed held-out community-block arrival by
    10.6 h (Glass and North Complex 10-15 h early on single proxy fuels and event-strength winds; CZU
    late). Embers only add spread, so v6's fit drove the ember efficiency η to its lowest grid value.
    v7 therefore
      stage 1  replaces the proxy fuel with pre-fire LANDFIRE FBFM40 crosswalked to FBP
               (``long_range_v7_fuels.py``), replaces constant member winds with the hourly observed
               RAWS wind (speed and direction) and hourly FFMC (Van Wagner 1977 hourly model), and
               calibrates three spread parameters on the TRAINING fires' arrival times
               (θ = m_open: ROS multiplier for grass and shrub cells; m_T: ROS multiplier for timber
               cells; f_w: RAWS 20 ft -> effective 10 m open-wind factor) by a grid with
               leave-one-fire-out (LOFO) cross-validation;
      stage 2  refits long-range ember establishment on top of the calibrated front with a variable η:
               η(cell, t) = η0 · w(group) · p_e(category(cell), FFMC(t)), w = 1 for grass/town cells and
               κ for shrub/timber cells, p_e = Beverly & Wotton (2007) P(sustained flaming) with the
               grass (eq 2.02) or pine (eq 2.03) category at the hourly FFMC (AMS preprint p.5).

Models (paired ensemble members share the wind perturbation)
    A0v6  the v6 no-ember baseline (v6 runs reused for the 8 v6 fires; for fresh fires the same v6
          set-up rule applied to them, ``v6_baseline_cfg``);
    A0c   calibrated front + FireSim's short-range spotting, no long-range embers;
    V7    A0c + long-range ember spot fires with the variable η fitted in stage 2.

Inputs
    v7 run grids (``validation-data/embers-v7/grids/<fire>.npz``: v6 WorldCover/3DEP arrays + LANDFIRE);
    the RAWS / HADS hourly records already on disk (``calfire-dins/refs/<fire>/``), the v6 observation
    code (GOFER/FIRIS perimeters, VIIRS, NIST points; ``long_range_v6_arrival``) and its community
    blocks (540 m, >= 10 Microsoft footprints). DINS damage is never read here.

Outputs (outside the repo): ``<out>/<fire>/<tag>_m<k>.npz`` per run; ``<out>/fit_front.json``,
    ``<out>/fit_eta.json``, ``<out>/scores_<tag>.json`` (aggregate only, ``_about`` blocks).

Run (repo root; one fire per process; 0.3-1.5 GB each)
    PYTHONPATH=engine/src python3 scripts/long_range_v7_arrival.py front-grid --fire camp --out DIR
    PYTHONPATH=engine/src python3 scripts/long_range_v7_arrival.py fit-front --out DIR
    PYTHONPATH=engine/src python3 scripts/long_range_v7_arrival.py run --fire camp --model A0c V7 --members 10 --out DIR
    PYTHONPATH=engine/src python3 scripts/long_range_v7_arrival.py fit-eta --out DIR
    PYTHONPATH=engine/src python3 scripts/long_range_v7_arrival.py score --fires dixie caldor --out DIR --tag PREREG_LR7

References: Van Wagner (1977) hourly FFMC (as ``firesim.fwi.calculator.hourly_ffmc``); Beverly & Wotton
    (2007) AMS preprint eqs 2.02-2.03 p.5; Andrews (2009) RMRS-GTR-213 p.58 (20 ft -> 10 m wind ×1.15);
    Storey et al. (2020) Fire 3:10 p.7 (MS74WYUD); Cruz et al. (2012) For. Ecol. Manage. 284:269 (9PGICYK7);
    Scott & Burgan (2005) RMRS-GTR-153. Status: pre-registered test (PREREG_LR7); illustrative.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import itertools
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import long_range_v6_arrival as A  # noqa: E402

CALFIRE = A.CALFIRE
V7DATA = Path.home() / "dev" / "wildfire" / "validation-data" / "embers-v7"
TRAINING = A.TRAINING  # eaton, palisades, camp, tubbs, woolsey
SECONDARY = A.HELDOUT  # glass, czu, northcomplex: seen in v4-v6 -> disclosed secondary held-out set
FRESH = ("dixie", "caldor")  # PREREG_LR7 primary held-out: fresh fires, never simulated or scored before
SEED = 20261015
COARSEN = 2
TOWN_ROS = A.TOWN_ROS  # 0.5 [H], spec C40, as v6
GRASS_CURE = 95.0  # % [H]: California dry season (Aug-Jan); FireSim's own seasonal default is Alberta's
RAW_TO_10M = 1.15  # standard 20 ft -> 10 m conversion (Andrews 2009 p.58); f_w = 1.15 means "no reduction"
SPINUP_H = 24.0  # hourly FFMC spin-up before the run start (from the v6 daily FFMC) [H]
MEMBER_SPEED_SIGMA = 0.2  # ensemble: member wind-speed factor exp(N(0, 0.2)) [H]
MEMBER_DIR_SD = 10.0  # ensemble: member direction offset N(0, 10 deg) [H]

# stage-1 grid (fixed before any fit): ROS multipliers and the wind factor
GRID_M_OPEN = (0.15, 0.25, 0.4, 0.6, 0.85, 1.2, 1.7)
GRID_M_T = (0.25, 0.5, 1.0, 2.0, 4.0)
GRID_F_W = (0.6, 0.85, 1.15)
# stage-1b grid (final; set 2026-10-15 after the 3-parameter grid above showed, on TRAINING fires only,
# grass-dominated Woolsey wanting m_open <= 0.15 and shrub-dominated Palisades >= 1.2): one multiplier
# per fuel group, θ = (m_G, m_S, m_T, f_w)
GRID4_M_G = (0.08, 0.15, 0.25, 0.4, 0.6)
GRID4_M_S = (0.6, 1.0, 1.5, 2.2, 3.2)
GRID4_M_T = (0.5, 1.0, 2.0, 4.0)
GRID4_F_W = (0.45, 0.6)  # stage 1 optimum at its 0.6 edge on Eaton, Woolsey, Palisades; 0.85 runs kept, unused
# stage-2 grid
GRID_ETA0 = (1e-8, 1e-7, 3e-7, 1e-6, 3e-6, 1e-5)
GRID_KAPPA = (0.3, 1.0, 3.0)
BW_CATEGORY = {"G": "grass_summer", "U": "grass_summer", "S": "pine_lichen", "T": "pine_lichen"}  # [H]

# FireSim stop-rule stations of v3/v4 (true-time HADS hourly summaries) and their FEMS names (T/RH)
FEMS_NAME = {"eaton": "HENNINGER FLATS", "palisades": "MALIBU HILLS", "tubbs": "HAWKEYE", "woolsey": "CHEESEBORO",
             "glass": "KNOXVILLE CREEK", "czu": "BEN LOMOND", "northcomplex": "JARBO GAP"}


# --------------------------------------------------------------------------------- config


def fresh_cfg(fire: str) -> dict:
    """Run set-up of a fresh v7 held-out fire (``calfire-dins/refs/<fire>/v7_config.json``, written blind
    by the v7 data agent from incident narratives, never from damage)."""
    c = json.load(open(CALFIRE / "refs" / fire / "v7_config.json"))
    cfg = {"perimeter": f"raw/frap_perimeter_{fire}.geojson", "timed_perimeters": c.get("timed_perimeters"),
           "start_utc": c["start_utc"], "duration": float(c["duration_min"]), "start": c["start"],
           "origin": tuple(c["origin"]), "lat": c["lat"], "elev": c["elev"], "doy": c["doy"],
           # DMC/DC: the v4 convention (100 / 800) used for every v4-v7 fire, not the agent's 1 March recursion
           # (Dixie 255 / 964, Caldor 656 / 1033, outside the FBP calibration range) [H, disclosed]
           "dmc": c.get("dmc_v4_convention", 100.0), "dc": c.get("dc_v4_convention", 800.0), "ffmc": c.get("ffmc"),
           "fresh": c, "ffmc_raws": (c["ffmc_station"], *c["ffmc_station_window"]), "raws_dir": fire,
           "raws_glob": "raws_FEMS_*.csv"}
    if cfg["timed_perimeters"] and cfg["timed_perimeters"].startswith(str(CALFIRE)):
        cfg["timed_perimeters"] = os.path.relpath(cfg["timed_perimeters"], CALFIRE)
    return cfg


def cfg_v7(fire: str) -> dict:
    if (CALFIRE / "refs" / fire / "v7_config.json").exists() and fire not in TRAINING + SECONDARY:
        return fresh_cfg(fire)
    return A.fire_cfg(fire)


def _local_tz(fire: str, cfg: dict) -> int:
    if cfg.get("fresh"):
        return int(cfg["fresh"].get("utc_offset_h", -7))
    return -8 if fire in ("eaton", "palisades", "camp", "woolsey") else -7


# -------------------------------------------------------------------------------- weather


def _parse_cell(s: str):
    """'13/20@239' -> (13 mph sustained, 239 deg from); '10/@280' ok; '' -> None."""
    try:
        sv, rest = s.split("/", 1)
        d = rest.split("@")[1]
        return float(sv), float(d)
    except (ValueError, IndexError):
        return None


COMPASS = {k: i * 22.5 for i, k in enumerate("N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}


def _vec_mean(pairs):
    """Speed-weighted vector-mean direction (deg from) of [(speed, dir)], or None."""
    pairs = [(v, d) for v, d in pairs if v is not None and d is not None and v > 0]
    if not pairs:
        return None
    x = sum(v * math.sin(math.radians(d)) for v, d in pairs)
    y = sum(v * math.cos(math.radians(d)) for v, d in pairs)
    return math.degrees(math.atan2(x, y)) % 360.0


def wind_obs(fire: str, cfg: dict):
    """(UTC datetimes, 20 ft sustained mph of the fire's v3/v4 wind-event station, direction from deg).

    Speed: the documented wind-event (stop-rule) station of v3/v4. Direction: the speed-weighted vector
    mean over every station in the fire's hourly table (RAWS/HADS and ASOS) [H], because single
    mountain stations are channelled (e.g. Henninger Flats records SSE during the NE Santa Ana of
    Eaton, while the other eight stations record N-NE)."""
    out = []
    if fire == "camp":  # MesoWest JBGC1 (Jarbo Gap) speed; JBGC1 + CICC1 + CSTC1 directions, PST
        per = {}
        for stn in ("JBGC1", "CICC1", "CSTC1"):
            p = CALFIRE / "refs" / "camp" / f"mesowest_{stn}_hourly_2018-11-07_to_11-09_PST.csv"
            for row in csv.DictReader(r for r in open(p) if not r.startswith("#")):
                v, d = row.get("6.1m Wind Speed  mph") or row.get("Wind Speed  mph"), \
                    row.get("6.1m Wind Direction") or row.get("Wind Direction")
                if not v or d not in COMPASS:
                    continue
                t = datetime.strptime(row["datetime_PST"][:13], "%Y-%m-%d %H") + timedelta(hours=8)
                per.setdefault(t, {})[stn] = (float(v), COMPASS[d])
        for t in sorted(per):
            if "JBGC1" not in per[t]:
                continue
            # Direction: JBGC1 only (training set-up decision, disclosed): CICC1 (Openshaw, 268 ft, Sacramento
            # Valley floor, NW valley flow) and CSTC1 (Cohasset, 1-6 mph) are decoupled from the NE downslope
            # event (NIST TN 2135 p.xxii); the first v7 training grid with the all-station mean sent every
            # run south of Paradise (no scored block reached in 105 settings).
            out.append((t.replace(tzinfo=timezone.utc), per[t]["JBGC1"][0], per[t]["JBGC1"][1]))
        return out
    if cfg.get("fresh"):
        # Fresh fires (rule fixed before scoring): speed = the first station in the data agent's ranked
        # ``wind_stations`` with a valid reading that hour; direction = speed-weighted vector mean over all
        # valid listed stations. Invalid = FEMS WSflag != 0, or a frozen sensor (the same speed and direction
        # for >= 3 consecutive hours with speed 0, e.g. STEELY FORK after the fire reached it on 17 Aug 2021).
        rows = fems_obs(fire, cfg, wind=True, all_stations=True, with_flag=True)
        per = {}
        for stn in {r[3] for r in rows}:
            rs = sorted([r for r in rows if r[3] == stn], key=lambda r: r[0])
            for i, r in enumerate(rs):
                frozen = r[1] == 0 and any(all(rs[j][1] == 0 and rs[j][2] == r[2] for j in range(k, k + 3))
                                           for k in range(max(0, i - 2), min(i, len(rs) - 3) + 1) if k + 3 <= len(rs))
                if r[4] == 0 and not frozen:
                    per.setdefault(r[0].replace(minute=0), {})[stn] = (r[1], r[2])
        rank = [x.upper() for x in cfg["fresh"]["wind_stations"]]
        for t in sorted(per):
            prim = next((per[t][n] for n in rank if n in per[t]), None)
            if prim is None:
                continue
            out.append((t, prim[0], _vec_mean([per[t][n] for n in per[t] if n in rank]) or prim[1]))
        return out
    import long_range_v3_validation as V3

    src = cfg.get("stop_source") or V3.STOP_SOURCES[fire]
    tz = _local_tz(fire, cfg)
    for row in csv.DictReader(open(CALFIRE / src["summary"])):
        cell = _parse_cell(row.get(src["column"], "") or "")
        if cell is None:
            continue
        s = row[next(iter(row))]
        if src.get("year"):
            s = f"{src['year']}-{s}"
        t = datetime.strptime(s, src["fmt"]) - timedelta(hours=tz)
        d = _vec_mean([c for c in (_parse_cell(row[k] or "") for k in list(row)[1:]) if c is not None])
        out.append((t.replace(tzinfo=timezone.utc), cell[0], d if d is not None else cell[1]))
    return out


def fems_obs(fire: str, cfg: dict, wind: bool = False, all_stations: bool = False, with_flag: bool = False):
    """FEMS RAWS hourly rows of the fire's station: (UTC, T C, RH %, rain mm) or, with ``wind``,
    (UTC, mph, dir). Label offset (hours, true = label - k) from the data checks when recorded."""
    name = cfg["fresh"]["wind_stations"][0] if cfg.get("fresh") else FEMS_NAME.get(fire)
    if cfg.get("fresh") and not wind:
        name = cfg["fresh"].get("ffmc_station") or name
    k = 0
    if cfg.get("fresh"):
        k = int(cfg["fresh"].get("fems_label_offset_h", {}).get(name, 0))
    else:
        oc = CALFIRE / "refs" / fire / "fems_offset_check.json"
        if oc.exists():
            k = int(json.load(open(oc)).get(name, {}).get("best_k_hours", 0) or 0)
        elif fire in ("palisades", "woolsey") or (fire == "tubbs" and name == "HAWKEYE"):
            k = 1  # DATA_QUALITY.md: FEMS label = obs rounded up + 1 h
    rows = []
    for p in sorted(glob.glob(str(CALFIRE / "refs" / fire / "raws_FEMS_*.csv"))):
        for r in csv.DictReader(open(p)):
            stn = r["StationName"].strip().upper()
            if (not all_stations and stn != str(name).upper()) or r.get("ObservationType", "O") != "O":
                continue
            kk = k
            if all_stations and cfg.get("fresh"):
                kk = int(cfg["fresh"].get("fems_label_offset_h", {}).get(stn, 0))
            t = datetime.strptime(r["DateTime"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc) - timedelta(hours=kk)
            try:
                if wind and with_flag:
                    rows.append((t, float(r["WindSpeed(mph)"]), float(r["WindAzimuth(degrees)"]), stn,
                                 int(float(r.get("WSflag") or 0))))
                elif wind:
                    rows.append((t, float(r["WindSpeed(mph)"]), float(r["WindAzimuth(degrees)"])))
                else:
                    rows.append((t, (float(r["Temperature(F)"]) - 32) / 1.8, float(r["RelativeHumidity(%)"]),
                                 25.4 * float(r["Precipitation(in)"] or 0)))
            except (TypeError, ValueError):
                continue
    rows.sort(key=lambda x: x[0])
    return rows


def thermo_obs(fire: str, cfg: dict):
    if fire == "camp":
        p = CALFIRE / "refs" / "camp" / "mesowest_JBGC1_hourly_2018-11-07_to_11-09_PST.csv"
        out = []
        for row in csv.DictReader(r for r in open(p) if not r.startswith("#")):
            try:
                t = datetime.strptime(row["datetime_PST"][:16], "%Y-%m-%d %H:%M") + timedelta(hours=8)
                out.append((t.replace(tzinfo=timezone.utc), (float(row["2.0m Temperature ° F"]) - 32) / 1.8,
                             float(row["2.0m Relative Humidity %"]), 0.0))
            except (TypeError, ValueError, KeyError):
                continue
        return out
    return fems_obs(fire, cfg)


def _interp_hourly(obs, t0: datetime, hours: np.ndarray, kind: str):
    """Hourly values at t0 + hours (centre of each hour) from irregular obs, by time interpolation
    (directions as unit vectors)."""
    if not obs:
        return None
    ts = np.array([(o[0] - t0).total_seconds() / 3600.0 for o in obs])
    q = hours + 0.5
    if kind == "wind":
        v = np.array([o[1] for o in obs])
        d = np.radians([o[2] for o in obs])
        sp = np.interp(q, ts, v)
        x = np.interp(q, ts, v * np.sin(d) + 1e-6 * np.sin(d))
        y = np.interp(q, ts, v * np.cos(d) + 1e-6 * np.cos(d))
        return sp, np.degrees(np.arctan2(x, y)) % 360.0
    cols = list(zip(*[o[1:] for o in obs]))
    return [np.interp(q, ts, np.array(c)) for c in cols]


def weather(fire: str, cfg: dict) -> dict:
    """Hourly 20 ft wind (mph), direction, hourly FFMC from SPINUP_H before the run start."""
    from firesim.fwi.calculator import hourly_ffmc

    t0 = A._utc(cfg["start_utc"])
    nh = int(math.ceil(cfg["duration"] / 60.0))
    hrs = np.arange(-SPINUP_H, nh, 1.0)
    w = _interp_hourly(wind_obs(fire, cfg), t0, hrs, "wind")
    th = _interp_hourly(thermo_obs(fire, cfg), t0, hrs, "thermo")
    if w is None:
        raise RuntimeError(f"{fire}: no wind observations")
    sp, dr = w
    f = float(cfg["ffmc"]) if cfg.get("ffmc") else float(A.ffmc_of(fire, cfg))
    ff = []
    for i in range(len(hrs)):
        if th is not None:
            T, RH, rain = th[0][i], th[1][i], th[2][i]
            f = hourly_ffmc(float(T), float(RH), float(sp[i] * 1.609 * RAW_TO_10M), float(max(rain, 0.0)), f)
        ff.append(f)
    k = hrs >= 0
    return {"hours": hrs[k], "mph": sp[k], "from_deg": dr[k], "ffmc": np.array(ff)[k], "ffmc_start_daily": f,
            "thermo": th is not None}


# ---------------------------------------------------------------------------------- grid


def load_grid_v7(fire: str) -> dict:
    """180 m run grid with fuel groups (v7 grid = v6 arrays + LANDFIRE shares; same box as v6)."""
    z = np.load(V7DATA / "grids" / f"{fire}.npz")
    g = {k: float(z[k]) for k in ("lat_max", "lng_min", "cell_lat", "cell_lng", "rows", "cols", "h_m")}
    keys = ("f_water", "f_bare", "f_built", "elev_m", "f_G", "f_S", "f_T", "f_U", "f_NB")
    arrs = {k: z[k].astype(float) for k in keys}
    c = COARSEN
    R, C = int(g["rows"]) // c * c, int(g["cols"]) // c * c
    for k, a in arrs.items():
        arrs[k] = a[:R, :C].reshape(R // c, c, C // c, c).mean(axis=(1, 3))
    g.update(rows=R // c, cols=C // c, cell_lat=g["cell_lat"] * c, cell_lng=g["cell_lng"] * c, h_m=g["h_m"] * c)
    g["rows"], g["cols"] = int(g["rows"]), int(g["cols"])
    from firesim.data.dem_loader import slope_aspect

    mid = g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]
    dx = g["cell_lng"] * 111_320.0 * math.cos(math.radians(mid))
    s, a = slope_aspect(arrs["elev_m"], g["cell_lat"] * 111_320.0, dx)
    arrs["slope"], arrs["aspect"] = s, a
    g.update(arrs)
    g["lat_min"] = g["lat_max"] - g["rows"] * g["cell_lat"]
    g["lng_max"] = g["lng_min"] + g["cols"] * g["cell_lng"]
    shares = np.stack([g["f_G"], g["f_S"], g["f_T"], g["f_U"], g["f_NB"]])
    grp = np.array(["G", "S", "T", "U", "NB"])[np.argmax(shares, axis=0)]
    nonfuel = (g["f_water"] >= A.WATER_SHARE) | (g["f_bare"] >= A.BARE_SHARE) | (grp == "NB")
    town = (~nonfuel) & ((grp == "U") | (g["f_built"] >= A.TOWN_BUILT_SHARE))
    grp = np.where(nonfuel, "NB", np.where(town, "U", grp))
    g["group"] = grp
    return g


def build_inputs_v7(g: dict, theta: dict):
    """FuelGrid / TerrainGrid / SpreadModifierGrid for θ = {m_open, m_T}."""
    from firesim.fbp.constants import FuelType
    from firesim.spread.huygens import FuelGrid, SpreadModifierGrid, TerrainGrid

    from long_range_v7_fuels import FBP_OF_GROUP

    grp = g["group"]
    fuel = [[None if grp[r, c] == "NB" else FuelType(FBP_OF_GROUP[grp[r, c]]) for c in range(g["cols"])]
            for r in range(g["rows"])]
    box = (g["lat_min"], g["lat_max"], g["lng_min"], g["lng_max"], g["rows"], g["cols"])
    fg = FuelGrid(fuel, *box)
    tg = TerrainGrid([list(map(float, row)) for row in g["slope"]], [list(map(float, row)) for row in g["aspect"]], *box)
    mg_, ms_ = (theta["m_G"], theta["m_S"]) if "m_G" in theta else (theta["m_open"], theta["m_open"])
    mult = {"G": mg_, "S": ms_, "T": theta["m_T"], "U": mg_ * TOWN_ROS, "NB": 1.0}
    rm = np.vectorize(mult.get)(grp).astype(float)
    one = [[1.0] * g["cols"] for _ in range(g["rows"])]
    mg = SpreadModifierGrid([list(map(float, row)) for row in rm], one, one, *box)
    return fg, tg, mg


def member_pert(m: int | None):
    if m is None:
        return 1.0, 0.0
    rng = np.random.default_rng([SEED, m])
    return float(math.exp(MEMBER_SPEED_SIGMA * rng.standard_normal())), float(MEMBER_DIR_SD * rng.standard_normal())


def schedule_v7(cfg: dict, wx: dict, theta: dict, m: int | None, fmc: float):
    from firesim.spread.huygens import SpreadConditions

    sf, dd = member_pert(m)
    out = []
    for h, mph, d, f in zip(wx["hours"], wx["mph"], wx["from_deg"], wx["ffmc"]):
        out.append((float(h * 60.0), SpreadConditions(
            wind_speed=float(mph * 1.609 * theta["f_w"] * sf), wind_direction=float((d + dd) % 360.0), ffmc=float(f),
            dmc=cfg["dmc"], dc=cfg["dc"], fmc=fmc, grass_cure=GRASS_CURE)))
    return out


def run_member_v7(fire, g, cfg, wx, theta, m, eta=None, out_extra=True):
    """One grid run. ``eta`` = None (A0c) or {"eta0", "kappa"} (V7)."""
    from firesim.fbp.calculator import calculate_foliar_moisture
    from firesim.spread.cellular import run_cellular_simulation
    from firesim.spread.ember_spotting import LongRangeSpotter

    fg, tg, mg = build_inputs_v7(g, theta)
    fmc = calculate_foliar_moisture(cfg["lat"], cfg["origin"][1], cfg["elev"], cfg["doy"])
    sched = schedule_v7(cfg, wx, theta, m, fmc)
    run_cfg = {"ignition_lat": cfg["origin"][0], "ignition_lng": cfg["origin"][1], "duration_hours": cfg["duration"] / 60.0}
    kw = {}
    if cfg["start"] == "gofer":
        kw["initial_burned"], _ = A.gofer_start(fire, cfg, g)
    hook = None
    if eta is not None:
        def wind_at(tm, _s=sched):
            k = max(0, min(len(_s) - 1, int(tm // 60.0)))
            return _s[k][1].wind_speed, _s[k][1].wind_direction

        def ffmc_at(tm, _s=sched):
            return _s[max(0, min(len(_s) - 1, int(tm // 60.0)))][1].ffmc

        grp = g["group"]
        rec = np.where(np.isin(grp, ["S", "T"]), float(eta["kappa"]), 1.0)
        cat = np.vectorize(lambda x: BW_CATEGORY.get(x, "grass_summer"))(grp)
        hook = LongRangeSpotter(g["h_m"], wind_at, float(sched[0][1].ffmc), eta=float(eta["eta0"]), long=A.LONG_MODE,
                                seed=int(SEED * 10 + (m or 0)), receptivity=rec, pe_category=cat, ffmc_at=ffmc_at)
    t = time.time()
    frames = run_cellular_simulation(run_cfg, fg, sched[0][1], spread_modifier_grid=mg, terrain_grid=tg,
                                     dt_minutes=A.DT_SPOT_MIN, snapshot_interval_minutes=cfg["duration"],
                                     enable_spotting=True, compute_perimeter=False, weather_schedule=sched,
                                     seed=int(SEED * 100 + (m or 0)), spot_hook=hook, spot_sample_spacing_m=150.0, **kw)
    arr = frames[-1].arrival.astype(np.float32)
    out = {"arrival": arr, "seconds": time.time() - t}
    if out_extra:
        alb = []
        for fr in frames:
            for s in fr.spot_fires or []:
                r = int((g["lat_max"] - s.lat) / g["cell_lat"])
                c = int((s.lng - g["lng_min"]) / g["cell_lng"])
                if 0 <= r < g["rows"] and 0 <= c < g["cols"]:
                    alb.append((r, c))
        out["albini_spots"] = np.array(A.spot_distances(arr, alb, g["h_m"]), dtype=np.float32).reshape(-1, 4)
    if hook is not None:
        out["lr_spots"] = np.array([(e.t_min, e.row, e.col, e.dist_to_fire_m) for e in hook.events],
                                   dtype=np.float32).reshape(-1, 4)
        out["emitted"] = hook.emitted
        out["landed"] = hook.landed.astype(np.float32) if hook.landed is not None else np.zeros(1, np.float32)
    return out


# --------------------------------------------------------- v6 baseline for fresh fires (A1 comparator)


def v6_baseline_cfg(fire: str, cfg: dict, wx: dict, g: dict) -> dict:
    """v6's A0 set-up applied to a fresh fire by a fixed rule (written before any fresh-fire score):
    one proxy FBP fuel (C-7 when LANDFIRE timber covers >= 60 % of the run box's fuel cells, else C-4,
    as v4 chose C-7 for the Sierra/Klamath North Complex and C-4 for Glass), member wind speed drawn
    uniformly between the 25th and 90th percentiles of the station's hourly 10 m wind in the window
    (m/s), member direction uniformly within ±20 deg of the window's vector-mean direction, hourly
    station gain as v6 (station hourly mean / its 75th percentile, clipped 0.3-1.5), constant FFMC =
    the mean hourly FFMC over the window (the v2 ``ffmc_from_raws`` rule)."""
    fuel = g["group"] != "NB"
    t_share = float(np.mean(g["group"][fuel] == "T")) if fuel.any() else 0.0
    v10 = wx["mph"] * 1.609 * RAW_TO_10M / 3.6
    d = _vec_mean(list(zip(wx["mph"], wx["from_deg"]))) or 0.0
    return {"proxy": "C7" if t_share >= 0.6 else "C4", "t_share": t_share,
            "wind_speed": (float(np.percentile(v10, 25)), float(np.percentile(v10, 90))),
            "wind_from": (d - 20.0, d + 20.0), "ffmc_const": float(np.mean(wx["ffmc"]))}


def run_v6_baseline(fire, cfg, wx, g, m):
    """One v6-style A0 member on the current engine (fresh fires only)."""
    from firesim.fbp.calculator import calculate_foliar_moisture
    from firesim.fbp.constants import FuelType
    from firesim.spread.cellular import run_cellular_simulation
    from firesim.spread.huygens import FuelGrid, SpreadConditions, SpreadModifierGrid, TerrainGrid

    b = v6_baseline_cfg(fire, cfg, wx, g)
    ft = FuelType(b["proxy"])
    nonfuel = g["group"] == "NB"
    town = g["group"] == "U"
    box = (g["lat_min"], g["lat_max"], g["lng_min"], g["lng_max"], g["rows"], g["cols"])
    fg = FuelGrid([[None if nonfuel[r, c] else ft for c in range(g["cols"])] for r in range(g["rows"])], *box)
    tg = TerrainGrid([list(map(float, row)) for row in g["slope"]], [list(map(float, row)) for row in g["aspect"]], *box)
    one = [[1.0] * g["cols"] for _ in range(g["rows"])]
    mg = SpreadModifierGrid([list(map(float, row)) for row in np.where(town, TOWN_ROS, 1.0)], one, one, *box)
    rng = np.random.default_rng([A.SEED, m])
    u = float(rng.uniform(*b["wind_speed"]))
    dfrom = float(rng.uniform(*b["wind_from"])) % 360.0
    v = wx["mph"]
    ref = float(np.percentile(v, A.WIND_REF_Q))
    fmc = calculate_foliar_moisture(cfg["lat"], cfg["origin"][1], cfg["elev"], cfg["doy"])
    sched = [(float(h * 60.0), SpreadConditions(wind_speed=u * 3.6 * float(np.clip(vi / ref, *A.WIND_GAIN)),
                                                wind_direction=dfrom, ffmc=b["ffmc_const"], dmc=cfg["dmc"], dc=cfg["dc"],
                                                fmc=fmc)) for h, vi in zip(wx["hours"], v)]
    run_cfg = {"ignition_lat": cfg["origin"][0], "ignition_lng": cfg["origin"][1], "duration_hours": cfg["duration"] / 60.0}
    kw = {}
    if cfg["start"] == "gofer":
        kw["initial_burned"], _ = A.gofer_start(fire, cfg, g)
    t = time.time()
    frames = run_cellular_simulation(run_cfg, fg, sched[0][1], spread_modifier_grid=mg, terrain_grid=tg,
                                     dt_minutes=A.DT_SPOT_MIN, snapshot_interval_minutes=cfg["duration"],
                                     enable_spotting=True, compute_perimeter=False, weather_schedule=sched,
                                     seed=int(A.SEED * 100 + m), spot_sample_spacing_m=150.0, **kw)
    return {"arrival": frames[-1].arrival.astype(np.float32), "seconds": time.time() - t, "baseline": json.dumps(b)}


def theta_tag(th: dict) -> str:
    if "m_G" in th:
        return f"mg{th['m_G']:g}_ms{th['m_S']:g}_mt{th['m_T']:g}_fw{th['f_w']:g}"
    return f"mo{th['m_open']:g}_mt{th['m_T']:g}_fw{th['f_w']:g}"


def eta_tag(e: dict) -> str:
    return f"e{e['eta0']:g}_k{e['kappa']:g}"


# ------------------------------------------------------------------------------- scoring

_OBS = {}


def obs_for(fire: str):
    if fire not in _OBS:
        cfg = cfg_v7(fire)
        g = load_grid_v7(fire)
        ob = A.observed_blocks(fire, cfg, g)
        _OBS[fire] = (cfg, g, ob)
    return _OBS[fire]


def score_arrivals(fire: str, arrivals: list) -> dict:
    """Per-block ensemble-median interval error (h) and per-block Brier of P(reached by τ), as v6."""
    cfg, g, ob = obs_for(fire)
    dur = cfg["duration"]
    comm = ob["n_bld"] >= A.MIN_BUILDINGS
    scored = comm & np.isfinite(ob["t_hi"]) & (ob["t_hi"] <= dur)
    BA = np.array([A.block_arrival(a, g) for a in arrivals])
    BAc = np.where(np.isfinite(BA), BA, dur)
    med = np.median(BAc, axis=0)
    err_b = A.interval_err(med, ob["t_lo"], ob["t_hi"]) / 60.0
    se_sum = np.zeros(comm.shape)
    se_n = np.zeros(comm.shape)
    for tau in np.arange(60.0, dur + 1e-9, 60.0):
        pr = np.mean(BA <= tau, axis=0)
        yes = scored & (ob["t_hi"] <= tau)
        no = comm & ((ob["t_lo"] >= tau) | ob["outside"])
        k = yes | no
        se_sum[k] += (pr[k] - yes[k]) ** 2
        se_n[k] += 1
    bri_mask = se_n > 0
    bri_b = np.where(bri_mask, se_sum / np.maximum(se_n, 1), np.nan)
    signed = np.where(med < ob["t_lo"], (med - ob["t_lo"]) / 60.0, np.where(med > ob["t_hi"], (med - ob["t_hi"]) / 60.0, 0.0))
    return {"err_b": err_b, "scored": scored, "bri_b": bri_b, "bri_mask": bri_mask, "med": med, "signed": signed,
            "mae_h": float(err_b[scored].mean()) if scored.any() else None,
            "brier": float(np.nanmean(bri_b[bri_mask])) if bri_mask.any() else None,
            "bias_h": float(signed[scored].mean()) if scored.any() else None,
            "n_scored": int(scored.sum()), "n_brier_blocks": int(bri_mask.sum()),
            "burned_ha": float(np.mean([np.isfinite(a).sum() for a in arrivals]) * g["h_m"] ** 2 / 1e4), "ob": ob, "g": g}


def v6_reference(fire: str) -> dict:
    """v6 A0 per-fire MAE / Brier (normalisers of the stage-1 objective; training fires)."""
    fit = json.load(open(A.V6DATA / "results-2026-10-13" / "train" / "fit_v6_arrival.json"))
    row = next(iter(fit["table"][fire].values()))
    return {"mae": row["A0"], "brier": row["brier_A0"]}


# --------------------------------------------------------------------------- stage 1 front


def thetas(stage: str = "1b"):
    if stage == "1":
        return [{"m_open": a, "m_T": b, "f_w": c} for a, b, c in itertools.product(GRID_M_OPEN, GRID_M_T, GRID_F_W)]
    return [{"m_G": a, "m_S": b, "m_T": c, "f_w": d}
            for a, b, c, d in itertools.product(GRID4_M_G, GRID4_M_S, GRID4_M_T, GRID4_F_W)]


def cmd_front_grid(args):
    """Deterministic (unperturbed observed wind) run per θ on one training fire; records only scores."""
    fire = args.fire
    cfg = cfg_v7(fire)
    g = load_grid_v7(fire)
    wx = weather(fire, cfg)
    od = args.out / ("front_grid" if args.stage == "1" else "front_grid_1b")
    od.mkdir(parents=True, exist_ok=True)
    resf = od / (f"{fire}.json" if not args.part else f"{fire}__p{args.part.replace('/', 'of')}.json")
    res = json.loads(resf.read_text()) if resf.exists() else {}
    done = {}
    for q in od.glob(f"{fire}*.json"):
        done.update(json.loads(q.read_text()))
    todo = thetas(args.stage) if not args.theta else [json.loads(args.theta)]
    if args.part:
        k, n = (int(x) for x in args.part.split("/"))
        todo = todo[k::n]
    todo = [th for th in todo if theta_tag(th) not in done or args.redo]
    for th in todo:
        tag = theta_tag(th)
        if tag in res and not args.redo:
            continue
        r = run_member_v7(fire, g, cfg, wx, th, None, out_extra=False)
        s = score_arrivals(fire, [r["arrival"]])
        res[tag] = {"theta": th, "mae_h": s["mae_h"], "brier": s["brier"], "bias_h": s["bias_h"],
                    "burned_ha": s["burned_ha"], "n_scored": s["n_scored"], "seconds": round(r["seconds"], 1)}
        resf.write_text(json.dumps(res, indent=1))
        print(fire, tag, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res[tag].items() if k != "theta"},
              flush=True)


def front_objective(row: dict, ref: dict) -> float:
    """J = (MAE/MAE_v6A0 + Brier/Brier_v6A0) / 2 per fire (v6's objective form; 1 = v6 A0 level)."""
    return 0.5 * (row["mae_h"] / ref["mae"] + row["brier"] / ref["brier"])


def cmd_fit_front(args):
    od = args.out / ("front_grid" if args.stage == "1" else "front_grid_1b")
    tab = {}
    for f in TRAINING:
        tab[f] = {}
        for q in sorted(od.glob(f"{f}*.json")):
            tab[f].update(json.loads(q.read_text()))
    refs = {f: v6_reference(f) for f in TRAINING}
    tags = sorted(set.intersection(*[set(t) for t in tab.values()]))
    J = {f: {t: front_objective(tab[f][t], refs[f]) for t in tags} for f in TRAINING}
    mean_j = {t: float(np.mean([J[f][t] for f in TRAINING])) for t in tags}
    best = min(tags, key=lambda t: mean_j[t])
    lofo = {}
    for f in TRAINING:
        oth = [x for x in TRAINING if x != f]
        tb = min(tags, key=lambda t: np.mean([J[o][t] for o in oth]))
        lofo[f] = {"theta": tab[f][tb]["theta"], "J_out": J[f][tb], "mae_out_h": tab[f][tb]["mae_h"],
                   "brier_out": tab[f][tb]["brier"], "bias_out_h": tab[f][tb]["bias_h"],
                   "v6_A0": refs[f]}
    out = {"_about": {"purpose": "v7 stage 1: front calibration on the training fires (deterministic observed-wind runs)",
                      "objective": front_objective.__doc__, "grid": {"m_open": GRID_M_OPEN, "m_T": GRID_M_T, "f_w": GRID_F_W},
                      "units": "MAE hours; Brier; J dimensionless (1 = v6 A0 level, < 1 better)",
                      "script": "scripts/long_range_v7_arrival.py fit-front"},
           "theta_final": tab[TRAINING[0]][best]["theta"], "mean_J_final": mean_j[best],
           "per_fire_at_final": {f: {**tab[f][best], "J": J[f][best]} for f in TRAINING},
           "lofo": lofo, "mean_J": mean_j, "v6_reference": refs, "n_theta": len(tags)}
    out["_about"]["stage"] = args.stage
    if args.stage == "1b":
        out["_about"]["grid"] = {"m_G": GRID4_M_G, "m_S": GRID4_M_S, "m_T": GRID4_M_T, "f_w": GRID4_F_W}
    (args.out / ("fit_front.json" if args.stage == "1b" else "fit_front_stage1.json")).write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: out[k] for k in ("theta_final", "mean_J_final", "per_fire_at_final", "lofo")}, indent=1, default=float))


# ------------------------------------------------------------------------------ ensembles


def load_theta(args) -> dict:
    if getattr(args, "theta", None):
        return json.loads(args.theta)
    try:
        import long_range_v7_prereg as P

        th = P.PREREG_LR7.get("stage1_front", {}).get("theta_final")
        if th:
            return th
    except Exception:
        pass
    return json.loads((args.out_fit / "fit_front.json").read_text())["theta_final"]


def cmd_run(args):
    fire = args.fire
    cfg = cfg_v7(fire)
    g = load_grid_v7(fire)
    wx = weather(fire, cfg)
    th = load_theta(args)
    od = args.out / fire
    od.mkdir(parents=True, exist_ok=True)
    jobs = []
    for model in args.model:
        if model == "A0c":
            jobs.append(("A0c", None))
        elif model == "A0v6":
            jobs.append(("A0v6", "v6"))
        else:
            for e0 in args.eta0:
                for k in args.kappa:
                    jobs.append(("V7", {"eta0": e0, "kappa": k}))
    for m in range(args.first, args.first + args.members):
        for model, e in jobs:
            tag = "A0c" if e is None else ("A0v6" if e == "v6" else f"V7_{eta_tag(e)}")
            p = od / f"{tag}_m{m}.npz"
            if p.exists() and not args.redo:
                continue
            if e == "v6":
                r = run_v6_baseline(fire, cfg, wx, g, m)
                r["albini_spots"] = np.zeros((0, 4), np.float32)
            else:
                r = run_member_v7(fire, g, cfg, wx, th, m, eta=e)
            r["theta"] = json.dumps(th)
            np.savez_compressed(p, **r)
            print(f"{fire} {tag} m{m}: burned {np.isfinite(r['arrival']).sum()} cells, lr "
                  f"{len(r.get('lr_spots', []))} albini {len(r['albini_spots'])} | {r['seconds']:.0f} s", flush=True)


def load_runs(od: Path, tag: str, members=None):
    fs = sorted(od.glob(f"{tag}_m*.npz"), key=lambda p: int(p.stem.split("_m")[-1]))
    if members:
        fs = [p for p in fs if int(p.stem.split("_m")[-1]) < members]
    return [dict(np.load(p)) for p in fs]


def cmd_fit_eta(args):
    """Stage 2 on the training fires: η0 × κ grid; per fire J = (MAE_V7/MAE_A0c + Brier_V7/Brier_A0c)/2 (v6's
    form, now relative to the calibrated front); minimise the mean over the five fires; LOFO reported."""
    tab = {}
    for f in TRAINING:
        a0 = load_runs(args.out / f, "A0c", args.members)
        sa = score_arrivals(f, [m["arrival"] for m in a0])
        tab[f] = {"A0c": {"mae_h": sa["mae_h"], "brier": sa["brier"], "bias_h": sa["bias_h"], "members": len(a0)}}
        for e0, k in itertools.product(GRID_ETA0, GRID_KAPPA):
            tag = f"V7_{eta_tag({'eta0': e0, 'kappa': k})}"
            mem = load_runs(args.out / f, tag, args.members)
            if not mem:
                continue
            s = score_arrivals(f, [m["arrival"] for m in mem])
            lr = sum(len(m.get("lr_spots", [])) for m in mem) / len(mem)
            tab[f][tag] = {"eta0": e0, "kappa": k, "mae_h": s["mae_h"], "brier": s["brier"], "bias_h": s["bias_h"],
                           "lr_spots_per_member": lr, "members": len(mem),
                           "J": 0.5 * (s["mae_h"] / sa["mae_h"] + s["brier"] / sa["brier"])}
            print(f, tag, {kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in tab[f][tag].items()}, flush=True)
    tags = sorted(set.intersection(*[set(t) - {"A0c"} for t in tab.values()]))
    mean_j = {t: float(np.mean([tab[f][t]["J"] for f in TRAINING])) for t in tags}
    best = min(tags, key=lambda t: mean_j[t])
    lofo = {}
    for f in TRAINING:
        oth = [x for x in TRAINING if x != f]
        tb = min(tags, key=lambda t: np.mean([tab[o][t]["J"] for o in oth]))
        lofo[f] = {"tag": tb, "J_out": tab[f][tb]["J"]}
    out = {"_about": {"purpose": "v7 stage 2: variable-η fit on the calibrated front (training fires)",
                      "objective": cmd_fit_eta.__doc__, "units": "J dimensionless (< 1: embers improve on A0c)",
                      "grid": {"eta0": GRID_ETA0, "kappa": GRID_KAPPA}},
           "eta_final": {"eta0": tab[TRAINING[0]][best]["eta0"], "kappa": tab[TRAINING[0]][best]["kappa"]},
           "mean_J_final": mean_j[best], "mean_J": mean_j, "lofo": lofo, "table": tab}
    (args.out / "fit_eta.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: out[k] for k in ("eta_final", "mean_J_final", "lofo")}, indent=1, default=float))


# ------------------------------------------------------------------------------ bootstrap


def boot_diff(a, b, clusters, n=2000, seed=SEED):
    """mean(b - a), cluster (2 km super-block) bootstrap, percentile 95 % CI (as v6)."""
    return A.boot_mae_diff(a, b, clusters, n=n, seed=seed)


def prereg_hash() -> str:
    try:
        import long_range_v7_prereg as P

        return hashlib.sha256(json.dumps(P.PREREG_LR7, sort_keys=True, default=float).encode()).hexdigest()
    except Exception:
        return "unregistered"


def _model_runs(fire: str, model: str, args) -> list:
    """Arrival grids of a model: A0v6 = v6's own held-out A0 runs for the v6 fires (PREREG_LR6, 20
    members), the v6 rule re-run on the current engine for fresh fires; A0c / V7 = v7 runs."""
    if model == "A0v6" and fire in SECONDARY:
        return [m["arrival"] for m in A.load_members(args.v6_dir / fire, "A0", 0.0, args.members)]
    tag = model if model != "V7" else f"V7_{args.eta_tag}"
    return [m["arrival"] for m in load_runs(args.out / fire, tag, args.members)]


def _spot_tail(fires, args):
    d = []
    for f in fires:
        for m in load_runs(args.out / f, f"V7_{args.eta_tag}", args.members):
            if m.get("lr_spots") is not None and len(m["lr_spots"]):
                d.append(m["lr_spots"][:, 3])
    d = np.concatenate(d) if d else np.zeros(0)
    if not len(d):
        return {"n": 0}
    p1, p3 = float(np.mean(d > 1000)), float(np.mean(d > 3000))
    return {"n": int(len(d)), "share_gt_1km": p1, "share_gt_3km": p3, "tail_ratio_3_over_1": p3 / p1 if p1 else None,
            "max_m": float(d.max()), "in_band_0.10_0.40": bool(p1 and 0.10 <= p3 / p1 <= 0.40),
            "storey_2020": {"tail_ratio_3_over_1": 0.20, "max_m": 13900.0}, "cruz_2012_max_m": 33000.0}


def cmd_score(args):
    """Pre-registered target-A scores: A1 = MAE(A0c) - MAE(A0v6); A2 = MAE(V7) - MAE(A0c) and
    Brier(V7) - Brier(A0c); pooled over the fire set's scored blocks, 2 km cluster bootstrap within
    fires, 2,000 draws, percentile 95 % CI (seed SEED)."""
    out = {"_about": {"purpose": f"PREREG_LR7 target A scores ({args.tag})", "units": "hours (MAE), Brier",
                      "status": args.tag, "prereg_sha256": prereg_hash(), "eta_tag": args.eta_tag,
                      "method": cmd_score.__doc__, "script": "scripts/long_range_v7_arrival.py score"},
           "fires": {}}
    pooled = {k: [] for k in ("A0v6", "A0c", "V7", "cl", "bA0c", "bV7", "bcl")}
    for i, fire in enumerate(args.fires):
        S = {}
        for model in ("A0v6", "A0c", "V7"):
            runs = _model_runs(fire, model, args)
            if runs:
                S[model] = score_arrivals(fire, runs)
                S[model]["members"] = len(runs)
        res = {m: {k: S[m][k] for k in ("mae_h", "brier", "bias_h", "n_scored", "burned_ha", "members")} for m in S}
        sc = S["A0c"]["scored"]
        cl = A.cluster_ids(S["A0c"]["ob"], sc) + i * 10**9
        if "A0v6" in S:
            res["A1_A0c_minus_A0v6_mae"] = boot_diff(S["A0v6"]["err_b"][sc], S["A0c"]["err_b"][sc], cl)
            pooled["A0v6"].append(S["A0v6"]["err_b"][sc])
        if "V7" in S:
            res["A2_V7_minus_A0c_mae"] = boot_diff(S["A0c"]["err_b"][sc], S["V7"]["err_b"][sc], cl)
            bm = S["A0c"]["bri_mask"] & S["V7"]["bri_mask"]
            bcl = A.cluster_ids(S["A0c"]["ob"], bm) + i * 10**9
            res["A2_V7_minus_A0c_brier"] = boot_diff(S["A0c"]["bri_b"][bm], S["V7"]["bri_b"][bm], bcl)
            pooled["V7"].append(S["V7"]["err_b"][sc])
            pooled["bA0c"].append(S["A0c"]["bri_b"][bm])
            pooled["bV7"].append(S["V7"]["bri_b"][bm])
            pooled["bcl"].append(bcl)
        pooled["A0c"].append(S["A0c"]["err_b"][sc])
        pooled["cl"].append(cl)
        out["fires"][fire] = res
        print(fire, json.dumps({m: round(res[m]["mae_h"], 3) for m in S}), flush=True)
    cat = {k: np.concatenate(v) if v else None for k, v in pooled.items()}
    P = {"blocks": int(len(cat["A0c"])), "mae_A0c_h": float(cat["A0c"].mean())}
    if cat["A0v6"] is not None and len(cat["A0v6"]) == len(cat["A0c"]):
        P["mae_A0v6_h"] = float(cat["A0v6"].mean())
        P["A1_A0c_minus_A0v6_mae"] = boot_diff(cat["A0v6"], cat["A0c"], cat["cl"])
    if cat["V7"] is not None:
        P["mae_V7_h"] = float(cat["V7"].mean())
        P["A2_V7_minus_A0c_mae"] = boot_diff(cat["A0c"], cat["V7"], cat["cl"])
        P["A2_V7_minus_A0c_brier"] = boot_diff(cat["bA0c"], cat["bV7"], cat["bcl"])
        P["brier_A0c"], P["brier_V7"] = float(cat["bA0c"].mean()), float(cat["bV7"].mean())
    out["pooled"] = P
    dec = {}
    if "A1_A0c_minus_A0v6_mae" in P:
        dec["A1_calibrated_front_beats_v6_front"] = P["A1_A0c_minus_A0v6_mae"]["p97.5"] < 0
    if "A2_V7_minus_A0c_mae" in P:
        dec["A2_mae_better"] = P["A2_V7_minus_A0c_mae"]["p97.5"] < 0
        dec["A2_brier_not_worse"] = P["A2_V7_minus_A0c_brier"]["p2.5"] <= 0
        dec["A2_embers_add_skill"] = dec["A2_mae_better"] and dec["A2_brier_not_worse"]
    out["decision"] = dec
    out["spot_tail_V7"] = _spot_tail(args.fires, args)
    (args.out / f"scores_{args.tag}.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({"pooled": P, "decision": dec, "tail": out["spot_tail_V7"]}, indent=1, default=float))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("front-grid")
    a.add_argument("--fire", required=True)
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--theta", default=None)
    a.add_argument("--stage", default="1b", choices=["1", "1b"])
    a.add_argument("--part", default=None, help="k/n: run every n-th θ starting at k (parallel parts)")
    a.add_argument("--redo", action="store_true")
    b = sub.add_parser("fit-front")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--stage", default="1b", choices=["1", "1b"])
    c = sub.add_parser("run")
    c.add_argument("--fire", required=True)
    c.add_argument("--model", nargs="+", default=["A0c"])
    c.add_argument("--eta0", nargs="+", type=float, default=[1e-7])
    c.add_argument("--kappa", nargs="+", type=float, default=[1.0])
    c.add_argument("--members", type=int, default=4)
    c.add_argument("--first", type=int, default=0)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--out-fit", type=Path, default=None)
    c.add_argument("--theta", default=None)
    c.add_argument("--redo", action="store_true")
    d = sub.add_parser("fit-eta")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--members", type=int, default=None)
    e = sub.add_parser("score")
    e.add_argument("--fires", nargs="+", required=True)
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--eta-tag", required=True)
    e.add_argument("--members", type=int, default=None)
    e.add_argument("--tag", default="exploratory")
    e.add_argument("--v6-dir", type=Path, default=A.V6DATA / "results-2026-10-13" / "heldout")
    args = ap.parse_args()
    if args.cmd == "run" and args.out_fit is None:
        args.out_fit = args.out.parent
    {"front-grid": cmd_front_grid, "fit-front": cmd_fit_front, "run": cmd_run, "fit-eta": cmd_fit_eta,
     "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
