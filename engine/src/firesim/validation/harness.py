"""Fire-day validation runs: observed day N-1 perimeter -> one burn day -> observed day N.

For each fire-day the grid model starts from the area CFSDS shows burned before the burn day,
runs the burn day's hourly weather, and is scored against the area CFSDS shows burned on that
day (Bennett et al. 2026 protocol; see docs/validation.md for assumptions).

Two initialisations:

- ``"perimeter"`` (default, operational): everything burned before the burn day is the starting
  fire, passed through the same path as an RPAS-observed perimeter (``initial_perimeter`` for the
  largest burned patch, ``initial_burned`` for any other patches). Uses no information from the
  burn day itself.
- ``"active"``: as ``"perimeter"``, but only the parts of the starting fire that grew in the
  last ``active_days`` days (CFSDS day of burning; within ``active_buffer_m``) are active, as an
  RPAS thermal flight would mark the active edges (``active_edges`` in the grid model). The
  rest of the starting fire is burned out. Uses no information from the burn day.
- ``"bennett"``: as Bennett et al. (2026) for W.I.S.E.: only previous-day cells that touch the
  burn day's observed growth are ignited. This uses the observed outcome to choose where the
  fire starts, so it flatters the model; it exists for like-for-like comparison with W.I.S.E.
  Earlier-burned cells are made non-fuel (they cannot burn again).

Scores are computed on the day's *growth*: cells in the model's starting area are removed from
both the prediction and the observation. Cumulative (whole-fire) scores are also reported; they
are higher for big fires simply because old area is counted as agreement.

The run is deterministic. ``members`` is the ensemble hook: each ``Member`` perturbs the
weather stream (wind direction offset or constant direction, wind speed factor); with one
default member the run is the deterministic forecast.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import numpy as np
from scipy import ndimage

from firesim.data.fuel_loader import CODE_SCHEMES
from firesim.fbp.constants import FuelType
from firesim.fwi.classes import fwi_class
from firesim.spread.cellular import burned_outline, run_cellular_simulation
from firesim.spread.diurnal import SPINUP_FROM_HOUR as _DIURNAL_SPINUP_FROM_HOUR
from firesim.spread.huygens import FireVertex, FuelGrid, TerrainGrid
from firesim.spread.simulator import Simulator
from firesim.types import HourlyWeather, SimulationConfig, WeatherInput
from firesim.validation.cfsds import FireDomain
from firesim.validation.metrics import (
    bearing_error_deg,
    hausdorff_m,
    head_spread,
    overlap_scores,
    within_cruz_alexander,
)

EIGHT = np.ones((3, 3), dtype=bool)


@dataclass(frozen=True)
class Member:
    """One ensemble member: a perturbation of the hourly weather stream."""

    name: str = "det"
    wind_direction_offset: float = 0.0  # degrees added to every hour
    constant_wind_direction: float | None = None  # replaces every hour's direction
    wind_speed_factor: float = 1.0

    def apply(self, hourly: tuple[HourlyWeather, ...]) -> tuple[HourlyWeather, ...]:
        out = []
        for h in hourly:
            wd = (self.constant_wind_direction if self.constant_wind_direction is not None
                  else h.wind_direction + self.wind_direction_offset) % 360.0
            out.append(replace(h, wind_direction=wd, wind_speed=h.wind_speed * self.wind_speed_factor))
        return tuple(out)


DETERMINISTIC = (Member(),)


def wind_direction_members(step_deg: float = 30.0) -> tuple[Member, ...]:
    """Bennett et al. (2026) scenario 3: constant wind directions every ``step_deg`` degrees."""
    n = int(round(360.0 / step_deg))
    return tuple(Member(name=f"wd{int(k * step_deg):03d}", constant_wind_direction=k * step_deg)
                 for k in range(n))


@dataclass(frozen=True)
class RunOptions:
    """How a fire-day is simulated and scored."""

    windows_h: tuple[float, ...] = (8.0, 17.0, 24.0)  # scored burn durations from the start hour
    ignition: str = "perimeter"  # "perimeter" (operational), "active" or "bennett"
    active_days: int = 1  # "active": growth of the last N days before the burn day is active
    active_buffer_m: float | None = None  # "active": distance from that growth (None = 1 cell)
    ffmc_spinup: bool = False  # start the hourly FFMC the previous afternoon (case.spinup)
    burning_period: tuple[float, float] | None = None  # local clock hours; None = always
    enable_spotting: bool = False
    oracle_max_h: int = 17  # Bennett scenario 2: best of hours 1..oracle_max_h
    # Seasonal fuel handling for national grids (D-1/M-1 labels -> green D-2/M-2 in summer)
    greenup_doy: int = 150
    leafoff_doy: int = 258
    percent_conifer: float = 50.0  # M-1/M-2 (the 2014b grid carries no percent conifer)
    percent_dead_fir: float = 35.0
    grass_cure_green: float = 60.0  # O-1 curing between green-up and leaf-off
    grass_cure_dormant: float = 90.0  # O-1 curing in spring / fall
    # O-1 curing in FireSim's pre-green-up window (firesim.fbp.curing.SPRING_WINDOW_DOY);
    # None = use grass_cure_dormant there too (the harness's original rule)
    grass_cure_spring: float | None = None
    use_terrain: bool = True


@dataclass
class FireDayCase:
    """Everything needed to simulate one burn day."""

    domain: FireDomain  # cropped to the fire-day's working area
    day: int  # burn day, day of year (Bennett's day n)
    start: datetime  # local start of the simulated burn period
    hourly: tuple[HourlyWeather, ...]  # from ``start``, at least max(windows_h) hours
    ffmc: float  # start-of-day codes (CFSDS row of the previous day)
    dmc: float
    dc: float
    fwi: float = math.nan  # burn day's CFSDS FWI (for classing only)
    bui: float = math.nan
    meta: dict = field(default_factory=dict)
    # Hours before ``start`` (negative hours_from_start) from the previous afternoon, for the
    # hourly-FFMC spin-up (RunOptions.ffmc_spinup)
    spinup: tuple[HourlyWeather, ...] = ()

    @property
    def fire_id(self) -> str:
        return self.domain.fire_id


def crop_for_day(domain: FireDomain, day: int, margin_m: float = 20000.0) -> FireDomain:
    """The domain cropped to everything burned up to ``day`` plus ``margin_m``."""
    burned = (domain.dob > 0) & (domain.dob <= day)
    rr, cc = np.nonzero(burned)
    mr = int(math.ceil(margin_m / domain.dy))
    mc = int(math.ceil(margin_m / domain.dx))
    r0, r1 = max(rr.min() - mr, 0), min(rr.max() + mr + 1, domain.rows)
    c0, c1 = max(cc.min() - mc, 0), min(cc.max() + mc + 1, domain.cols)
    return domain.crop(r0, r1, c0, c1)


# The daily FFMC describes mid-afternoon moisture (about 16:00 LST = 17:00 MDT; Lawson et al.
# 1996), so the hourly FFMC spin-up starts there on the previous day (firesim.spread.diurnal).
SPINUP_FROM_HOUR = int(_DIURNAL_SPINUP_FROM_HOUR)


def build_case(domain: FireDomain, day: int, groups: dict[int, dict], records,
               start_hour: int = 6, hours: int = 24, margin_m: float = 20000.0,
               spinup_from_hour: int = SPINUP_FROM_HOUR) -> FireDayCase:
    """FireDayCase for burn day ``day`` from a domain, its CFSDS rows and hourly weather."""
    from firesim.validation.weather import burn_hours, doy_to_date

    prev = groups.get(day - 1) or groups.get(day)
    if prev is None:
        raise KeyError(f"{domain.fire_id}: no CFSDS row for day {day - 1} or {day}")
    today = groups.get(day, prev)
    d = doy_to_date(domain.year, day)
    start = datetime(d.year, d.month, d.day, start_hour)
    return FireDayCase(
        domain=crop_for_day(domain, day, margin_m), day=day, start=start,
        hourly=burn_hours(records, start, hours),
        ffmc=float(prev["ffmc"]), dmc=float(prev["dmc"]), dc=float(prev["dc"]),
        fwi=float(today.get("fwi", math.nan)), bui=float(today.get("bui", math.nan)),
        meta={"codes_from_day": int(prev["DOB"])},
        spinup=_spinup_hours(records, start, spinup_from_hour),
    )


def _spinup_hours(records, start: datetime, from_hour: int) -> tuple[HourlyWeather, ...]:
    """Hourly records from ``from_hour`` on the day before ``start`` up to ``start``, with
    negative hours_from_start (empty if any hour is missing)."""
    from firesim.validation.weather import burn_hours

    t0 = datetime(start.year, start.month, start.day, from_hour) - timedelta(days=1)
    n = int(round((start - t0).total_seconds() / 3600.0))
    try:
        hrs = burn_hours(records, t0, n)
    except KeyError:
        return ()
    return tuple(replace(h, hours_from_start=h.hours_from_start - n) for h in hrs)


def grass_cure_for(doy: int, opts: RunOptions) -> float:
    """O-1 degree of curing (%) for a fire-day: green season, FireSim's pre-green-up window
    (when ``opts.grass_cure_spring`` is set) or dormant."""
    from firesim.fbp.curing import in_spring_curing_window

    if opts.greenup_doy <= doy < opts.leafoff_doy:
        return opts.grass_cure_green
    if opts.grass_cure_spring is not None and in_spring_curing_window(doy):
        return opts.grass_cure_spring
    return opts.grass_cure_dormant


def _seasonal(ft: FuelType | None, doy: int, opts: RunOptions) -> FuelType | None:
    green = opts.greenup_doy <= doy < opts.leafoff_doy
    if green and ft is FuelType.D1:
        return FuelType.D2
    if green and ft is FuelType.M1:
        return FuelType.M2
    if green and ft is FuelType.M3:
        return FuelType.M4
    return ft


def fuel_grid_for(domain: FireDomain, doy: int, opts: RunOptions,
                  non_fuel: np.ndarray | None = None) -> FuelGrid:
    """FuelGrid from the domain's fuel codes, with the seasonal leafless/green choice."""
    code_map = CODE_SCHEMES[domain.fuel_scheme]
    lut = {code: _seasonal(ft, doy, opts) for code, ft in code_map.items()}
    codes = domain.fuel
    uniq, inv = np.unique(codes, return_inverse=True)
    types = np.array([lut.get(int(u)) for u in uniq], dtype=object)[inv.reshape(codes.shape)]
    if non_fuel is not None:
        types[non_fuel] = None
    return FuelGrid(
        fuel_types=types.tolist(), lat_min=domain.lat_min, lat_max=domain.lat_max,
        lng_min=domain.lng_min, lng_max=domain.lng_max, rows=domain.rows, cols=domain.cols,
    )


