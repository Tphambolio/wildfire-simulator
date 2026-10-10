#!/usr/bin/env python3
"""Sensitivity of the Hamada structure-spread option to its FireSim heuristics.

Owner decisions D2 and D4 (2026-10-09; ``docs/PROJECT_RECORD.md`` §3): keep the defaults
``neighbour_cutoff_m = 30`` and ``wildland_contact_m = 10`` and keep every Microsoft footprint
(garages and sheds included), and measure how much the results depend on them
(``docs/structure-spread-spec.md`` §4.4, §8 item 5). Nothing here is tuned.

For each site x weather day, one deterministic FBP grid run is made exactly as the API makes it
(Edmonton fuel grid, DEM, building mask of the 4 nearest neighbourhoods, no water mask, no
spotting, fixed seed). The run's flame panels (burned cells and arrival minutes) are then
reused for every structure-spread variant, so only the structure layer changes:

- ``neighbour_cutoff_m`` 20 / 30 / 45 m (contact 10 m);
- ``wildland_contact_m`` 5 / 10 / 20 m (cutoff 30 m);
- the front-contact rule: from the footprint's own grid cells (the engine's rule since
  2026-10-09, spec §3) vs the first rule, nearest burned cell edge within the contact distance
  of the footprint (``default_old_rule``, kept to compare with report R7);
- footprint filter: none vs drop footprints under 40 m^2 (a FireSim [H] size, roughly a
  single-car garage or small shed; sensitivity only, not a default).

Structure units are built from every footprint in the fuel grid's box, as the API does
(``BuildingIndex.building_geoms_in_bbox``). The default variant is checked against the counts
the ``Simulator`` itself returns with ``structure_spread=True``.

Outputs are **aggregate only** (counts per hour, distances, graph statistics). No per-building
list or time is written. Structure spread is illustrative and not validated in Canada; these
are model-sensitivity numbers, not loss estimates.

Usage (from the repo root; writes JSON + Markdown tables outside the repo):

    PYTHONPATH=engine/src python3 scripts/structure_sensitivity.py \\
        --out ~/dev/wildfire/reports/data/structure-sensitivity-2026-10-09
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))

from firesim.data.building_index import BuildingIndex  # noqa: E402
from firesim.data.dem_loader import load_terrain_grid  # noqa: E402
from firesim.data.environment import load_environment_mask  # noqa: E402
from firesim.data.fuel_loader import load_fuel_grid  # noqa: E402
from firesim.fbp.constants import FuelType  # noqa: E402
from firesim.fwi.calculator import FWICalculator  # noqa: E402
from firesim.spread.simulator import Simulator  # noqa: E402
from firesim.structures.spread import (  # noqa: E402
    SOURCE_FRONT,
    SOURCE_STRUCTURE,
    WindPeriod,
    building_cell_contact_times,
    footprint_contact_times,
    hamada_spread,
)
from firesim.structures.units import LocalFrame, build_units  # noqa: E402
from firesim.types import SimulationConfig, WeatherInput  # noqa: E402

DATA = REPO / "data"
FUEL = DATA / "Edmonton_FBP_FuelLayer_20251105_10m.tif"
DEM = DATA / "edmonton_dem.tif"
BUILDINGS = DATA / "edmonton_buildings.geojson.gz"
NEIGHBOURHOODS = DATA / "edmonton_neighbourhoods.geojson"

DEFAULT_CUTOFF_M = 30.0
DEFAULT_CONTACT_M = 10.0
SMALL_FOOTPRINT_M2 = 40.0  # [H] sensitivity only (decision D4)
LOCAL_RADIUS_M = 2000.0  # graph statistics around each site

# Representative Edmonton WUI settings (see the report for the reasons). Ignition points are in
# ravine / open-space vegetation; the wind direction (FROM, degrees) is set per site so the
# modelled fire meets the housing in three different ways (along a ravine, across a ravine,
# from a grass corridor). Scenario choices, not a wind climatology. The engine snaps a point
# on non-fuel to the nearest fuel cell and reports the distance.
SITES = {
    "A_mature_ravine_edge": {
        "label": "Mature inner-ring housing (many detached garages) on both sides of a narrow "
                 "aspen/mixedwood ravine; wind along the ravine",
        "lat": 53.5136, "lng": -113.4718, "wind_direction": 200.0,
    },
    "B_low_density_ravine_edge": {
        "label": "Large-lot lower-density housing on the downwind side of a wide creek ravine; "
                 "wind across the ravine",
        "lat": 53.4718, "lng": -113.5626, "wind_direction": 270.0,
    },
    "C_newer_suburb_grass_edge": {
        "label": "1990s-2000s suburb (narrow side yards, few outbuildings) beside an open grass "
                 "corridor; wind along the corridor with a component into the housing",
        "lat": 53.4363, "lng": -113.5250, "wind_direction": 240.0,
    },
}

# Weather days: constant over the run, 10 m open wind. Codes stated; ISI, BUI and FWI are
# computed from them with FWICalculator and reported. D-2 does not spread below BUI 80
# (cffdrs rule), so on the moderate day only grass, M-2 and C-2 carry fire.
WEATHER = {
    "moderate": {"temperature": 22.0, "relative_humidity": 40.0, "wind_speed": 15.0,
                 "ffmc": 88.0, "dmc": 25.0, "dc": 250.0},
    "extreme": {"temperature": 30.0, "relative_humidity": 20.0, "wind_speed": 30.0,
                "ffmc": 94.0, "dmc": 80.0, "dc": 500.0},
}
DAY_OF_YEAR = 220  # early August: green aspen (D-2), cured grass
GRASS_CURE = 85.0
DURATION_H = 6.0
SEED = 20261009

VARIANTS = [
    # name, cutoff, contact, min footprint area (m^2), front-contact rule
    ("default", 30.0, 10.0, 0.0, "cells"),
    ("cutoff_20", 20.0, 10.0, 0.0, "cells"),
    ("cutoff_45", 45.0, 10.0, 0.0, "cells"),
    ("contact_5", 30.0, 5.0, 0.0, "cells"),
    ("contact_20", 30.0, 20.0, 0.0, "cells"),
    ("drop_lt40m2", 30.0, 10.0, SMALL_FOOTPRINT_M2, "cells"),
    # Interaction check: the size filter where more buildings are reached (contact 20 m)
    ("contact_20_drop_lt40m2", 30.0, 20.0, SMALL_FOOTPRINT_M2, "cells"),
    # The first rule (nearest burned cell edge within 10 m of the footprint), for comparison
    ("default_old_rule", 30.0, 10.0, 0.0, "footprint"),
]
CONTACT_BANDS_M = (0.0, 5.0, 10.0, 20.0, 30.0, 50.0)


class _CapturingSimulator(Simulator):
    """Simulator that keeps the grid run's flame panels and the engine's own structure result."""

    def _structure_spread(self, emitters, duration_min, arrival=None):
        self.captured_emitters = emitters
        self.captured_arrival = arrival
        self.captured_structures = super()._structure_spread(emitters, duration_min, arrival)
        return self.captured_structures


def contact_gap_m(units, sel, cells_xy, half) -> np.ndarray:
    """Distance (m) from each selected footprint to the nearest burned cell's edge."""
    import shapely
    from scipy.spatial import cKDTree

    tree = cKDTree(cells_xy)
    out = []
    for i in np.nonzero(sel)[0]:
        d, _ = tree.query([units.x[i], units.y[i]], k=min(16, len(cells_xy)))
        idx = tree.query_ball_point([units.x[i], units.y[i]], float(np.max(d)) + 4 * half)
        dist = shapely.distance(units.footprints[i], shapely.points(cells_xy[idx])) - half
        out.append(max(float(dist.min()), 0.0))
    return np.asarray(out)


