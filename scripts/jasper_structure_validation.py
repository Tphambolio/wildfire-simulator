#!/usr/bin/env python3
"""End-state, building-by-building check of the Hamada structure-spread option on Jasper 2024.

Specification: ``docs/structure-spread-spec.md`` §8 (validation plan). **Illustrative — not
validated in Canada.** This is a structure-only run: there is no Jasper fuel grid in FireSim,
so the wildland front is not modelled. The ember entry of 24 July 2024 is imposed as a fixed
ignition set taken in advance from the published accounts, and the Hamada option
(``firesim.structures``, engine defaults unchanged) spreads fire from it over the Jasper
footprints with the documented wind for a fixed burning window. The modelled end state is
scored against the Municipality of Jasper rapid damage assessment, one unit per footprint.

Every choice is fixed in ``PREREG`` below **before** the damage status is read (the commit that
adds this file precedes the first run against the observed statuses). Nothing is tuned on
Jasper; all sensitivity runs are reported.

Data (public, read-only; **not in the repo**):
- Municipality of Jasper "Damage Assessment Structures" ArcGIS feature layer 0 (owner MOJ_GIS).
  Only ``OBJECTID`` and ``Status`` plus the footprint geometry are requested, for the townsite
  subset (``Value_Type = 'Jasper'``, used in the WHERE clause only). Street and address fields
  are never requested or written. The layer states no licence: it is cached for local use only
  under ``~/dev/wildfire/validation-data/jasper2024/`` and must never be committed or
  redistributed.
- Timing and wind from CFS NOR-X-433 (OGL-Canada 2.0) and the municipal after-action review;
  spacing finding from FPInnovations WF TR 2025 n.04. Page references are in ``PREREG``.

Outputs (outside the repo): ``results.json``, ``tables.md`` and aggregate PNG maps in
``--out``. No address, street name or owner field exists in any input or output.

Usage (from the repo root):

    PYTHONPATH=engine/src python3 scripts/jasper_structure_validation.py \\
        --out ~/dev/wildfire/validation-data/jasper2024/results-2026-10-10

Burn-out (``--burnout``, third pre-registration ``PREREG_BURNOUT``, 2026-10-10): the PREREG
primary run and its 11 sensitivity runs repeated with building burn-out on (a unit passes fire
only until its design fire ends, spec §4.3), each against its Hamada-only twin:

    PYTHONPATH=engine/src python3 scripts/jasper_structure_validation.py --burnout \\
        --out ~/dev/wildfire/validation-data/jasper2024/results-burnout-2026-10-10

Ember ignition (``--embers``, second pre-registration ``PREREG_EMBERS``, 2026-10-10): the same
units, seeds, wind, window and baselines, with ember ignition of buildings added to Hamada
(``firesim.structures.embers``, spec §6), parameters fixed from the literature:

    PYTHONPATH=engine/src python3 scripts/jasper_structure_validation.py --embers \\
        --out ~/dev/wildfire/validation-data/jasper2024/results-embers-2026-10-10
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))

from firesim.structures.embers import EmberOptions  # noqa: E402
from firesim.structures.spread import (  # noqa: E402
    SOURCE_EMBER,
    WindPeriod,
    burnout_minutes,
    hamada_spread,
    spread_with_embers,
)
from firesim.structures.units import build_units  # noqa: E402

LAYER_URL = ("https://services7.arcgis.com/1glZz4XajYW77W8H/arcgis/rest/services/"
             "Damage_Assessment_Structures/FeatureServer/0")
WHERE = "Value_Type='Jasper'"  # townsite subset (search report 2026-10-09: 1,119 polygons)
OUT_FIELDS = "OBJECTID,Status"  # never the street / address field
ALLOWED_PROPS = {"OBJECTID", "Status"}
DEFAULT_DATA_DIR = Path.home() / "dev" / "wildfire" / "validation-data" / "jasper2024"
CACHE_NAME = "moj_damage_townsite_status.geojson"

# --------------------------------------------------------------------------------------------
# Pre-registered set-up (fixed 2026-10-10 before any Status value was read; do not edit after
# the first scored run: add a new, dated block instead and report both).
# Times are minutes after 18:00 MDT, 24 July 2024 (first structure ignitions: NOR-X-433
# §4.6.2 p.35 "documented within the Jasper townsite at 18:00"; AAR p.21 and p.56 18:00;
# pages as printed).
# Seed points are area centres rounded to 0.001 deg (~100 m): they locate an area, not a
# building. They were placed from OpenStreetMap features matching the areas the accounts name;
# the names are not repeated here (no street names in the repo).
# --------------------------------------------------------------------------------------------
PREREG = {
    "date_fixed": "2026-10-10",
    "outcome_primary": "Destroyed = 1; Visible Damage and No Visible Damage = 0",
    "outcome_sensitivity": "Destroyed or Visible Damage = 1; No Visible Damage = 0",
    "seed_areas": {
        # Municipal AAR p.21 (18:00: first impacts in the industrial area, a south-end road area
        # and a west-side neighbourhood) and p.56 (18:00: the south end of town first to ignite)
        "aar_south_end": (52.870, -118.086),
        "aar_industrial": (52.873, -118.078),
        "aar_west_neighbourhood": (52.869, -118.102),
        # FPInnovations WF TR 2025 n.04 §5.1 p.14 (also p.3): one of the first rooftop
        # ignitions in a west-end crescent, then the roof of a south-west lodge
        "fpi_west_end_first_roof": (52.870, -118.099),
        "fpi_southwest_lodge": (52.871, -118.092),
    },
    "ignition_sets": {
        "I0_primary": {"areas": ["aar_south_end", "aar_industrial", "aar_west_neighbourhood"],
                       "nearest_per_area": 5},
        "I1_fewer": {"areas": ["aar_south_end", "aar_industrial", "aar_west_neighbourhood"],
                     "nearest_per_area": 1},
        "I2_more": {"areas": ["aar_south_end", "aar_industrial", "aar_west_neighbourhood"],
                    "nearest_per_area": 15},
        "I3_fpi_first_roofs": {"areas": ["fpi_west_end_first_roof", "fpi_southwest_lodge"],
                               "nearest_per_area": 5},
        # No document locations: the upwind-most 5 % of units along the wind axis
        "I4_windward_band": {"windward_fraction": 0.05},
    },
    "ignition_time_min": 0.0,  # all seeds at 18:00
    # Wind (10 m, constant): speed NOR-X-433 p.33 "light southerly cross-slope winds of about
    # 15 km/h" (17:00-19:00) and p.36 Tangle station SSW 14 km/h at 20:00; direction SW from
    # p.35 (northern neighbourhoods spared "due to the southwesterly winds"; collapse winds
    # "southwest"). The 17:45-18:05 collapse gusts (up to ~110 km/h, inferred from tree damage,
    # p.35) end before structure spread and are represented by the imposed ember ignitions.
    "wind_speed_kmh": 15.0,
    "wind_from_deg": 225.0,
    "window_min": 360.0,  # 18:00-24:00 (rain ~22:00-22:45: NOR-X-433 p.36; FPI p.3)
    "neighbour_cutoff_m": 30.0,  # engine default (spec §4.4, D2)
    "combustible_fraction": 1.0,  # engine default (spec §4.4)
    "front_contact_rule": "not used (structure-only; seeds imposed)",
    "sensitivity": {
        "neighbour_cutoff_m": [20.0, 45.0],
        "wind_factor": [0.75, 1.25],
        "wind_from_deg": [202.5],  # SSW: Tangle 20:00 (p.36)
        "window_min": [240.0, 720.0],  # to 22:00 (rain) / to 06:00 next day
        "ignition_sets": ["I1_fewer", "I2_more", "I3_fpi_first_roofs", "I4_windward_band"],
        "outcome": ["destroyed_or_damaged"],
        "score_excluding_seeds": True,
    },
    "baselines": {
        "a_distance_band_count_matched": "units ranked by edge-to-edge distance to the nearest "
                                         "seed footprint; the N nearest, N = FireSim's count",
        "a_obs_distance_band_observed_count": "same ranking, N = observed destroyed count "
                                              "(uses the observed total; reference only)",
        "b1_separation_5m_one_step": "seeds + units within 5 m of a seed (FPI p.26: < 5 m to a "
                                     "burned structure -> P(destroyed) > 0.8; exec. summary p.2)",
        "b2_separation_5m_percolation": "seeds + every unit linked to a seed by gaps <= 5 m",
        "c_random_observed_rate": "each unit destroyed with p = observed rate; 1000 draws",
        "d_seeds_only": "no structure spread (spec §8 item 4b)",
    },
    "decision_rule": "FireSim is worth showing on this test only if its Cohen's kappa exceeds "
                     "baselines (a) and (d) (spec §8 item 4); all baselines reported",
    "kappa_difference_ci": {"block_m": 250.0, "resamples": 2000, "seed": 20261010},
    "random_seed": 20261010,
    "random_draws": 1000,
    "distance_bands_m": [0, 100, 250, 500, 1000, 1e9],
}


# --------------------------------------------------------------------------------------------
# Second pre-registration: ember ignition (fixed 2026-10-10, committed before the ember runs
# were scored; PREREG above is unchanged and its primary run is repeated here as the
# Hamada-only reference). All ember parameters are the specified values of
# docs/structure-spread-spec.md §6, taken from the papers; nothing is chosen on Jasper.
# --------------------------------------------------------------------------------------------
PREREG_EMBERS = {
    "date_fixed": "2026-10-10",
    "question": "Does adding ember ignition between buildings to Hamada improve the building-"
                "level end state at Jasper (the R10 misses were destroyed clusters 250-500 m "
                "from the seeds, NOR-X-433 p.35, FPI pp.14-15)?",
    "base": "PREREG primary: I0 seeds (15) at 18:00, 1,119 townsite units, wind 15 km/h from "
            "225 deg (10 m), window 360 min, cutoff 30 m, f_b 1, Destroyed = 1",
    "embers": {
        "design_fire_kw_m2": 150,  # PROCI24 p.3; owner decision D1 (spec C1)
        "gr_structure_pcs_per_mw_s": 10.0,  # [T] Qin25 p.147; FSJ104686 p.4 (spec C14)
        "ember_mass_g": 0.2,  # FSJ104686 p.3
        "transport": "Himoto lognormal (HT08 eqs 38-40), D = sqrt(footprint area), "
                     "truncated at the 99th percentile; crosswind normal sigma_Y = 0.92 D",
        "pdf_wind": "10 m wind (spec C17)",
        "flight_wind": "10 m wind / 1.15 (6.1 m; Andrews 2009 p.58) [H]",
        "v_air": "0.064 x 6.1 m wind (Qin25 p.157)",
        "ignition": "psi* = 0.211 / ((v + 0.073)(4.111 - v)) g/cm2 (FSJ104686 eq 1), "
                    "delays TOA + 42 s + 300 s",
        "wildland_embers": "none (structure-only run; no fuel grid)",
        "seeds_burn_design_fire_from_min": 0.0,
        "dt_min": 0.5,
    },
    "runs": {
        "E0_primary": "Hamada + embers, values above",
        "H0_hamada_only": "PREREG primary repeated (reference, expected kappa 0.472)",
        "E1_gr_5.68": "GR' = 5.68 pcs/(MW s) (Qin25 eq 3.17, reference value)",
        "E2_design_fire_400": "400 kW/m2, 300/3600/300 s (FSJ104686 p.2)",
        "E3_cutoff_20": "cutoff 20 m with embers (H at 20 m reported alongside)",
        "E4_cutoff_45": "cutoff 45 m with embers (H at 45 m reported alongside)",
        "E5_embers_only": "embers without Hamada (diagnostic: what embers alone do)",
        "E6_embers_only_400": "embers without Hamada, 400 kW/m2 (diagnostic)",
        "E7_wind_x1.25": "wind 18.75 km/h with embers",
        "E8_wind_x0.75": "wind 11.25 km/h with embers",
        # Added after a model-only dry run (geometry with random statuses; no observed status
        # read) showed no ember ignition at all at 15 km/h in E0-E8. Chosen from a documented
        # value, not tuned: the Tangle station gust of 27 km/h at 20:00 (NOR-X-433 p.36),
        # held for the whole window (an upper case for the documented evening wind).
        "E9_gust_27": "wind 27 km/h with embers (H9: Hamada at 27 km/h alongside)",
        "E10_gust_27_400": "wind 27 km/h, 400 kW/m2 with embers",
    },
    "metrics": "as PREREG; plus kappa difference E - H (same cutoff) with the 250 m block "
               "bootstrap; units ignited by embers, by distance band from the seeds, and "
               "how many of them were destroyed",
    "decision_rule": "Embers help on this test only if kappa(E0) - kappa(H0) > 0 with the 95 % "
                     "block-bootstrap interval excluding zero, AND kappa(E0) > kappa of the "
                     "count-matched distance band (a). Otherwise reported as no improvement.",
    "expectation_from_the_equations_only": "Before scoring (model quantities only, no status "
        "read): at 15 km/h (v_air = 0.23 m/s) psi* = 0.178 g/cm2, i.e. ~1.37 million embers "
        "on a median 154 m2 footprint, while one 154 m2 building emits 0.46 million embers in "
        "its whole 150 kW/m2 design fire (2.4 million at 400). Himoto X_max is 46 m (150) / "
        "57 m (400). Ember ignition at the default therefore needs several burning neighbours "
        "within ~50 m and is not expected to reach the 250-500 m clusters.",
}


# --------------------------------------------------------------------------------------------
# Third pre-registration: building burn-out (fixed 2026-10-10 and committed before any burn-out
# run was scored; PREREG and PREREG_EMBERS are unchanged). The burn-out time is the end of the
# building design fire, taken from the literature (docs/structure-spread-spec.md §4.3, §5);
# nothing is chosen on Jasper.
# --------------------------------------------------------------------------------------------
PREREG_BURNOUT = {
    "date_fixed": "2026-10-10",
    "question": "Does stopping a building from passing fire once its design fire has ended "
                "(burn-out) improve the building-level end state at Jasper over Hamada alone "
                "(kappa 0.472) and the count-matched distance band (a) (kappa 0.516)?",
    "burnout": {
        "rule": "a unit involved at t passes fire (Hamada crossings, ember emission) only until "
                "t + D; a Hamada crossing not complete by t + D never arrives [H]",
        "D_primary_min": 66.0,  # 150 kW/m2 design fire: 5 + 1 + 60 min (PROCI24 p.3; D1)
        "D_scenario_min": 70.0,  # 400 kW/m2: 300 + 3600 + 300 s (FSJ104686 p.2)
        "sources": "PROCI24 p.2 (HRR decreases linearly to zero when all fuel is consumed), "
                   "p.3 (5/1/60 min); Qin25 eq 6.2 p.146 (t_decay = end of combustion), p.67 "
                   "(emission ceases when HRR returns to zero); FSJ104686 p.2 (design fire from "
                   "ignition to burnout without suppression); FSJ104651 p.3 (no burnout in "
                   "ELMFIRE or Hamada)",
    },
    "runs": {
        "B0_primary": "PREREG primary (I0 seeds, 15 km/h from 225 deg, 360 min, cutoff 30 m, "
                      "f_b 1) with burn-out D = 66 min; reference H0 = the same without burn-out",
        "B_sensitivity": "the 11 PREREG sensitivity runs (cutoff 20 / 45 m; wind x0.75 / x1.25; "
                         "wind from 202.5 deg; window 240 / 720 min; ignition sets I1-I4), each "
                         "with burn-out D = 66 min against its own Hamada-only twin",
        "B_D70": "B0 with D = 70 min (400 kW/m2 design fire)",
        "BE0_embers": "B0 plus ember ignition (PREREG_EMBERS E0 values, 150 kW/m2, GR' 10) "
                      "with burn-out; reference E0 without burn-out",
    },
    "metrics": "as PREREG (confusion counts, precision, recall, F1, kappa; outcome Destroyed); "
               "kappa difference B - H (same configuration) and B - (a) with the 250 m block "
               "bootstrap; units removed by burn-out, and how many of them were destroyed",
    "decision_rule": "Burn-out helps on this test only if kappa(B0) - kappa(H0) > 0 with the "
                     "95 % block-bootstrap interval excluding zero, AND kappa(B0) > kappa of "
                     "the count-matched distance band (a). Otherwise reported as no improvement. "
                     "The engine default (burn-out on) is set on physical grounds (one design "
                     "fire for embers and Hamada) and is not changed by this result.",
    "expectation_from_the_equations_only": "Before scoring (model quantities only, no status "
        "read): at 15 km/h (4.17 m/s) Hamada crossing times for a0 = 12 m are 8-19 min "
        "downwind and 15-58 min crosswind / upwind for d = 3-30 m, all below 66 min, so "
        "burn-out can remove a crossing only for large footprints, long gaps near the 30 m "
        "cutoff crosswind / upwind, or calm periods. Few changes are expected at cutoff 20 / "
        "30 m; more at 45 m.",
}


def run_embers(units, seed, wind_kmh, wind_from, window, fb, opts):
    t_front = np.where(seed, PREREG["ignition_time_min"], np.inf)
    return spread_with_embers(units, t_front, [WindPeriod(0.0, wind_kmh, wind_from)],
                              duration_min=window, fb=fb, embers=opts)


# ---------------------------------------------------------------------------------- data


def fetch_layer(data_dir: Path) -> Path:
    """Download the townsite footprints with OBJECTID and Status only; cache outside the repo."""
    data_dir.mkdir(parents=True, exist_ok=True)
    features, offset, page = [], 0, 1000
    while True:
        params = {"where": WHERE, "outFields": OUT_FIELDS, "returnGeometry": "true",
                  "outSR": "4326", "f": "geojson", "orderByFields": "OBJECTID",
                  "resultOffset": str(offset), "resultRecordCount": str(page)}
        url = f"{LAYER_URL}/query?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 (fixed https URL)
            d = json.load(r)
        got = d.get("features", [])
        for f in got:  # keep only the allowed fields, whatever the server returns
            f["properties"] = {k: v for k, v in (f.get("properties") or {}).items()
                               if k in ALLOWED_PROPS}
        features += got
        if len(got) < page and not d.get("exceededTransferLimit"):
            break
        offset += len(got)
    with urllib.request.urlopen(f"{LAYER_URL}?f=json", timeout=60) as r:  # noqa: S310
        meta = json.load(r)
    out = data_dir / CACHE_NAME
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    edit = (meta.get("editingInfo") or {}).get("dataLastEditDate")
    prov = {
        "source": "Municipality of Jasper, 'Damage Assessment Structures' (ArcGIS Online, "
                  "owner MOJ_GIS), layer 0",
        "url": LAYER_URL,
        "query": {"where": WHERE, "outFields": OUT_FIELDS, "outSR": 4326, "f": "geojson"},
        "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "features": len(features),
        "layer_data_last_edit_utc": (dt.datetime.fromtimestamp(edit / 1000, dt.timezone.utc)
                                     .isoformat() if edit else None),
        "licence": "none stated on the item or service; local research use only; not "
                   "redistributed; never commit",
        "fields_dropped": "all except OBJECTID and Status (no street/address/owner fields)",
    }
    (data_dir / "provenance.json").write_text(json.dumps(prov, indent=2))
    return out


def load_features(path: Path):
    import shapely
    from shapely.geometry import shape

    d = json.loads(path.read_text())
    geoms, status, oid = [], [], []
    for f in d["features"]:
        assert set(f["properties"]) <= ALLOWED_PROPS, "unexpected field in the cache"
        g = shape(f["geometry"])
        if not g.is_valid:
            g = shapely.make_valid(g)
            if g.geom_type == "GeometryCollection":
                g = shapely.union_all([p for p in g.geoms if p.area > 0])
        geoms.append(g)
        status.append((f["properties"].get("Status") or "").strip())
        oid.append(f["properties"].get("OBJECTID"))
    return np.asarray(geoms, dtype=object), np.asarray(status), np.asarray(oid)


# ---------------------------------------------------------------------------------- model


def seeds_for(units, spec: dict, wind_from_deg: float) -> np.ndarray:
    """Boolean mask of the seed units for an ignition-set spec."""
    import shapely

    n = len(units)
    seed = np.zeros(n, dtype=bool)
    if "windward_fraction" in spec:
        to = math.radians(wind_from_deg + 180.0)
        proj = units.x * math.sin(to) + units.y * math.cos(to)
        k = int(math.ceil(spec["windward_fraction"] * n))
        seed[np.argsort(proj, kind="stable")[:k]] = True
        return seed
    for name in spec["areas"]:
        lat, lng = PREREG["seed_areas"][name]
        x, y = units.frame.to_local(lat, lng)
        d = shapely.distance(units.footprints, shapely.Point(float(x), float(y)))
        seed[np.argsort(d, kind="stable")[:spec["nearest_per_area"]]] = True
    return seed


def run_hamada(units, seed, wind_kmh, wind_from, window, fb, burnout_min=None):
    """Hamada end state; ``burnout_min=None`` is the published model (PREREG, no burn-out)."""
    t_front = np.where(seed, PREREG["ignition_time_min"], np.inf)
    res = hamada_spread(units, t_front, [WindPeriod(0.0, wind_kmh, wind_from)],
                        duration_min=window, fb=fb, burnout_min=burnout_min)
    return res.t_min


# ---------------------------------------------------------------------------------- scoring


def score(pred, obs, mask=None) -> dict:
    pred, obs = np.asarray(pred, bool), np.asarray(obs, bool)
    if mask is not None:
        pred, obs = pred[mask], obs[mask]
    tp = int(np.sum(pred & obs))
    fp = int(np.sum(pred & ~obs))
    fn = int(np.sum(~pred & obs))
    tn = int(np.sum(~pred & ~obs))
    n = tp + fp + fn + tn
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else float("nan")
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    return {"n": n, "TP": tp, "FP": fp, "FN": fn, "TN": tn, "predicted": tp + fp,
            "observed": tp + fn, "precision": prec, "recall": rec, "F1": f1,
            "accuracy": po, "kappa": kappa}


def kappa_of(pred, obs):
    return score(pred, obs)["kappa"]


def edge_distance_to_seeds(units, seed) -> np.ndarray:
    import shapely

    target = shapely.union_all(units.footprints[seed])
    return shapely.distance(units.footprints, target)


def percolate(units, seed, max_gap_m) -> np.ndarray:
    on = seed.copy()
    q = deque(np.nonzero(seed)[0].tolist())
    while q:
        i = q.popleft()
        nb, sep = units.neighbours(i)
        for j in nb[sep <= max_gap_m]:
            if not on[j]:
                on[j] = True
                q.append(int(j))
    return on


def baselines(units, seed, obs, n_firesim, dist, veg_scores: dict | None = None) -> dict:
    """Pre-registered baselines; ``veg_scores`` (name -> out-of-fold scores from
    ``distance_vegetation_scores``) adds the step-3 "distance + vegetation" baselines, each
    count-matched to FireSim (top ``n_firesim`` scores)."""
    order = np.lexsort((np.arange(len(units)), dist))
    out = {}
    for name, sc in (veg_scores or {}).items():
        out[name] = top_n(sc, n_firesim)
    a = np.zeros(len(units), bool)
    a[order[:n_firesim]] = True
    out["a_distance_band_count_matched"] = a
    a2 = np.zeros(len(units), bool)
    a2[order[:int(obs.sum())]] = True
    out["a_obs_distance_band_observed_count"] = a2
    out["b1_separation_5m_one_step"] = seed | (dist <= 5.0)
    out["b2_separation_5m_percolation"] = percolate(units, seed, 5.0)
    out["d_seeds_only"] = seed.copy()
    return out


def _logistic_fit(X: np.ndarray, y: np.ndarray, ridge: float = 1e-6, iters: int = 50) -> np.ndarray:
    """Unpenalised (tiny ridge for stability) logistic regression by IRLS; X has a 1s column."""
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ w, -30, 30)))
        W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + ridge * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - p) - ridge * w)
        w += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def distance_vegetation_scores(units, dist, veg_cols: np.ndarray, obs, *, block_m: float = 250.0,
                               folds: int = 10, repeats: int = 10, seed: int = 20261010) -> np.ndarray:
    """Out-of-fold probability of loss from log(1 + distance to the seeds) + vegetation columns.

    Structure step 3 baseline (spec §8 item 4; step-3 data report §5.2): a logistic regression
    fitted on the observed outcome but scored only out of fold, with spatial blocks of
    ``block_m`` assigned at random to ``folds`` folds, averaged over ``repeats`` assignments
    (fixed ``seed``). Features standardised on each training fold; missing vegetation values
    take the training-fold median. It uses the outcome, so it is a deliberately strong
    baseline: a spread model is worth showing only if it beats it."""
    obs = np.asarray(obs, float)
    feats = np.column_stack([np.log1p(np.asarray(dist, float)),
                             np.asarray(veg_cols, float).reshape(len(dist), -1)])
    bx = np.floor(units.x / block_m).astype(int)
    by = np.floor(units.y / block_m).astype(int)
    _, block = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    block = block.ravel()
    nb = int(block.max()) + 1
    rng = np.random.default_rng(seed)
    out = np.zeros(len(obs))
    for _ in range(repeats):
        fold_of_block = rng.permutation(np.arange(nb) % folds)
        fold = fold_of_block[block]
        for k in range(folds):
            te, tr = fold == k, fold != k
            if not te.any() or len(np.unique(obs[tr])) < 2:
                out[te] += obs[tr].mean() if tr.any() else 0.5
                continue
            Xtr, Xte = feats[tr].copy(), feats[te].copy()
            med = np.nanmedian(Xtr, axis=0)
            med = np.where(np.isfinite(med), med, 0.0)
            for c in range(feats.shape[1]):
                Xtr[~np.isfinite(Xtr[:, c]), c] = med[c]
                Xte[~np.isfinite(Xte[:, c]), c] = med[c]
            mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
            sd = np.where(sd > 0, sd, 1.0)
            Xtr, Xte = (Xtr - mu) / sd, (Xte - mu) / sd
            w = _logistic_fit(np.column_stack([np.ones(tr.sum()), Xtr]), obs[tr])
            out[te] += 1.0 / (1.0 + np.exp(-np.clip(np.column_stack([np.ones(te.sum()), Xte]) @ w,
                                                     -30, 30)))
    return out / repeats


def top_n(scores, n: int) -> np.ndarray:
    """The ``n`` highest scores (ties by index) as a boolean prediction."""
    order = np.lexsort((np.arange(len(scores)), -np.asarray(scores, float)))
    pred = np.zeros(len(scores), dtype=bool)
    pred[order[:max(int(n), 0)]] = True
    return pred


def random_baseline(obs, mask=None) -> dict:
    rng = np.random.default_rng(PREREG["random_seed"])
    o = obs if mask is None else obs[mask]
    p = o.mean()
    keys = ["precision", "recall", "F1", "kappa", "predicted"]
    vals = {k: [] for k in keys}
    for _ in range(PREREG["random_draws"]):
        s = score(rng.random(len(o)) < p, o)
        for k in keys:
            vals[k].append(s[k])
    return {k: {"mean": float(np.nanmean(v)), "p2.5": float(np.nanpercentile(v, 2.5)),
                "p97.5": float(np.nanpercentile(v, 97.5))} for k, v in vals.items()}


def block_bootstrap_kappa_diff(units, pred_a, pred_b, obs) -> dict:
    cfg = PREREG["kappa_difference_ci"]
    bx = np.floor(units.x / cfg["block_m"]).astype(int)
    by = np.floor(units.y / cfg["block_m"]).astype(int)
    _, block = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    block = block.ravel()
    nb = block.max() + 1
    members = [np.nonzero(block == b)[0] for b in range(nb)]
    rng = np.random.default_rng(cfg["seed"])
    diffs = []
    for _ in range(cfg["resamples"]):
        idx = np.concatenate([members[b] for b in rng.integers(0, nb, nb)])
        diffs.append(kappa_of(pred_a[idx], obs[idx]) - kappa_of(pred_b[idx], obs[idx]))
    diffs = np.asarray(diffs)
    return {"blocks": int(nb), "point": kappa_of(pred_a, obs) - kappa_of(pred_b, obs),
            "p2.5": float(np.nanpercentile(diffs, 2.5)),
            "p97.5": float(np.nanpercentile(diffs, 97.5))}


def graph_stats(units) -> dict:
    out = {"units": len(units),
           "median_nearest_separation_m": float(np.median(units.nearest_separation_m)),
           "share_no_neighbour_within_cutoff": float(np.mean(~np.isfinite(units.nearest_separation_m))),
           "median_area_m2": float(np.median(units.area_m2))}
    for gap in (5.0, 20.0, 30.0, 45.0):
        if gap > units.neighbour_cutoff_m:
            continue
        lab = np.full(len(units), -1)
        c = 0
        for s in range(len(units)):
            if lab[s] >= 0:
                continue
            lab[percolate(units, np.eye(1, len(units), s, dtype=bool)[0], gap) & (lab < 0)] = c
            c += 1
        sizes = np.bincount(lab)
        out[f"components_at_{gap:g}m"] = int(c)
        out[f"largest_component_share_at_{gap:g}m"] = float(sizes.max() / len(units))
    return out


# ---------------------------------------------------------------------------------- driver


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--refresh", action="store_true", help="re-download the layer")
    ap.add_argument("--features", type=Path, default=None,
                    help="use this GeoJSON instead of the cache (testing)")
    ap.add_argument("--no-maps", action="store_true")
    ap.add_argument("--burnout", action="store_true",
                    help="run the burn-out pre-registration (PREREG_BURNOUT)")
    ap.add_argument("--embers", action="store_true",
                    help="run the ember-ignition pre-registration (PREREG_EMBERS)")
    args = ap.parse_args()
    if args.burnout:
        return main_burnout(args)
    if args.embers:
        return main_embers(args)
    out_dir = args.out or (args.data_dir / "results")
    if REPO in out_dir.resolve().parents or REPO in args.data_dir.resolve().parents:
        sys.exit("refusing to write Jasper data inside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)

    path = args.features or args.data_dir / CACHE_NAME
    if args.features is None and (args.refresh or not path.exists()):
        path = fetch_layer(args.data_dir)
    geoms, status, _oid = load_features(path)
    status_counts = {str(s) or "(blank)": int(np.sum(status == s)) for s in sorted(set(status))}
    assessed = np.isin(status, ["Destroyed", "Visible Damage", "No Visible Damage"])

    P = PREREG
    cache_units = {}

    def units_at(cutoff):
        if cutoff not in cache_units:
            u = build_units(geoms[assessed], neighbour_cutoff_m=cutoff)
            assert len(u) == int(assessed.sum()), "a footprint was dropped by build_units"
            cache_units[cutoff] = u
        return cache_units[cutoff]

    st = status[assessed]
    outcomes = {"destroyed": st == "Destroyed",
                "destroyed_or_damaged": np.isin(st, ["Destroyed", "Visible Damage"])}
    u30 = units_at(P["neighbour_cutoff_m"])

    def config(name, **kw):
        c = {"name": name, "ignition_set": "I0_primary", "cutoff": P["neighbour_cutoff_m"],
             "wind_kmh": P["wind_speed_kmh"], "wind_from": P["wind_from_deg"],
             "window": P["window_min"], "fb": P["combustible_fraction"]}
        c.update(kw)
        return c

    S = P["sensitivity"]
    configs = [config("primary")]
    configs += [config(f"cutoff_{c:g}m", cutoff=c) for c in S["neighbour_cutoff_m"]]
    configs += [config(f"wind_x{f:g}", wind_kmh=P["wind_speed_kmh"] * f) for f in S["wind_factor"]]
    configs += [config(f"wind_from_{d:g}", wind_from=d) for d in S["wind_from_deg"]]
    configs += [config(f"window_{w:g}min", window=w) for w in S["window_min"]]
    configs += [config(f"ignition_{s}", ignition_set=s) for s in S["ignition_sets"]]

    results = {"prereg": P, "status_counts_townsite": status_counts,
               "units_scored": int(assessed.sum()), "graph": graph_stats(u30), "runs": []}
    keep_for_maps = {}
    for c in configs:
        u = units_at(c["cutoff"])
        seed = seeds_for(u, P["ignition_sets"][c["ignition_set"]], c["wind_from"])
        t = run_hamada(u, seed, c["wind_kmh"], c["wind_from"], c["window"], c["fb"])
        pred = t <= c["window"]
        dist = edge_distance_to_seeds(u, seed)
        run = {"config": c, "seeds": int(seed.sum()),
               "involved_by_min": {str(m): int(np.sum(t <= m))
                                   for m in (30, 50, 80, 120, 240, 360, 720) if m <= c["window"]},
               "scores": {}}
        for oname, obs in outcomes.items():
            if oname != "destroyed" and c["name"] != "primary":
                continue
            for excl in (False, True):
                if excl and not S["score_excluding_seeds"]:
                    continue
                mask = ~seed if excl else None
                key = f"{oname}{'_excl_seeds' if excl else ''}"
                bl = baselines(u, seed, obs, int(pred.sum()), dist)
                run["scores"][key] = {"firesim": score(pred, obs, mask),
                                      **{k: score(v, obs, mask) for k, v in bl.items()},
                                      "c_random_observed_rate": random_baseline(obs, mask)}
                if key == "destroyed":
                    run["kappa_diff_vs_a"] = block_bootstrap_kappa_diff(
                        u, pred, bl["a_distance_band_count_matched"], obs)
                    run["kappa_diff_vs_d"] = block_bootstrap_kappa_diff(
                        u, pred, bl["d_seeds_only"], obs)
        if c["name"] == "primary":
            obs = outcomes["destroyed"]
            bands = P["distance_bands_m"]
            rows = []
            for lo, hi in zip(bands[:-1], bands[1:]):
                m = (dist >= lo) & (dist < hi)
                s = score(pred[m], obs[m]) if m.any() else None
                rows.append({"band_m": [lo, hi if hi < 1e8 else None], "units": int(m.sum()),
                             "observed_destroyed": int(obs[m].sum()),
                             "predicted": int(pred[m].sum()),
                             **({k: s[k] for k in ("TP", "FP", "FN", "TN")} if s else {})})
            run["by_distance_band"] = rows
            keep_for_maps["primary"] = (u, seed, pred, obs)
            keep_for_maps["band_a"] = (u, seed, baselines(u, seed, obs, int(pred.sum()), dist)[
                "a_distance_band_count_matched"], obs)
        results["runs"].append(run)
        print(f"{c['name']:28s} seeds={int(seed.sum()):3d} pred={int(pred.sum()):4d} "
              f"kappa={run['scores']['destroyed']['firesim']['kappa']:+.3f}", flush=True)

    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=float))
    (out_dir / "tables.md").write_text(tables_md(results))
    if not args.no_maps:
        for name, (u, seed, pred, obs) in keep_for_maps.items():
            draw_map(u, seed, pred, obs, out_dir / f"map_{name}.png", name)
    print(f"wrote {out_dir}")


def _f(v, pct=False):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{100 * v:.1f} %" if pct else (f"{v:+.3f}" if isinstance(v, float) else str(v))


def tables_md(r: dict) -> str:
    L = ["# Jasper 2024 structure validation: tables", "",
         f"Status counts (townsite): {r['status_counts_townsite']}; units scored {r['units_scored']}",
         f"Graph (30 m cutoff): {r['graph']}", ""]
    head = "| Model | Pred | TP | FP | FN | TN | Precision | Recall | F1 | κ |"
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    for run in r["runs"]:
        c = run["config"]
        L += [f"## {c['name']} (seeds {run['seeds']}; involved by minute {run['involved_by_min']})",
              ""]
        for key, sc in run["scores"].items():
            L += [f"### outcome: {key}", "", head, sep]
            for m, s in sc.items():
                if m == "c_random_observed_rate":
                    L.append(f"| {m} (mean of {PREREG['random_draws']}) | {s['predicted']['mean']:.0f}"
                             f" | | | | | {_f(s['precision']['mean'], True)} | {_f(s['recall']['mean'], True)}"
                             f" | {_f(s['F1']['mean'], True)} | {_f(s['kappa']['mean'])} "
                             f"({_f(s['kappa']['p2.5'])} to {_f(s['kappa']['p97.5'])}) |")
                else:
                    L.append(f"| {m} | {s['predicted']} | {s['TP']} | {s['FP']} | {s['FN']} | {s['TN']}"
                             f" | {_f(s['precision'], True)} | {_f(s['recall'], True)} | {_f(s['F1'], True)}"
                             f" | {_f(s['kappa'])} |")
            L.append("")
        for k in ("kappa_diff_vs_a", "kappa_diff_vs_d"):
            if k in run:
                d = run[k]
                L.append(f"- {k}: {d['point']:+.3f} (block bootstrap 95 %: {d['p2.5']:+.3f} to "
                         f"{d['p97.5']:+.3f}; {d['blocks']} blocks)")
        if "by_distance_band" in run:
            L += ["", "| Distance to nearest seed (m) | Units | Observed destroyed | Predicted | TP | FP | FN | TN |",
                  "|---|---|---|---|---|---|---|---|"]
            for b in run["by_distance_band"]:
                lo, hi = b["band_m"]
                L.append(f"| {lo:g}-{hi:g} | " if hi else f"| ≥ {lo:g} | ")
                L[-1] += (f"{b['units']} | {b['observed_destroyed']} | {b['predicted']} | {b.get('TP', '')}"
                          f" | {b.get('FP', '')} | {b.get('FN', '')} | {b.get('TN', '')} |")
        L.append("")
    return "\n".join(L)


def draw_map(units, seed, pred, obs, path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    colours = {"TP": "#b2182b", "FP": "#ef8a62", "FN": "#2166ac", "TN": "#d9d9d9"}
    cls = np.where(pred & obs, "TP", np.where(pred & ~obs, "FP", np.where(~pred & obs, "FN", "TN")))
    fig, ax = plt.subplots(figsize=(9, 9), dpi=150)
    for g, k, s in zip(units.footprints, cls, seed):
        polys = getattr(g, "geoms", [g])
        for p in polys:
            xs, ys = p.exterior.xy
            ax.fill(xs, ys, color=colours[k], lw=0)
            if s:
                ax.plot(xs, ys, color="black", lw=0.8)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Jasper 2024, {title}: TP/FP/FN/TN (seeds outlined). Illustrative, not validated.",
                 fontsize=8)
    ax.legend(handles=[Patch(color=v, label=k) for k, v in colours.items()], loc="lower right")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)



# ---------------------------------------------------------------------------------- embers


def main_embers(args) -> None:
    """PREREG_EMBERS: Hamada + ember ignition against the same observed end state."""
    out_dir = args.out or (args.data_dir / "results-embers")
    if REPO in out_dir.resolve().parents or REPO in args.data_dir.resolve().parents:
        sys.exit("refusing to write Jasper data inside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = args.features or args.data_dir / CACHE_NAME
    if args.features is None and (args.refresh or not path.exists()):
        path = fetch_layer(args.data_dir)
    geoms, status, _oid = load_features(path)
    assessed = np.isin(status, ["Destroyed", "Visible Damage", "No Visible Damage"])
    obs = status[assessed] == "Destroyed"
    P, PE = PREREG, PREREG_EMBERS
    cache = {}

    def units_at(cutoff):
        if cutoff not in cache:
            u = build_units(geoms[assessed], neighbour_cutoff_m=cutoff)
            assert len(u) == int(assessed.sum())
            cache[cutoff] = u
        return cache[cutoff]

    base = dict(cutoff=P["neighbour_cutoff_m"], wind=P["wind_speed_kmh"], dfire=150, gr=10.0,
                hamada=True, embers=True)
    runs = {
        "H0_hamada_only": dict(base, embers=False),
        "E0_primary": dict(base),
        "E1_gr_5.68": dict(base, gr=5.68),
        "E2_design_fire_400": dict(base, dfire=400),
        "H3_hamada_cutoff_20": dict(base, cutoff=20.0, embers=False),
        "E3_cutoff_20": dict(base, cutoff=20.0),
        "H4_hamada_cutoff_45": dict(base, cutoff=45.0, embers=False),
        "E4_cutoff_45": dict(base, cutoff=45.0),
        "E5_embers_only": dict(base, hamada=False),
        "E6_embers_only_400": dict(base, hamada=False, dfire=400),
        "H7_hamada_wind_x1.25": dict(base, wind=P["wind_speed_kmh"] * 1.25, embers=False),
        "E7_wind_x1.25": dict(base, wind=P["wind_speed_kmh"] * 1.25),
        "H8_hamada_wind_x0.75": dict(base, wind=P["wind_speed_kmh"] * 0.75, embers=False),
        "E8_wind_x0.75": dict(base, wind=P["wind_speed_kmh"] * 0.75),
        "H9_hamada_gust_27": dict(base, wind=27.0, embers=False),
        "E9_gust_27": dict(base, wind=27.0),
        "E10_gust_27_400": dict(base, wind=27.0, dfire=400),
    }
    results = {"prereg": P, "prereg_embers": PE, "units_scored": int(assessed.sum()), "runs": {}}
    preds = {}
    for name, c in runs.items():
        u = units_at(c["cutoff"])
        seed = seeds_for(u, P["ignition_sets"]["I0_primary"], P["wind_from_deg"])
        if c["embers"]:
            opts = EmberOptions(design_fire_kw_m2=c["dfire"], gr_structure=c["gr"],
                                from_wildland=False, hamada=c["hamada"])
            res = run_embers(u, seed, c["wind"], P["wind_from_deg"], P["window_min"],
                             P["combustible_fraction"], opts)
        else:
            res = hamada_spread(u, np.where(seed, 0.0, np.inf),
                                [WindPeriod(0.0, c["wind"], P["wind_from_deg"])],
                                duration_min=P["window_min"], fb=P["combustible_fraction"])
        t = res.t_min
        pred = t <= P["window_min"]
        preds[name] = (u, seed, pred)
        dist = edge_distance_to_seeds(u, seed)
        bl = baselines(u, seed, obs, int(pred.sum()), dist)
        ember = pred & (res.source == SOURCE_EMBER)
        bands = P["distance_bands_m"]
        run = {"config": c, "seeds": int(seed.sum()), "predicted": int(pred.sum()),
               "ember_units": int(ember.sum()),
               "ember_units_destroyed": int(np.sum(ember & obs)),
               "ember_by_distance_band": [
                   {"band_m": [lo, hi if hi < 1e8 else None],
                    "ember_units": int(np.sum(ember & (dist >= lo) & (dist < hi))),
                    "ember_destroyed": int(np.sum(ember & obs & (dist >= lo) & (dist < hi)))}
                   for lo, hi in zip(bands[:-1], bands[1:])],
               "involved_by_min": {str(m): int(np.sum(t <= m)) for m in (30, 60, 120, 240, 360)},
               "scores": {"firesim": score(pred, obs),
                          **{k: score(v, obs) for k, v in bl.items()}},
               "kappa_diff_vs_a": block_bootstrap_kappa_diff(
                   u, pred, bl["a_distance_band_count_matched"], obs),
               "kappa_diff_vs_d": block_bootstrap_kappa_diff(u, pred, bl["d_seeds_only"], obs)}
        if name.startswith("E") and c["hamada"]:
            ref = {30.0: "H0_hamada_only", 20.0: "H3_hamada_cutoff_20",
                   45.0: "H4_hamada_cutoff_45"}[c["cutoff"]]
            if c["wind"] != P["wind_speed_kmh"]:
                ref = {P["wind_speed_kmh"] * 1.25: "H7_hamada_wind_x1.25",
                       P["wind_speed_kmh"] * 0.75: "H8_hamada_wind_x0.75",
                       27.0: "H9_hamada_gust_27"}[c["wind"]]
            hu, _, hpred = preds[ref]
            assert hu is u
            run["reference_hamada"] = ref
            run["kappa_diff_vs_hamada"] = block_bootstrap_kappa_diff(u, pred, hpred, obs)
            run["changed_vs_hamada"] = {"added": int(np.sum(pred & ~hpred)),
                                        "added_destroyed": int(np.sum(pred & ~hpred & obs)),
                                        "removed": int(np.sum(~pred & hpred))}
        results["runs"][name] = run
        f = run["scores"]["firesim"]
        print(f"{name:24s} pred={run['predicted']:4d} ember={run['ember_units']:3d} "
              f"kappa={f['kappa']:+.3f} P={f['precision']:.3f} R={f['recall']:.3f}", flush=True)
    (out_dir / "results_embers.json").write_text(json.dumps(results, indent=2, default=float))
    (out_dir / "tables_embers.md").write_text(tables_embers_md(results))
    if not args.no_maps:
        u, seed, pred = preds["E0_primary"]
        draw_map(u, seed, pred, obs, out_dir / "map_embers_primary.png", "Hamada + embers")
    print(f"wrote {out_dir}")


def tables_embers_md(r: dict) -> str:
    L = ["# Jasper 2024: ember ignition (PREREG_EMBERS)", "",
         "| Run | Pred | Ember units (destroyed) | TP | FP | FN | TN | Precision | Recall | F1 | κ |"
         " κ (a) | κ − (a) [95 %] | κ − Hamada [95 %] |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, run in r["runs"].items():
        s = run["scores"]["firesim"]
        a = run["scores"]["a_distance_band_count_matched"]
        da = run["kappa_diff_vs_a"]
        dh = run.get("kappa_diff_vs_hamada")
        dh_txt = f"{dh['point']:+.3f} [{dh['p2.5']:+.3f}, {dh['p97.5']:+.3f}]" if dh else "—"
        L.append(f"| {name} | {s['predicted']} | {run['ember_units']} ({run['ember_units_destroyed']})"
                 f" | {s['TP']} | {s['FP']} | {s['FN']} | {s['TN']} | {_f(s['precision'], True)}"
                 f" | {_f(s['recall'], True)} | {_f(s['F1'], True)} | {_f(s['kappa'])} | {_f(a['kappa'])}"
                 f" | {da['point']:+.3f} [{da['p2.5']:+.3f}, {da['p97.5']:+.3f}] | {dh_txt} |")
    L += ["", "## Ember-ignited units by distance band from the seeds", "",
          "| Run | " + " | ".join(f"{b['band_m'][0]:g}-{b['band_m'][1]:g} m" if b['band_m'][1] else
                                 f"≥ {b['band_m'][0]:g} m"
                                 for b in next(iter(r['runs'].values()))['ember_by_distance_band']) + " |",
          "|---|" + "---|" * len(r["prereg"]["distance_bands_m"][:-1])]
    for name, run in r["runs"].items():
        L.append(f"| {name} | " + " | ".join(f"{b['ember_units']} ({b['ember_destroyed']})"
                                            for b in run["ember_by_distance_band"]) + " |")
    L += ["", "Ember units (destroyed among them) per band.", ""]
    for name, run in r["runs"].items():
        if "changed_vs_hamada" in run:
            L.append(f"- {name} vs {run['reference_hamada']}: {run['changed_vs_hamada']}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------- burn-out


def sensitivity_configs() -> list[dict]:
    """The PREREG primary run and its 11 sensitivity runs (same list as ``main``)."""
    P, S = PREREG, PREREG["sensitivity"]

    def config(name, **kw):
        c = {"name": name, "ignition_set": "I0_primary", "cutoff": P["neighbour_cutoff_m"],
             "wind_kmh": P["wind_speed_kmh"], "wind_from": P["wind_from_deg"],
             "window": P["window_min"], "fb": P["combustible_fraction"]}
        c.update(kw)
        return c

    out = [config("primary")]
    out += [config(f"cutoff_{c:g}m", cutoff=c) for c in S["neighbour_cutoff_m"]]
    out += [config(f"wind_x{f:g}", wind_kmh=P["wind_speed_kmh"] * f) for f in S["wind_factor"]]
    out += [config(f"wind_from_{d:g}", wind_from=d) for d in S["wind_from_deg"]]
    out += [config(f"window_{w:g}min", window=w) for w in S["window_min"]]
    out += [config(f"ignition_{s}", ignition_set=s) for s in S["ignition_sets"]]
    return out


def main_burnout(args) -> None:
    """PREREG_BURNOUT: Hamada with building burn-out against the same observed end state."""
    out_dir = args.out or (args.data_dir / "results-burnout")
    if REPO in out_dir.resolve().parents or REPO in args.data_dir.resolve().parents:
        sys.exit("refusing to write Jasper data inside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = args.features or args.data_dir / CACHE_NAME
    if args.features is None and (args.refresh or not path.exists()):
        path = fetch_layer(args.data_dir)
    geoms, status, _oid = load_features(path)
    assessed = np.isin(status, ["Destroyed", "Visible Damage", "No Visible Damage"])
    obs = status[assessed] == "Destroyed"
    P, PB = PREREG, PREREG_BURNOUT
    d66, d70 = burnout_minutes(150), burnout_minutes(400)
    assert d66 == PB["burnout"]["D_primary_min"] and d70 == PB["burnout"]["D_scenario_min"]
    cache = {}

    def units_at(cutoff):
        if cutoff not in cache:
            u = build_units(geoms[assessed], neighbour_cutoff_m=cutoff)
            assert len(u) == int(assessed.sum())
            cache[cutoff] = u
        return cache[cutoff]

    def scored(u, seed, pred, ref_pred=None):
        dist = edge_distance_to_seeds(u, seed)
        bl = baselines(u, seed, obs, int(pred.sum()), dist)
        run = {"seeds": int(seed.sum()), "predicted": int(pred.sum()),
               "scores": {"firesim": score(pred, obs),
                          **{k: score(v, obs) for k, v in bl.items()}},
               "kappa_diff_vs_a": block_bootstrap_kappa_diff(
                   u, pred, bl["a_distance_band_count_matched"], obs)}
        if ref_pred is not None:
            run["kappa_diff_vs_no_burnout"] = block_bootstrap_kappa_diff(u, pred, ref_pred, obs)
            run["changed_vs_no_burnout"] = {
                "removed": int(np.sum(ref_pred & ~pred)),
                "removed_destroyed": int(np.sum(ref_pred & ~pred & obs)),
                "added": int(np.sum(pred & ~ref_pred))}
        return run

    results = {"prereg": P, "prereg_burnout": PB, "units_scored": int(assessed.sum()),
               "runs": {}}
    first = {}
    for c in sensitivity_configs():
        u = units_at(c["cutoff"])
        seed = seeds_for(u, P["ignition_sets"][c["ignition_set"]], c["wind_from"])
        t_h = run_hamada(u, seed, c["wind_kmh"], c["wind_from"], c["window"], c["fb"])
        t_b = run_hamada(u, seed, c["wind_kmh"], c["wind_from"], c["window"], c["fb"], d66)
        ph, pb = t_h <= c["window"], t_b <= c["window"]
        name = f"B_{c['name']}"
        results["runs"][f"H_{c['name']}"] = {"config": c, **scored(u, seed, ph)}
        results["runs"][name] = {"config": {**c, "burnout_min": d66}, **scored(u, seed, pb, ph)}
        if c["name"] == "primary":
            first = {"u": u, "seed": seed, "ph": ph, "pb": pb, "c": c}
        r = results["runs"][name]
        k_h = results["runs"]["H_" + c["name"]]["scores"]["firesim"]["kappa"]
        print(f"{name:32s} H={int(ph.sum()):4d} B={int(pb.sum()):4d} "
              f"kH={k_h:+.3f} "
              f"kB={r['scores']['firesim']['kappa']:+.3f} "
              f"k(a)={r['scores']['a_distance_band_count_matched']['kappa']:+.3f}", flush=True)
    # B0 with the 400 kW/m2 design fire's duration
    u, seed, ph, c = first["u"], first["seed"], first["ph"], first["c"]
    t70 = run_hamada(u, seed, c["wind_kmh"], c["wind_from"], c["window"], c["fb"], d70)
    results["runs"]["B_D70"] = {"config": {**c, "burnout_min": d70},
                                **scored(u, seed, t70 <= c["window"], ph)}
    # Embers (PREREG_EMBERS E0 values) with and without burn-out
    opts = EmberOptions(design_fire_kw_m2=150, gr_structure=10.0, from_wildland=False)
    t_front = np.where(seed, P["ignition_time_min"], np.inf)
    wind = [WindPeriod(0.0, c["wind_kmh"], c["wind_from"])]
    e0 = spread_with_embers(u, t_front, wind, duration_min=c["window"], fb=c["fb"], embers=opts)
    be0 = spread_with_embers(u, t_front, wind, duration_min=c["window"], fb=c["fb"],
                             embers=opts, burnout_min=d66)
    pe0, pbe0 = e0.t_min <= c["window"], be0.t_min <= c["window"]
    results["runs"]["E0_embers_no_burnout"] = {"config": {**c, "embers": True},
                                               "ember_units": int(np.sum(pe0 & (e0.source == SOURCE_EMBER))),
                                               **scored(u, seed, pe0)}
    results["runs"]["BE0_embers"] = {"config": {**c, "embers": True, "burnout_min": d66},
                                     "ember_units": int(np.sum(pbe0 & (be0.source == SOURCE_EMBER))),
                                     **scored(u, seed, pbe0, pe0)}
    for k in ("B_D70", "E0_embers_no_burnout", "BE0_embers"):
        r = results["runs"][k]
        print(f"{k:32s} pred={r['predicted']:4d} kappa={r['scores']['firesim']['kappa']:+.3f}",
              flush=True)
    (out_dir / "results_burnout.json").write_text(json.dumps(results, indent=2, default=float))
    (out_dir / "tables_burnout.md").write_text(tables_burnout_md(results))
    if not args.no_maps:
        draw_map(u, seed, first["pb"], obs, out_dir / "map_burnout_primary.png", "Hamada + burn-out")
    print(f"wrote {out_dir}")


def tables_burnout_md(r: dict) -> str:
    L = ["# Jasper 2024: building burn-out (PREREG_BURNOUT)", "",
         "| Run | Burn-out (min) | Pred | TP | FP | FN | TN | Precision | Recall | F1 | κ | κ (a) |"
         " κ − (a) [95 %] | κ − no burn-out [95 %] | Removed (destroyed) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, run in r["runs"].items():
        s = run["scores"]["firesim"]
        a = run["scores"]["a_distance_band_count_matched"]
        da = run["kappa_diff_vs_a"]
        dh = run.get("kappa_diff_vs_no_burnout")
        dh_txt = f"{dh['point']:+.3f} [{dh['p2.5']:+.3f}, {dh['p97.5']:+.3f}]" if dh else "—"
        ch = run.get("changed_vs_no_burnout")
        ch_txt = f"{ch['removed']} ({ch['removed_destroyed']})" if ch else "—"
        bo = run["config"].get("burnout_min")
        L.append(f"| {name} | {bo if bo else '—'} | {s['predicted']} | {s['TP']} | {s['FP']} | "
                 f"{s['FN']} | {s['TN']} | {_f(s['precision'], True)} | {_f(s['recall'], True)} | "
                 f"{_f(s['F1'], True)} | {_f(s['kappa'])} | {_f(a['kappa'])} | "
                 f"{da['point']:+.3f} [{da['p2.5']:+.3f}, {da['p97.5']:+.3f}] | {dh_txt} | {ch_txt} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