def terrain_grid_for(domain: FireDomain) -> TerrainGrid | None:
    """Slope (%) and upslope aspect (deg from true north) on exactly the simulation grid.

    Same finite-difference formula as ``firesim.data.dem_loader`` (central differences,
    aspect 0 where slope < 0.1 %), applied to the domain's elevation without resampling so
    terrain cells coincide with fuel cells.
    """
    if domain.elevation is None:
        return None
    elev = domain.elevation.astype(np.float64)
    dz_south, dz_east = np.gradient(elev, domain.dy, domain.dx)
    dz_north = -dz_south
    slope = np.sqrt(dz_north ** 2 + dz_east ** 2) * 100.0
    aspect = np.degrees(np.arctan2(dz_east, dz_north)) % 360.0
    aspect[slope < 0.1] = 0.0
    return TerrainGrid(
        slope=slope.tolist(), aspect=aspect.tolist(), lat_min=domain.lat_min,
        lat_max=domain.lat_max, lng_min=domain.lng_min, lng_max=domain.lng_max,
        rows=domain.rows, cols=domain.cols,
    )


def _cell_centres(mask: np.ndarray, domain: FireDomain) -> list[tuple[float, float]]:
    rr, cc = np.nonzero(mask)
    lats = domain.lat_max - (rr + 0.5) * domain.cell_lat
    lngs = domain.lng_min + (cc + 0.5) * domain.cell_lng
    return list(zip(lats.tolist(), lngs.tolist()))