def fwi_codes(w: dict) -> dict:
    isi = FWICalculator.calculate_isi(w["ffmc"], w["wind_speed"])
    bui = FWICalculator.calculate_bui(w["dmc"], w["dc"])
    return {"isi": round(isi, 1), "bui": round(bui, 1),
            "fwi": round(FWICalculator.calculate_fwi(isi, bui), 1)}


def graph_stats(units, mask=None) -> dict:
    """Neighbour-graph statistics (all units, or those in ``mask``)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(units)
    deg = np.diff(units.indptr)
    sel = np.ones(n, dtype=bool) if mask is None else mask
    near = units.nearest_separation_m[sel]
    fin = near[np.isfinite(near)]
    g = csr_matrix((np.ones(len(units.indices)), units.indices, units.indptr), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    sizes = np.bincount(lab)
    largest = sizes.max() if len(sizes) else 0
    return {
        "units": int(sel.sum()),
        "median_area_m2": round(float(np.median(units.area_m2[sel])), 1),
        "share_lt40m2": round(float(np.mean(units.area_m2[sel] < SMALL_FOOTPRINT_M2)), 3),
        "median_nearest_sep_m": round(float(np.median(fin)), 2) if len(fin) else None,
        "share_isolated": round(float(np.mean(~np.isfinite(near))), 4),
        "median_degree": float(np.median(deg[sel])),
        "mean_degree": round(float(np.mean(deg[sel])), 2),
        # Largest connected component of the whole graph, as a share of all units (city-wide)
        "largest_component_share": round(float(largest / n), 4) if mask is None else None,
        "units_in_largest_component": (round(float(np.mean(lab[sel] == np.argmax(sizes))), 4)
                                       if len(sizes) else None),
    }


def _components(units) -> np.ndarray:
    """Connected-component label of every unit in the neighbour graph."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(units)
    g = csr_matrix((np.ones(len(units.indices)), units.indices, units.indptr), shape=(n, n))
    return connected_components(g, directed=False)[1]