def initial_state(domain: FireDomain, day: int, ignition: str):
    """(initial_perimeter, initial_burned, extra non-fuel mask) for the chosen initialisation."""
    dob = domain.dob
    prior = (dob > 0) & (dob < day)
    if ignition == "perimeter":
        labels, n = ndimage.label(prior, structure=EIGHT)
        sizes = ndimage.sum(prior, labels, index=np.arange(1, n + 1))
        largest = labels == (int(np.argmax(sizes)) + 1)
        outline = burned_outline(largest, domain.lat_max, domain.lng_min,
                                 domain.cell_lat, domain.cell_lng)
        others = prior & ~largest
        return outline, _cell_centres(others, domain) or None, None
    if ignition == "bennett":
        growth = dob == day
        ign = (dob == day - 1) & ndimage.binary_dilation(growth, structure=EIGHT)
        if not ign.any():
            ign = (dob == day - 1)
        return None, _cell_centres(ign, domain), prior & ~ign
    raise ValueError(f"unknown ignition {ignition!r}")


def active_geometry(domain: FireDomain, day: int, days: int = 1) -> dict | None:
    """GeoJSON MultiPolygon (lng/lat) of the cells that burned in the ``days`` days before
    ``day``: the operational stand-in for the active edges seen on a thermal flight."""
    from rasterio.features import shapes
    from rasterio.transform import Affine

    dob = domain.dob
    mask = (dob >= day - days) & (dob < day) & (dob > 0)
    if not mask.any():
        return None
    transform = Affine(domain.cell_lng, 0.0, domain.lng_min, 0.0, -domain.cell_lat,
                       domain.lat_max)
    polys = [g["coordinates"] for g, _ in shapes(mask.astype(np.uint8), mask=mask,
                                                transform=transform)]
    return {"type": "MultiPolygon", "coordinates": polys}


def _setup(case: FireDayCase, opts: RunOptions, member: Member) -> dict:
    """Grids, starting state and base configuration of one fire-day run."""
    dom = case.domain
    duration_h = max(max(opts.windows_h), float(opts.oracle_max_h))
    perimeter, burned_pts, extra_nonfuel = initial_state(
        dom, case.day, "perimeter" if opts.ignition == "active" else opts.ignition)
    active = active_geometry(dom, case.day, opts.active_days) if opts.ignition == "active" else None
    fuel_grid = fuel_grid_for(dom, case.day, opts, extra_nonfuel)
    terrain = terrain_grid_for(dom) if opts.use_terrain else None
    hourly = member.apply(case.hourly)[: int(math.ceil(duration_h))]
    spinup = member.apply(case.spinup) if opts.ffmc_spinup else ()
    clat = dom.lat_max - 0.5 * dom.rows * dom.cell_lat
    clng = dom.lng_min + 0.5 * dom.cols * dom.cell_lng
    elev = float(np.mean(dom.elevation)) if dom.elevation is not None else None
    h0 = hourly[0]
    config = SimulationConfig(
        ignition_lat=clat, ignition_lng=clng,
        weather=WeatherInput(h0.temperature, h0.relative_humidity, h0.wind_speed,
                             h0.wind_direction, 0.0),
        duration_hours=duration_h, snapshot_interval_minutes=duration_h * 60.0,
        ffmc=case.ffmc, dmc=case.dmc, dc=case.dc,
        grass_cure=grass_cure_for(case.day, opts),
        percent_conifer=opts.percent_conifer, percent_dead_fir=opts.percent_dead_fir,
        day_of_year=case.day, elevation_m=elev, hourly_weather=spinup + hourly,
        start_hour=case.start.hour + case.start.minute / 60.0,
        burning_period=opts.burning_period,
    )
    return {"config": config, "fuel_grid": fuel_grid, "terrain": terrain,
            "perimeter": perimeter, "burned_pts": burned_pts, "active": active}


def _run(setup: dict, config: SimulationConfig, opts: RunOptions,
         ros_multiplier: float = 1.0, seed: str | None = None) -> tuple[np.ndarray, float, float]:
    """Run the grid model for ``config`` on ``setup``'s grids: (arrival, run_s, fmc).
    ``seed`` seeds the spotting model (used only with ``opts.enable_spotting``)."""
    dom_shape = (setup["fuel_grid"].rows, setup["fuel_grid"].cols)
    perimeter = setup["perimeter"]
    front = [FireVertex(lat=a, lng=b) for a, b in perimeter] if perimeter else None
    sim = Simulator(config, setup["fuel_grid"], setup["terrain"], initial_front=front,
                    initial_burned=setup["burned_pts"], enable_spotting=opts.enable_spotting)
    schedule = sim.weather_schedule()
    if ros_multiplier != 1.0:
        schedule = [(t, replace(c, ros_multiplier=c.ros_multiplier * ros_multiplier))
                    for t, c in schedule]
    duration_h = config.duration_hours
    t0 = time.perf_counter()
    # The same call Simulator._run_cellular makes, without building per-cell frame dicts.
    frames = run_cellular_simulation(
        config={"ignition_lat": config.ignition_lat, "ignition_lng": config.ignition_lng,
                "duration_hours": duration_h},
        fuel_grid=setup["fuel_grid"], conditions=schedule[0][1], terrain_grid=setup["terrain"],
        dt_minutes=1.0, weather_schedule=schedule,
        snapshot_interval_minutes=duration_h * 60.0, enable_spotting=opts.enable_spotting,
        initial_perimeter=perimeter, initial_burned=setup["burned_pts"], compute_perimeter=False,
        active_edges=setup["active"], active_edge_buffer_m=opts.active_buffer_m,
        seed=seed,
    )
    run_s = time.perf_counter() - t0
    arrival = frames[-1].arrival if frames and frames[-1].arrival is not None else \
        np.full(dom_shape, np.inf)
    return arrival, run_s, schedule[0][1].fmc