def run_metrics(units, res, ign_xy, cells_xy, duration_min) -> dict:
    """Aggregate outputs of one structure-spread result."""
    import shapely
    from scipy.spatial import cKDTree

    hours = list(range(1, int(duration_min // 60) + 1))
    t, src = res.t_min, res.source
    hourly = []
    for h in hours:
        by = t <= h * 60.0 + 1e-9
        hourly.append({
            "hour": h,
            "front": int(np.sum(by & (src == SOURCE_FRONT))),
            "structure": int(np.sum(by & (src == SOURCE_STRUCTURE))),
            "total": int(np.sum(by)),
        })
    inv = np.isfinite(t)
    out = {"hourly": hourly, "involved": int(inv.sum()),
           "front": int(np.sum(inv & (src == SOURCE_FRONT))),
           "structure": int(np.sum(inv & (src == SOURCE_STRUCTURE)))}
    if inv.any():
        d_ign = np.hypot(units.x[inv] - ign_xy[0], units.y[inv] - ign_xy[1])
        out["max_dist_from_ignition_m"] = round(float(d_ign.max()), 0)
        pts = shapely.multipoints(np.column_stack([units.x[inv], units.y[inv]]))
        out["hull_area_ha"] = round(float(shapely.convex_hull(pts).area) / 1e4, 1)
    s2s = inv & (src == SOURCE_STRUCTURE)
    if s2s.any() and len(cells_xy):
        dist, _ = cKDTree(cells_xy).query(np.column_stack([units.x[s2s], units.y[s2s]]))
        out["s2s_beyond_burned_cells_m"] = {"median": round(float(np.median(dist)), 0),
                                            "p90": round(float(np.percentile(dist, 90)), 0),
                                            "max": round(float(dist.max()), 0)}
        tt = t[s2s]
        out["first_s2s_min"] = round(float(tt.min()), 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "dev/wildfire/reports/data/structure-sensitivity-2026-10-09")
    ap.add_argument("--sites", nargs="*", default=list(SITES))
    ap.add_argument("--weather", nargs="*", default=list(WEATHER))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    timings: dict = {}

    t0 = time.time()
    fuel_grid = load_fuel_grid(str(FUEL))
    terrain = load_terrain_grid(str(DEM))
    bidx = BuildingIndex(str(BUILDINGS), str(NEIGHBOURHOODS))
    bbox = (fuel_grid.lat_min, fuel_grid.lat_max, fuel_grid.lng_min, fuel_grid.lng_max)
    footprints = bidx.building_geoms_in_bbox(*bbox)
    timings["load_s"] = round(time.time() - t0, 1)
    print(f"loaded grid {fuel_grid.rows}x{fuel_grid.cols}, {len(footprints)} footprints "
          f"({timings['load_s']} s)", flush=True)

    # Footprint areas in the same equirectangular frame build_units uses (for the size filter)
    import shapely

    geoms = np.asarray(footprints, dtype=object)
    cent = shapely.centroid(geoms)
    clat, clng = shapely.get_y(cent), shapely.get_x(cent)
    frame = LocalFrame(float((clat.min() + clat.max()) / 2), float((clng.min() + clng.max()) / 2))
    area_m2 = shapely.area(geoms) * frame.m_per_deg_lng * 111320.0
    keep_big = area_m2 >= SMALL_FOOTPRINT_M2

    # Units per (cutoff, filter): built once, reused by every site and weather day
    units_by_key: dict = {}
    for _, cutoff, _, min_area, _ in VARIANTS:
        key = (cutoff, min_area)
        if key in units_by_key:
            continue
        t1 = time.time()
        sel = geoms if min_area == 0 else geoms[keep_big]
        units_by_key[key] = build_units(list(sel), neighbour_cutoff_m=cutoff, bbox=bbox, frame=frame)
        timings[f"build_units_cutoff{cutoff:g}_min{min_area:g}_s"] = round(time.time() - t1, 1)
        print(f"units cutoff={cutoff} min_area={min_area}: {len(units_by_key[key])} units, "
              f"{units_by_key[key].n_edges} pairs ({timings[f'build_units_cutoff{cutoff:g}_min{min_area:g}_s']} s)",
              flush=True)

    city_graph = {f"cutoff{c:g}_min{m:g}": graph_stats(u) for (c, m), u in units_by_key.items()}
    labels_by_key = {k: _components(u) for k, u in units_by_key.items()}

    results: dict = {
        "meta": {
            "script": "scripts/structure_sensitivity.py",
            "fuel": FUEL.name, "dem": DEM.name, "buildings": BUILDINGS.name,
            "footprints_in_grid_box": len(footprints),
            "footprints_ge40m2": int(keep_big.sum()),
            "grid": [fuel_grid.rows, fuel_grid.cols],
            "duration_h": DURATION_H, "day_of_year": DAY_OF_YEAR, "grass_cure": GRASS_CURE,
            "seed": SEED, "spotting": False, "water_mask": False, "burning_period": None,
            "weather": {k: {**w, **fwi_codes(w)} for k, w in WEATHER.items()},
            "variants": [{"name": n, "cutoff_m": c, "contact_m": k, "min_area_m2": m,
                          "contact_rule": rule} for n, c, k, m, rule in VARIANTS],
        },
        "city_graph": city_graph,
        "runs": {},
    }

    for site in args.sites:
        s = SITES[site]
        # Building mask on the fuel grid exactly as the API runner does (4 nearest neighbourhoods)
        nearest = bidx.nearest_neighbourhoods(s["lat"], s["lng"], n=4)
        bgeoms = bidx.building_geoms_for(nearest)
        mask = load_environment_mask(bounds=bbox, rows=fuel_grid.rows, cols=fuel_grid.cols,
                                     building_geoms=bgeoms)
        ft = [list(r) for r in fuel_grid.fuel_types]
        for r in range(fuel_grid.rows):
            for c in range(fuel_grid.cols):
                if mask[r, c] and ft[r][c] is not None:
                    ft[r][c] = None
        masked = dataclasses.replace(fuel_grid, fuel_types=ft)

        u0 = units_by_key[(DEFAULT_CUTOFF_M, 0.0)]
        ix, iy = u0.frame.to_local(s["lat"], s["lng"])
        local = np.hypot(u0.x - ix, u0.y - iy) <= LOCAL_RADIUS_M
        site_graph = {}
        for (c, m), u in units_by_key.items():
            lx, ly = u.frame.to_local(s["lat"], s["lng"])
            site_graph[f"cutoff{c:g}_min{m:g}"] = graph_stats(
                u, np.hypot(u.x - lx, u.y - ly) <= LOCAL_RADIUS_M)
        results["runs"].setdefault(site, {"label": s["label"], "graph_2km": site_graph,
                                          "ignition": [s["lat"], s["lng"]],
                                          "wind_from_deg": s["wind_direction"],
                                          "neighbourhoods_masked": len(nearest),
                                          "units_within_2km": int(local.sum())})

        for wname in args.weather:
            w = WEATHER[wname]
            config = SimulationConfig(
                ignition_lat=s["lat"], ignition_lng=s["lng"],
                weather=WeatherInput(temperature=w["temperature"],
                                     relative_humidity=w["relative_humidity"],
                                     wind_speed=w["wind_speed"],
                                     wind_direction=s["wind_direction"],
                                     precipitation_24h=0.0),
                duration_hours=DURATION_H, snapshot_interval_minutes=60.0,
                ffmc=w["ffmc"], dmc=w["dmc"], dc=w["dc"],
                grass_cure=GRASS_CURE, day_of_year=DAY_OF_YEAR, seed=SEED,
            )
            sim = _CapturingSimulator(config, fuel_grid=masked, terrain_grid=terrain,
                                      default_fuel=FuelType.C2, structure_spread=True,
                                      structure_footprints=footprints)
            t1 = time.time()
            frames = list(sim.run())
            fbp_s = round(time.time() - t1, 1)
            em = sim.captured_emitters
            last = frames[-1]
            engine_counts = last.structure_spread or {}
            run = {
                "fbp_area_ha": round(last.area_ha, 1),
                "fbp_area_by_hour_ha": [round(f.area_ha, 1) for f in frames],
                "max_hfi_kw_m": round(last.max_hfi_kw_m, 0),
                "ignition_snapped_m": last.ignition_snapped_m,
                "fbp_plus_engine_structure_s": fbp_s,
                "engine_default_counts": {k: engine_counts.get(k) for k in
                                          ("units_in_run", "units_front_contact",
                                           "units_structure_to_structure", "units_involved")},
                "variants": {},
            }
            duration_min = DURATION_H * 60.0
            wind = [WindPeriod(float(st), float(c.wind_speed), float(c.wind_direction))
                    for st, c in sim._schedule]
            arrival = sim.captured_arrival
            for name, cutoff, contact, min_area, rule in VARIANTS:
                u = units_by_key[(cutoff, min_area)]
                t2 = time.time()
                if em is None or len(em.x) == 0:
                    run["variants"][name] = {"involved": 0}
                    continue
                lat = em.lat0 + np.asarray(em.y) / em.m_per_deg_lat
                lng = em.lng0 + np.asarray(em.x) / em.m_per_deg_lng
                cx, cy = u.frame.to_local(lat, lng)
                if rule == "cells":
                    t_front = building_cell_contact_times(u, arrival, bbox, contact)
                else:
                    t_front = footprint_contact_times(u, cx, cy, em.start_min, em.cell_size,
                                                      contact)
                res = hamada_spread(u, t_front, wind, duration_min=duration_min)
                lx, ly = u.frame.to_local(s["lat"], s["lng"])
                m = run_metrics(u, res, (float(lx), float(ly)), np.column_stack([cx, cy]),
                                duration_min)
                m["runtime_s"] = round(time.time() - t2, 2)
                # Ceiling with unlimited time: every unit in a graph component the front touches
                lab = labels_by_key[(cutoff, min_area)]
                touched = np.unique(lab[np.isfinite(t_front) & (t_front <= duration_min)])
                m["units_in_front_touched_components"] = int(np.isin(lab, touched).sum())
                if name in ("default", "default_old_rule"):
                    # How far the burned cell that gives front contact is from the footprint
                    hit = np.isfinite(t_front) & (t_front <= duration_min)
                    if hit.any():
                        gap = contact_gap_m(u, hit, np.column_stack([cx, cy]), em.cell_size / 2)
                        m["front_contact_gap_m"] = {
                            "median": round(float(np.median(gap)), 1),
                            "p90": round(float(np.percentile(gap, 90)), 1),
                            "max": round(float(gap.max()), 1),
                            "share_over_10m": round(float(np.mean(gap > 10.0)), 3)}
                run["variants"][name] = m
            if em is not None and len(em.x):
                # How many units lie within each distance of a burned cell (end of run): shows
                # how the contact distance interacts with the grid and the building mask
                lat = em.lat0 + np.asarray(em.y) / em.m_per_deg_lat
                lng = em.lng0 + np.asarray(em.x) / em.m_per_deg_lng
                cx, cy = u0.frame.to_local(lat, lng)
                run["units_within_m_of_burned_cells"] = {
                    f"{b:g}": int(np.isfinite(footprint_contact_times(
                        u0, cx, cy, em.start_min, em.cell_size, b)).sum())
                    for b in CONTACT_BANDS_M}
                run["cell_size_m"] = round(float(em.cell_size), 1)
            d = run["variants"]["default"]
            run["default_matches_engine"] = (
                engine_counts.get("units_involved") == d.get("involved")
                and engine_counts.get("units_structure_to_structure") == d.get("structure"))
            results["runs"][site][wname] = run
            print(f"{site} {wname}: area {run['fbp_area_ha']} ha, involved "
                  + ", ".join(f"{k}={v.get('involved')}" for k, v in run["variants"].items())
                  + f"; engine match {run['default_matches_engine']} ({fbp_s} s)", flush=True)

    results["timings"] = timings
    (args.out / "structure_sensitivity.json").write_text(json.dumps(results, indent=1))
    (args.out / "structure_sensitivity_tables.md").write_text(tables(results))
    print(f"wrote {args.out}")
    return 0


def _rel(v, d):
    if d in (None, 0) or v is None:
        return "n/a"
    return f"{100.0 * (v - d) / d:+.0f} %"


def tables(r: dict) -> str:
    """Markdown tables of the aggregate results."""
    lines = ["# Structure spread sensitivity: tables (generated)", ""]
    lines += ["## City-wide neighbour graph", "",
              "| Setting | Units | Median nearest sep. (m) | No neighbour within cutoff | "
              "Median / mean degree | Largest component |", "|---|---|---|---|---|---|"]
    for k, g in r["city_graph"].items():
        lines.append(f"| {k} | {g['units']:,} | {g['median_nearest_sep_m']} | "
                     f"{100 * g['share_isolated']:.1f} % | {g['median_degree']:g} / {g['mean_degree']} | "
                     f"{100 * g['largest_component_share']:.1f} % |")
    for site, sr in r["runs"].items():
        lines += ["", f"## {site}: {sr['label']}", "",
                  f"Units within {LOCAL_RADIUS_M / 1000:g} km: {sr['units_within_2km']:,}", "",
                  "| Setting | Median area (m²) | < 40 m² | Median nearest sep. (m) | Isolated | "
                  "Median / mean degree | In city's largest component |", "|---|---|---|---|---|---|---|"]
        for k, g in sr["graph_2km"].items():
            lines.append(f"| {k} | {g['median_area_m2']} | {100 * g['share_lt40m2']:.0f} % | "
                         f"{g['median_nearest_sep_m']} | {100 * g['share_isolated']:.1f} % | "
                         f"{g['median_degree']:g} / {g['mean_degree']} | "
                         f"{100 * g['units_in_largest_component']:.0f} % |")
        for wname in ("moderate", "extreme"):
            run = sr.get(wname)
            if not run:
                continue
            d = run["variants"]["default"]
            lines += ["", f"### {wname}: FBP area {run['fbp_area_ha']} ha at {DURATION_H:g} h, "
                          f"max HFI {run['max_hfi_kw_m']:,.0f} kW/m; default matches engine: "
                          f"{run['default_matches_engine']}", "",
                      "| Variant | Involved h1 / h2 / h3 / h4 / h5 / h6 | Front / bldg-to-bldg at 6 h | "
                      "vs default | Max dist. from ignition (m) | Hull (ha) | Bldg-to-bldg beyond "
                      "burned cells, median / max (m) | Units in front-touched components | "
                      "Spread time (s) |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for name, v in run["variants"].items():
                hrs = " / ".join(str(h["total"]) for h in v.get("hourly", []))
                b = v.get("s2s_beyond_burned_cells_m") or {}
                lines.append(
                    f"| {name} | {hrs} | {v.get('front', 0)} / {v.get('structure', 0)} | "
                    f"{_rel(v.get('involved'), d.get('involved')) if name != 'default' else '—'} | "
                    f"{v.get('max_dist_from_ignition_m', '—')} | {v.get('hull_area_ha', '—')} | "
                    f"{b.get('median', '—')} / {b.get('max', '—')} | "
                    f"{v.get('units_in_front_touched_components', '—')} | {v.get('runtime_s', '—')} |")
            bands = run.get("units_within_m_of_burned_cells")
            if bands:
                lines += ["", f"Units within X m of a burned cell by {DURATION_H:g} h (cell "
                              f"{run.get('cell_size_m')} m): "
                          + ", ".join(f"{k} m: {v}" for k, v in bands.items())]
            for name in ("default", "default_old_rule"):
                g = run["variants"].get(name, {}).get("front_contact_gap_m")
                if g:
                    lines += ["", f"Front-contact gap ({name}): footprint to the nearest burned "
                                  f"cell edge, median {g['median']} / p90 {g['p90']} / max "
                                  f"{g['max']} m; share over 10 m {100 * g['share_over_10m']:.0f} %"]
    lines += ["", f"Timings: {json.dumps(r.get('timings', {}))}", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