def simulate(case: FireDayCase, opts: RunOptions = RunOptions(),
             member: Member = Member()) -> dict:
    """Run one member of a fire-day; returns arrival minutes (inf = unburned) and run info."""
    setup = _setup(case, opts, member)
    # Spotting seed per fire-day and member (same draws as the former global seeding)
    arrival, run_s, fmc = _run(setup, setup["config"], opts,
                               seed=f"{case.fire_id}:{case.day}:{member.name}")
    return {"arrival": arrival, "run_s": run_s, "fmc": fmc}


def simulate_ensemble(case: FireDayCase, opts: RunOptions, ens) -> dict:
    """The deterministic run plus ``ens.n_members`` members perturbed exactly as
    ``firesim.spread.ensemble.run_ensemble`` perturbs them (same draws for the same seed).

    Returns ``det`` (deterministic arrival), ``members`` (stacked member arrivals, minutes,
    inf = unburned), the per-member perturbation records and the total model run time.
    """
    from firesim.spread.ensemble import perturb_config

    setup = _setup(case, opts, Member())
    det, run_s, fmc = _run(setup, setup["config"], opts, seed=f"{case.fire_id}:{case.day}:det")
    base_fmc = Simulator(setup["config"], fuel_grid=setup["fuel_grid"])._foliar_moisture()
    rng = random.Random(ens.seed)
    stack = np.empty((ens.n_members,) + det.shape, dtype=np.float32)
    records = []
    for i in range(ens.n_members):
        cfg, k_ros, rec = perturb_config(setup["config"], ens, rng, base_fmc)
        stack[i], s, _ = _run(setup, cfg, opts, k_ros,
                              seed=f"{case.fire_id}:{case.day}:ens{ens.seed}:{i}")
        run_s += s
        records.append(rec)
    return {"det": det, "members": stack, "records": records, "run_s": run_s, "fmc": fmc}


def _fuel_mix(domain: FireDomain, mask: np.ndarray, day: int, opts: RunOptions) -> dict[str, float]:
    """Fraction of ``mask`` cells per FBP fuel type ("NF" = non-fuel)."""
    code_map = CODE_SCHEMES[domain.fuel_scheme]
    codes, counts = np.unique(domain.fuel[mask], return_counts=True)
    out: dict[str, float] = {}
    total = counts.sum()
    for code, n in zip(codes, counts):
        ft = _seasonal(code_map.get(int(code)), day, opts)
        key = ft.value if ft is not None else "NF"
        out[key] = out.get(key, 0.0) + float(n) / total
    return out


def score_day(case: FireDayCase, arrival: np.ndarray, opts: RunOptions) -> dict:
    """Bennett-style scores at each window, the oracle-duration score and spread diagnostics."""
    dom = case.domain
    dob = dom.dob
    prior = (dob > 0) & (dob < case.day)
    start = arrival <= 0.0
    excluded = start | prior
    obs_growth = (dob == case.day) & ~excluded
    obs_cum = (dob > 0) & (dob <= case.day)
    cell = dom.cell_area_m2
    out: dict = {}

    obs_head = head_spread(excluded, obs_growth, dom.dx, dom.dy)
    for w in opts.windows_h:
        pred_growth = (arrival <= w * 60.0) & ~excluded
        s = overlap_scores(pred_growth, obs_growth, cell)
        c = overlap_scores(pred_growth | prior | start, obs_cum, cell)
        key = f"{w:g}h"
        out[key] = {
            **{k: v for k, v in s.as_dict().items()},
            "hausdorff_m": hausdorff_m(pred_growth, obs_growth, dom.dx, dom.dy),
            "cum_f1": c.f1, "cum_iou": c.iou,
        }
        ph = head_spread(excluded, pred_growth, dom.dx, dom.dy)
        if ph is not None and obs_head is not None:
            out[key].update({
                "pred_spread_m": ph.distance_m, "obs_spread_m": obs_head.distance_m,
                "spread_ratio": ph.distance_m / obs_head.distance_m if obs_head.distance_m else math.nan,
                "bearing_err_deg": bearing_error_deg(ph.bearing_deg, obs_head.bearing_deg),
                "ros_within_35pct": within_cruz_alexander(ph.distance_m, obs_head.distance_m),
            })
        else:
            out[key].update({"pred_spread_m": ph.distance_m if ph else 0.0,
                             "obs_spread_m": obs_head.distance_m if obs_head else math.nan,
                             "spread_ratio": 0.0 if ph is None and obs_head else math.nan,
                             "bearing_err_deg": math.nan,
                             "ros_within_35pct": False if ph is None and obs_head else None})
    # Bennett scenario 2: the hourly perimeter with the best F1
    best = None
    for h in range(1, opts.oracle_max_h + 1):
        s = overlap_scores((arrival <= h * 60.0) & ~excluded, obs_growth, cell)
        f1 = -1.0 if math.isnan(s.f1) else s.f1
        if best is None or f1 > best[0]:
            best = (f1, h, s)
    f1, h, s = best
    pred_best = (arrival <= h * 60.0) & ~excluded
    out["oracle"] = {**s.as_dict(), "hour": h,
                     "hausdorff_m": hausdorff_m(pred_best, obs_growth, dom.dx, dom.dy)}
    max_w = max(max(opts.windows_h), opts.oracle_max_h) * 60.0
    burned_any = arrival <= max_w
    out["edge_hit"] = bool(burned_any[0].any() or burned_any[-1].any()
                           or burned_any[:, 0].any() or burned_any[:, -1].any())
    out["obs_growth_ha"] = float(obs_growth.sum()) * cell / 1e4
    out["initial_ha"] = float((prior | start).sum()) * cell / 1e4
    out["obs_growth_fuel"] = _fuel_mix(dom, obs_growth, case.day, opts) if obs_growth.any() else {}
    out["obs_head_bearing_deg"] = obs_head.bearing_deg if obs_head else math.nan
    return out


def dominant_fuel(mix: dict[str, float]) -> str:
    return max(mix, key=mix.get) if mix else "none"


def run_fire_day(case: FireDayCase, opts: RunOptions = RunOptions(),
                 members: tuple[Member, ...] = DETERMINISTIC) -> dict:
    """Simulate and score a fire-day for each member.

    Returns a JSON-friendly record: case info, per-member scores, and (with more than one
    member) the best member by oracle-duration F1 (Bennett's scenario 3 when the members are
    ``wind_direction_members()``) and the fraction of members burning each observed-growth
    cell within 17 h (a first burn-probability reliability number).
    """
    rec: dict = {
        "fire_id": case.fire_id, "year": case.domain.year, "day": case.day,
        "date": case.start.date().isoformat(), "start_hour": case.start.hour,
        "ignition": opts.ignition, "spotting": opts.enable_spotting,
        "options": {"active_days": opts.active_days, "active_buffer_m": opts.active_buffer_m,
                    "ffmc_spinup": opts.ffmc_spinup, "burning_period": opts.burning_period},
        "ffmc": case.ffmc, "dmc": case.dmc, "dc": case.dc, "fwi": case.fwi, "bui": case.bui,
        "fwi_class": fwi_class(case.fwi) if not math.isnan(case.fwi) else None,
        "res_m": round(min(case.domain.dx, case.domain.dy), 1),
        "grid": [case.domain.rows, case.domain.cols],
        "mean_ws_kmh": float(np.mean([h.wind_speed for h in case.hourly[:17]])),
        "members": {},
    }
    burn_count = None
    for m in members:
        res = simulate(case, opts, m)
        scores = score_day(case, res["arrival"], opts)
        scores["run_s"] = res["run_s"]
        rec["fmc"] = res["fmc"]
        for k in ("obs_growth_ha", "initial_ha", "obs_growth_fuel", "obs_head_bearing_deg"):
            rec[k] = scores.pop(k)
        rec["members"][m.name] = scores
        if len(members) > 1:
            b = (res["arrival"] <= 17 * 60.0).astype(np.int32)  # ensemble hook: burn counts
            burn_count = b if burn_count is None else burn_count + b
    rec["dominant_fuel"] = dominant_fuel(rec.get("obs_growth_fuel", {}))
    if len(members) > 1:
        rec["best_member"] = max(
            rec["members"], key=lambda k: np.nan_to_num(rec["members"][k]["oracle"]["f1"], nan=-1))
        dob = case.domain.dob
        obs = dob == case.day
        frac = burn_count / len(members)
        rec["obs_growth_mean_burn_fraction"] = float(frac[obs].mean()) if obs.any() else None
    return rec
