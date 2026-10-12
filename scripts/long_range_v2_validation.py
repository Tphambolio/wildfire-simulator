#!/usr/bin/env python3
"""Long-range embers v2 (urban ember source): pre-registered building-level test.

Specification: ``docs/structure-spread-spec.md`` §6.4; engine ``firesim.structures.long_range``
(``model="v2"``). **Illustrative — not validated.** ``PREREG_LR2`` below fixes every value
**before any outcome of the primary fire is read** (commit and push precede the first scored
run; do not edit after: add a dated block and report both).

Primary (decides): Palisades 2025 (CAL FIRE DINS), never used before by FireSim; its data were
prepared blind to outcomes (``~/dev/wildfire/validation-data/calfire-dins/refs/palisades/
DATA_QUALITY.md``). Eaton 2025 and Camp 2018 are re-scored as **disclosed, non-independent
checks**: their v1 results (and Eaton's loss-vs-distance pattern) were seen before v2 was built.

The wildland fire is imposed (it is the ember source), as in ``long_range_ember_validation.py``
(PREREG_LR), whose helpers are reused unchanged: final-perimeter vegetated building-free
patches, a documented-progression burn-time rule, an FBP proxy fuel.

Data stay outside the repository; outputs (aggregate JSON + Markdown, plus a per-unit NumPy
archive of local-metre coordinates, probabilities and outcomes for the block-aggregated figures;
no address, no identifier beyond the unit index) go to ``--out`` outside the repo.

Usage (repo root):
    PYTHONPATH=engine/src python3 scripts/long_range_v2_validation.py run --fire palisades \\
        --out ~/dev/wildfire/validation-data/calfire-dins/results-long-range-v2-2026-10-11 [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import jasper_structure_validation as J  # noqa: E402
import long_range_ember_validation as L1  # noqa: E402
import structure_step3_validation as S3  # noqa: E402
from firesim.fbp.calculator import calculate_fbp, calculate_foliar_moisture  # noqa: E402
from firesim.fwi.calculator import hourly_ffmc  # noqa: E402
from firesim.structures import long_range as LR  # noqa: E402
from firesim.structures.spread import WindPeriod, burnout_minutes, hamada_spread  # noqa: E402
from firesim.structures.units import build_units  # noqa: E402

CALFIRE_DIR = L1.CALFIRE_DIR
SEED = 20261012  # parameter draws and ignition thresholds; winds keep PREREG_LR's seeds

# --------------------------------------------------------------------------------------------
# Pre-registration (fixed 2026-10-11 before any Palisades outcome was read).
# --------------------------------------------------------------------------------------------
PREREG_LR2 = {
    "date_fixed": "2026-10-11",
    "question": "Does the v2 long-range ember model (wildland embers plus embers from burning "
                "buildings, iterated every 10 min so that ember-ignited buildings become "
                "sources; per-ember ignition of a receptive near-home fuel bed from firebrand-"
                "shower tests; published parameter ranges drawn per member / building) give better calibrated per-building "
                "destruction probabilities on an independent fire (Palisades 2025) than the Chen "
                "& McAneney distance-decay rule, and better kappa than windward-edge seeding and a "
                "count-matched distance band?",
    "model_v2": {
        "wildland_generation": "n_kg per kg consumed from torching/crowning cells (f_s = 0), n_kg "
                               "drawn per member U(250, 640) /kg (Qin 2025 p.59 low yield; "
                               "Thompson et al. 2022 p.1630) [H: uniform over published values]",
        "wildland_transport": "unchanged from v1: Sardoy lognormal, Albini 2012 / 1979 cap, "
                              "sigma_y 0.92 x cell, FFT landing field, 10 min bins",
        "urban_generation": "GR' = 10 embers per MJ released (Qin 2025 Table 6.1 p.153) x the "
                            "150 kW/m2 design fire (PROCI24 p.3) from each involved unit until "
                            "burn-out (66 min)",
        "urban_transport": "Himoto & Tanaka 2008 eqs 38-40 lognormal from the downwind edge "
                           "(D/2 from the centroid), 10 m wind as spec C17, crosswind sigma_Y = "
                           "0.92 D, untruncated to the 99.9th percentile [H]; 4 building-size "
                           "classes (quartiles of D), kernel at the emission-weighted HRR; 10 m "
                           "raster [H]",
        "ignition": "receptive-bed formulation [D/H]: each building has one receptive near-home "
                    "fuel bed of the tested size, A_bed = 1.44 m2 (Suzuki, Manzello et al. 2015 "
                    "Fire Technol. mulch bed); embers landing on it = landing density at the "
                    "building x A_bed; each ignites the building with q = m_f p_bed p_b: p_bed = "
                    "per-brand bed ignition, 1 / brands to first sustained flaming under "
                    "firebrand showers, log-uniform per building over 2e-4 to 7e-3 (mulch "
                    "980-3,800, pine bark 1,508-2,624, decking 140-5,700 brands of 0.05 g; dry "
                    "mulch 7.36 /m2/s x 1.44 m2 x 172 s = 1,823) [D]; m_f = 4 (0.2 g model ember "
                    "/ 0.05 g tested brand, mass-equivalent) [D]; p_b = bed fire -> building "
                    "fire, no published value, U(0.1, 1) per member [H]. First ignition when the "
                    "accumulated hazard crosses an Exp(1) draw; involvement 42 s + 300 s later "
                    "(FSJ104686 p.4)",
        "sanity_check_not_fitted": "Itoigawa 2016 (~10 spot ignitions per 147 burned "
                                   "buildings at 9 m/s; firebrand-structures review 2026-10-11): "
                                   "the run reports urban-ember ignitions per involved building; "
                                   "a value far outside 0.01-0.1 is disclosed, nothing is tuned",
        "coupling": "10 min steps; the Hamada structure stage (cutoff 30 m, fb 1, burn-out "
                    "66 min) is re-run after any new ember ignition so that Hamada-involved units "
                    "also emit",
        "what_changed_from_v1_and_why": {
            "urban_source": "v1 had embers only from the wildland edge; observed loss rose "
                            "with distance into town at Eaton (seen in v1): a structural "
                            "omission, not a tuned value",
            "receptive_area_and_probability": "v1 used footprint + 1.5 m ring (~250 m2) and a "
                                              "match-flame p_e (Beverly & Wotton, ~0.9) x 0.5 x "
                                              "0.1 = ~0.025 per ember and saturated inside the "
                                              "cap; v2 uses one tested-size receptive bed (1.44 "
                                              "m2) and the per-brand ignition measured under "
                                              "firebrand showers (~1e-3 x p_b), about 4,000x "
                                              "less hazard per unit landing density. An "
                                              "alternative published per-brand building "
                                              "parameterisation (Himoto & Tanaka 2008 alpha_B "
                                              "beta_B) is a sensitivity",
            "parameter_uncertainty": "v1 drew only the wind; v2 also draws p_b, n_kg (per "
                                     "member) and p_bed (per building: bed material differs "
                                     "between buildings), so P reflects that uncertainty "
                                     "uncertainty (over-confidence: P >= 0.9 was 58 % destroyed "
                                     "at Eaton in v1)",
            "distance_cap": "unchanged (Albini cap for wildland embers); v1's q99 / x2 "
                            "sensitivities remain",
        },
        "members": 50, "member_seeds": "wind: default_rng([20261011, 1000 + m]) (as PREREG_LR); "
                                       "parameter draw then thresholds: default_rng([20261012, m])",
    },
    "fires": {
        "palisades": {"role": "primary (decides); independent: never used by FireSim before; "
                              "data prepared blind to outcomes",
                      "config": "FIRES2['palisades'] (filled from refs/palisades before scoring)"},
        "eaton": {"role": "disclosed non-independent check (v1 results seen)",
                  "config": "PREREG_LR eaton set-up unchanged"},
        "camp": {"role": "disclosed non-independent check (v1 results seen)",
                 "config": "PREREG_LR camp set-up unchanged"},
    },
    "units": "domain units as PREREG_LR (DINS-matched pre-fire footprints; Camp: DINS squares); "
             "plus every Microsoft footprint within 1 km outside the DINS domain (Eaton, "
             "Palisades) simulated as buildings (they burn and emit) but never scored",
    "metrics": {
        "primary": "Brier score of P = share of members with the unit involved by the window "
                   "end; Brier skill score against (c) and against climatology (e)",
        "secondary": "log score (clipped [1/(2M), 1 - 1/(2M)]), AUC, reliability (10 bins), "
                     "kappa / precision / recall at P >= 0.5 and count-matched (top N, ties "
                     "broken by a seeded permutation, default_rng(20261012), fixing the v1 "
                     "index tie-break flaw), sum P vs observed, distance-band table",
        "bootstrap": "250 m blocks, 2,000 resamples, seed 20261011",
        "false_alarms_outside": "member-mean number of involved buildings among the footprints "
                                "within 1 km outside the DINS domain (unsurveyed; almost all "
                                "undamaged), and the member-mean number ember-ignited there",
    },
    "baselines": {
        "a_hamada": "Hamada H30 from documented seeds (PREREG_STEP3 I0 for Eaton/Camp); "
                    "Palisades: Hamada H30 from the imposed front contact only, at the "
                    "member-median wind",
        "b_windward_edge": "upwind 5 % of the domain units seeded + Hamada H30 (Eaton/Camp: "
                           "step-3 wind; Palisades: the wind range's midpoint)",
        "c_chen_mcaneney": "P = max(0, 0.6 (1 - d / 700 m)), d = edge distance as PREREG_LR",
        "d_distance_band": "count-matched: N smallest d (N = v2's count at P >= 0.5), ties "
                           "by seeded permutation",
        "e_climatology": "P = observed destroyed rate in the scored units",
    },
    "decision_rule": "v2 is 'worth showing' only if on PALISADES BSS(v2 vs c) > 0 AND "
                     "kappa(v2 at P >= 0.5) - kappa(b) > 0 AND kappa(v2) - kappa(d) > 0, each "
                     "with the 95 % block-bootstrap interval excluding zero. Eaton and Camp are "
                     "reported as disclosed checks and never decide. No configuration may be "
                     "promoted to primary after scoring.",
    "variants_50_members": {
        "V1": "the v1 chain (PREREG_LR primary) on the same units, for comparison",
    },
    "sensitivity_20_members": {
        "NO_URBAN": "v2 without the urban source",
        "FIXED": "no parameter draws (p_bed 5.5e-4 dry mulch, p_b 0.5, n_kg 500)",
        "P_B_0.1": "p_b fixed 0.1 (p_bed still drawn)",
        "P_B_1": "p_b fixed 1",
        "NO_MASS_FACTOR": "m_f = 1 (count-equivalent embers)",
        "HT08_ALPHA": "per ember landed on the footprint, alpha = alpha_B beta_B / beta_B with "
                      "alpha_B beta_B ~ U(0, 2e-6) per kJ (Himoto & Tanaka 2008 Table 3) and "
                      "beta_B = GR' = 0.01 per kJ, so alpha ~ U(0, 2e-4) per member",
        "HT08_ALPHA_1e-4": "as HT08_ALPHA, alpha fixed 1e-4",
        "STEP_5": "5 min coupling step",
        "HIMOTO_Q99": "urban kernel truncated at the 99th percentile (Qin 2025 eq 4.8)",
        "GR_5.68": "GR' 5.68 per MJ (Qin 2025 eq 3.17) for urban generation, q unchanged",
        "NO_HAMADA": "embers only (no Hamada links): spread by front contact and embers",
        "proxy_alt": "the fire's alternative proxy fuel",
        "fwi_dmc60_dc400": "DMC 60, DC 400",
    },
    "expectation_from_a_model_only_dry_run": "2026-10-11, 3 members each, random outcomes "
        "(status files not read): Eaton 37,690 units (13,545 domain + 24,145 ring), 4,404 ha "
        "source, 357 front-contact units, 5,400-6,900 ember-ignited per member (2,100-3,500 by "
        "urban embers, i.e. ~0.1-0.15 per involved building, at the top of the Itoigawa band), "
        "9,200-11,500 domain and 11,800-14,500 ring units involved: Hamada (30 m) percolates "
        "through the dense suburbs once seeded, so large false alarms outside the domain are "
        "expected. Palisades 15,692 units (10,322 + 5,370 ring), 7,755 ha source, 1,093 "
        "front-contact units, 3,000-3,700 ember-ignited, 9,300-9,800 domain and 1,300-1,800 "
        "ring involved. ~60-90 s per member with sensitivities, 12-17 s without; peak RSS "
        "1.5 GB (Eaton), 2.5 GB (Palisades). FFMC Eaton 92.1, Palisades 92.1 (Malibu Hills).",
    "disclosure": "Seen before fixing this block: every v1 result on Eaton and Camp (report "
                  "'Long-range ember build and Eaton test 2026-10-11'), including Eaton's loss "
                  "rising with distance into town and the v1 over-confidence; the step-3 "
                  "results. The v2 structure (urban source, HT08 alpha, parameter draws) was "
                  "chosen from that diagnosis and from the literature, with no value fitted to "
                  "Eaton, Camp or Palisades. Palisades outcomes were not read: its data agent "
                  "wrote the status file sealed and reported no outcome statistics; the "
                  "Palisades set-up (seeds, progression, proxy fuel, wind, FFMC) was fixed from "
                  "published reports and weather records only. Dry runs (--dry-run: random "
                  "outcomes, status file never read) are allowed for timing and memory.",
}

FIRES2 = {
    "eaton": dict(L1.FIRES["eaton"], ring=True, ffmc_raws=("SAN RAFAEL", "2025-01-08T03:00",
                                                           "2025-01-08T14:00")),
    "camp": dict(L1.FIRES["camp"], ring=False),
    # Palisades 2025: set-up fixed from refs/palisades (LAFD AAR 2025-10-08, McChrystal AAR
    # App. 12 Fig. 40 FireGuard panels, FIRIS perimeters as summarised in DATA_QUALITY.md, RAWS)
    # before any outcome was read. Same rules as PREREG_LR's Eaton set-up wherever possible.
    "palisades": {
        "perimeter": "raw/frap_perimeter_palisades.geojson",
        "footprints": "derived/ms_usv2_palisades.geojsonl",
        "worldcover": "raw/worldcover2021_palisades.tif", "chm": "raw/meta_chm_palisades.tif",
        "h": 20.0,
        # 10:30 PST 7 Jan (ignition, CAL FIRE incident API) to 06:00 PST 8 Jan = 0-1170 min
        "window": 1170.0, "origin": (34.070, -118.545), "t0": 0.0,
        # progression rule [H]: burn time piecewise linear in the east or west distance from
        # the origin through documented front positions (minutes after 10:30 PST); beyond the
        # last anchor at the last segment's rate
        "anchors_lng_w": [(-118.552, 51.0),    # 11:21 spotting at Sunset/Palisades Dr (AAR p.26)
                          (-118.560, 82.0),    # 11:52 W flank into Castellammare (AAR p.30)
                          (-118.588, 399.0),   # FIRIS 2,920 ac perimeter, 16:01-18:17 midpoint (AAR p.35)
                          (-118.600, 540.0),   # FireGuard 18:30-20:30 W front (McChrystal p.125)
                          (-118.635, 660.0),   # FireGuard 20:30-22:30 coastal band
                          (-118.651, 1140.0)],  # FireGuard 04:30-06:30 W front
        "anchors_lng_e": [(-118.540, 33.0),    # 11:03 SE edge at Marquez Knolls (AAR p.24)
                          (-118.535, 74.0),    # 11:44 E flank on Temescal ridge (AAR p.29)
                          (-118.530, 420.0),   # FireGuard 16:30-18:30 E front
                          (-118.519, 540.0)],  # FireGuard 18:30-20:30 E front
        # wind [H]: per member U(7, 16) m/s (Topanga TPGC1 median 7.4 to Malibu Hills MBUC1
        # median 15.6 m/s sustained, 10:30-06:00) from U(5, 35) deg (station means 2-49 deg;
        # inside-perimeter MBUC1 24 deg, Topanga 21, Malibu Canyon 34, KVNY 23)
        "wind_speed": (7.0, 16.0), "wind_from": (5.0, 35.0),
        "proxy": "C4", "proxy_alt": "C2",  # as Eaton (chaparral / coastal sage analogue) [H]
        "lat": 34.05, "elev": 300.0, "doy": 7, "ffmc": None, "dmc": 100.0, "dc": 800.0,
        # FFMC: hourly FFMC over Malibu Hills RAWS (inside the perimeter) from 7 Jan 00 UTC
        # (start 85), mean over the window; FEMS labels are +1 h (DATA_QUALITY.md), so the
        # window 18:30Z-14:00Z is read as labels 20:00-15:00
        "ffmc_raws": ("MALIBU HILLS", "2025-01-07T20:00", "2025-01-08T15:00"),
        "raws_dir": "palisades", "raws_glob": "raws_FEMS_*.csv",
        "step3_seeds": None, "ring": True,
    },
}
N_MAIN, N_SENS = 50, 20


def rss_gb() -> float:
    return L1.rss_gb()


def ffmc_from_raws(cfg) -> float:
    """Mean hourly FFMC (Van Wagner 1977 hourly) over the window from a RAWS station, starting
    at 85 at the first record (the PREREG_LR Eaton rule, generalised)."""
    prefix, t_from, t_to = cfg["ffmc_raws"]
    path = glob.glob(str(CALFIRE_DIR / "refs" / cfg.get("raws_dir", "eaton") / cfg.get("raws_glob", "raws_FEMS*.csv")))[0]
    rows = [r for r in csv.DictReader(open(path)) if r["StationName"].startswith(prefix)]
    rows.sort(key=lambda r: r["DateTime"])
    f, vals = 85.0, []
    for r in rows:
        if r["DateTime"] > t_to:
            break
        try:
            t = (float(r["Temperature(F)"]) - 32.0) * 5.0 / 9.0
            f = hourly_ffmc(t, float(r["RelativeHumidity(%)"]), float(r["WindSpeed(mph)"]) * 1.609,
                            float(r["Precipitation(in)"] or 0.0) * 25.4, f)
        except ValueError:
            continue
        if r["DateTime"] >= t_from:
            vals.append(f)
    return float(np.mean(vals))


def ring_geoms(fire: str, cfg: dict) -> list:
    """Microsoft footprints (lng/lat) whose centroid lies within 1 km outside the DINS domain."""
    import shapely

    if not cfg.get("ring") or not cfg.get("footprints"):
        return []
    dom = json.load(open(CALFIRE_DIR / "derived" / f"{fire}_domain.geojson"))
    d = shapely.union_all([shapely.geometry.shape(f["geometry"]) for f in dom["features"]])
    lat0 = d.centroid.y
    mx = 111320.0 * math.cos(math.radians(lat0))
    # a degree buffer is anisotropic: buffer in a local metric frame instead
    to_m = lambda g: shapely.transform(g, lambda c: np.column_stack([(c[:, 0] - d.centroid.x) * mx, (c[:, 1] - lat0) * 111320.0]))  # noqa: E731
    dm = to_m(d)
    ring = shapely.difference(shapely.buffer(dm, 1000.0), dm)
    shapely.prepare(ring)
    out = []
    with open(CALFIRE_DIR / cfg["footprints"]) as f:
        for line in f:
            g = shapely.geometry.shape(json.loads(line)["geometry"])
            c = g.centroid
            if g.area * mx * 111320.0 < 11.15:
                continue
            if ring.contains(shapely.Point((c.x - d.centroid.x) * mx, (c.y - lat0) * 111320.0)):
                out.append(g)
    return out


def burn_times(cfg, frame, xs, ys) -> np.ndarray:
    """Pre-registered progression rule: westward (``anchors_lng``) or along an axis
    (``anchors_axis``), as ``long_range_ember_validation.burn_times``."""
    if "anchors_lng_w" in cfg:
        olat, olng = cfg["origin"]
        ox = float(frame.to_local(olat, olng)[0])
        x = np.asarray(xs, float) - ox
        out = np.empty(len(x))
        for side, key in ((-1.0, "anchors_lng_w"), (1.0, "anchors_lng_e")):
            d = np.array([0.0] + [side * (float(frame.to_local(olat, lng)[0]) - ox) for lng, _ in cfg[key]])
            t = np.array([cfg["t0"]] + [tt for _, tt in cfg[key]])
            rl = (d[-1] - d[-2]) / (t[-1] - t[-2])
            m = (x * side) >= 0
            s_ = np.abs(x[m])
            out[m] = np.where(s_ > d[-1], t[-1] + (s_ - d[-1]) / rl, np.interp(s_, d, t))
        return out
    fire = "eaton" if "anchors_lng" in cfg else "axis"
    return L1.burn_times(fire, cfg, frame, xs, ys)


def tiebreak_top(p, n, perm_seed=SEED):
    """Top-n by p, ties broken by a seeded permutation (fixes the v1 index tie-break)."""
    perm = np.random.default_rng(perm_seed).permutation(len(p))
    order = np.lexsort((perm, -np.asarray(p, float)))
    top = np.zeros(len(p), bool)
    top[order[:n]] = True
    return top


def prob_scores(p, o, eps):
    p = np.asarray(p, float)
    clim = float(o.mean())
    return {"brier": L1.brier(p, o), "bss_vs_climatology": 1 - L1.brier(p, o) / L1.brier(np.full(len(o), clim), o),
            "log_score": L1.logscore(p, o, eps), "auc": L1.auc(p, o), "expected_count": float(p.sum()),
            "observed": int(o.sum()), "at_0.5": J.score(p >= 0.5, o),
            "count_matched": J.score(tiebreak_top(p, int(o.sum())), o),
            "reliability": L1.reliability(p, o)}


def run(args) -> None:
    S3._guard_out(args.out)
    fire = args.fire
    cfg = FIRES2[fire]
    if cfg is None:
        sys.exit(f"{fire}: set-up not filled in")
    cfg = dict(cfg)
    t_start = time.time()
    ids, geoms = L1.load_units(fire, cfg)
    obs_d, scored_d = L1.outcomes(fire, ids, args.dry_run)
    ring = ring_geoms(fire, cfg)
    n_dom = len(ids)
    allg = np.concatenate([np.asarray(geoms, dtype=object), np.asarray(ring, dtype=object)]) if ring else np.asarray(geoms, dtype=object)
    all_ids = np.concatenate([ids, -1 - np.arange(len(ring))]) if ring else ids
    u30 = build_units(allg, neighbour_cutoff_m=30.0, ids=all_ids)
    assert len(u30) == len(allg)
    dom = np.zeros(len(u30), bool)
    dom[:n_dom] = True
    obs = np.zeros(len(u30), bool)
    obs[:n_dom] = obs_d
    sm = np.zeros(len(u30), bool)
    sm[:n_dom] = scored_d
    frame = u30.frame
    h = cfg["h"]
    if cfg.get("ffmc") is None:
        cfg["ffmc"] = ffmc_from_raws(cfg)
    fmc = calculate_foliar_moisture(cfg["lat"], cfg["origin"][1], cfg["elev"], cfg["doy"])
    f_max = calculate_fbp(cfg["proxy"], cfg["wind_speed"][1] * 3.6, cfg["ffmc"], cfg["dmc"], cfg["dc"], fmc=fmc)
    kind_max = "crown" if f_max.cfb >= LR.CFB_CROWNING else "torch"
    pad = 2.0 * LR.cap_distance(kind_max, f_max.hfi, f_max.cfb, cfg["wind_speed"][1], cfg["proxy"]) + 1000.0
    raster = LR.Raster.around(u30.x, u30.y, pad, h)
    wl, n_buildings_mask = L1.wildland_mask(fire, cfg, frame, u30.footprints[:n_dom], raster)
    rr, cc = np.nonzero(wl)
    sx = raster.x0 + (cc + 0.5) * h
    sy = raster.y_top - (rr + 0.5) * h
    t_burn = burn_times(cfg, frame, sx, sy)
    window = cfg["window"]
    print(f"{fire}: units {len(u30)} (domain {n_dom}, ring {len(ring)}) scored {int(sm.sum())} | "
          f"wildland {len(sx) * h * h / 1e4:.0f} ha | FFMC {cfg['ffmc']:.1f} | raster "
          f"{raster.rows}x{raster.cols} | rss {rss_gb():.2f} GB", flush=True)

    from scipy import ndimage

    dist_px, (ir, ic) = ndimage.distance_transform_edt(~wl, return_indices=True)
    ur, uc, ok = raster.index(u30.x, u30.y)
    urc, ucc = np.clip(ur, 0, raster.rows - 1), np.clip(uc, 0, raster.cols - 1)
    d_cell = np.where(ok, dist_px[urc, ucc] * h, np.inf)
    d_edge_raw = np.maximum(d_cell - u30.size_m / 2.0 - h / 2.0, 0.0)
    t_grid = np.full(wl.shape, np.inf)
    t_grid[rr, cc] = t_burn
    nearest_t = np.where(ok, t_grid[ir[urc, ucc], ic[urc, ucc]], np.inf)
    t_front = np.where(d_edge_raw <= L1.FRONT_CONTACT_M, np.maximum(nearest_t, 0.0), np.inf)
    t_front[t_front > window] = np.inf
    d_edge = np.maximum(d_edge_raw - L1.WILDLAND_MIN_DIST_M, 0.0)
    del dist_px, ir, ic, t_grid
    d66 = burnout_minutes(150)
    eps = 1.0 / (2 * N_MAIN)

    def sources_for(proxy, u_ms, dmc, dc):
        f = calculate_fbp(proxy, u_ms * 3.6, cfg["ffmc"], dmc, dc, fmc=fmc)
        n = len(sx)
        cross = h / max(f.ros_final, 1e-3)
        return LR.EmberSourceCells(sx, sy, t_burn, t_burn + cross, np.full(n, f.hfi), np.full(n, f.sfc),
                                   np.full(n, f.cfc), np.full(n, f.cfb), np.full(n, proxy, dtype=object), h), f

    def hamada_fn(u_ms, wfrom, links=True):
        """The structure stage (Hamada H30, burn-out 66 min), advanced step by step."""
        w = [WindPeriod(0.0, u_ms * 3.6, wfrom)]
        lf = None if links else (lambda i, nb, sep: np.zeros(len(nb), dtype=bool))
        return LR.IncrementalHamada(u30, w, duration_min=window, fb=1.0, burnout_min=d66, link_filter=lf)

    involved, ember, meta = {}, {}, []
    n_main = N_MAIN if not args.quick else 3
    n_sens = N_SENS if not args.quick else 2
    base_opts = LR.LongRangeOptions(members=1, seed=SEED, raster_m=h)
    for m in range(n_main):
        tm = time.time()
        u_ms, wfrom = L1.member_wind(cfg, m)
        wind = [(0.0, u_ms * 3.6, wfrom)]
        src, f = sources_for(cfg["proxy"], u_ms, cfg["dmc"], cfg["dc"])
        fld = LR.landing_field(src, wind, window, base_opts, raster=raster)
        landed = LR.landed_on_buildings(u30, fld, base_opts).astype(np.float32)
        urban = LR.UrbanSource.build(u30, wind, base_opts, LR.URBAN_RASTER_M)

        def one(name, opts, landed_, edges, stage, urban_):
            rng = np.random.default_rng([SEED, m])
            t, s, lr, _, draw = LR.run_member_v2(u30, t_front, landed_, edges, opts, stage, window,
                                                 rng, urban_, wind)
            involved.setdefault(name, []).append(t <= window)
            ember.setdefault(name, []).append(lr)
            return t, lr, draw

        t, lr, draw = one("PRIMARY", base_opts, landed, fld.edges, hamada_fn(u_ms, wfrom), urban)
        # v1 chain on the same units (PREREG_LR primary)
        o1 = LR.LongRangeOptions(members=1, seed=20261011, raster_m=h, model="v1")
        fl1 = LR.landing_field(src, wind, window, o1, raster=raster)
        hz1 = LR.building_hazard(u30, fl1, o1, cfg["ffmc"])
        rng1 = np.random.default_rng([20261011, m])
        t_ign = LR.draw_ignition_times(hz1, fl1.edges, rng1) + o1.delay_s / 60.0
        t_ign[t_ign > window] = np.inf
        r1 = hamada_spread(u30, np.minimum(t_front, t_ign), [WindPeriod(0.0, u_ms * 3.6, wfrom)],
                           duration_min=window, burnout_min=d66)
        involved.setdefault("V1", []).append(np.asarray(r1.t_min) <= window)
        ember.setdefault("V1", []).append(np.isfinite(t_ign) & (t_ign < t_front))
        del fl1, hz1
        meta.append({"m": m, "wind_ms": round(u_ms, 2), "wind_from": round(wfrom, 1), "hfi_kw_m": round(f.hfi),
                     "cfb": round(f.cfb, 3), "p_b": draw.p_b, "q_median": float(np.median(draw.q)),
                     "n_per_kg": draw.n_scale * LR.N_PER_KG, **draw.info,
                     "front_contact": int(np.isfinite(t_front).sum()),
                     "ember_ignited": int(lr.sum()), "involved_domain": int(np.sum((t <= window) & dom)),
                     "involved_ring": int(np.sum((t <= window) & ~dom)),
                     "landed_share": fld.landed_total / max(fld.emitted_total, 1.0)})
        if m < n_sens:
            import dataclasses as dc_

            rep = lambda **k: dc_.replace(base_opts, **k)  # noqa: E731
            one("NO_URBAN", rep(urban_source=False), landed, fld.edges, hamada_fn(u_ms, wfrom), None)
            one("FIXED", rep(param_draws=False), landed, fld.edges, hamada_fn(u_ms, wfrom), urban)
            one("P_B_0.1", rep(p_b_range=(0.1, 0.1)), landed, fld.edges, hamada_fn(u_ms, wfrom), urban)
            one("P_B_1", rep(p_b_range=(1.0, 1.0)), landed, fld.edges, hamada_fn(u_ms, wfrom), urban)
            one("NO_MASS_FACTOR", rep(mass_factor=1.0), landed, fld.edges, hamada_fn(u_ms, wfrom), urban)
            o_fp = rep(receptive="footprint")
            l_fp = LR.landed_on_buildings(u30, fld, o_fp).astype(np.float32)
            one("HT08_ALPHA", o_fp, l_fp, fld.edges, hamada_fn(u_ms, wfrom), urban)
            one("HT08_ALPHA_1e-4", rep(receptive="footprint", param_draws=False), l_fp, fld.edges,
                hamada_fn(u_ms, wfrom), urban)
            del l_fp
            o5 = rep(step_min=5.0)
            fl5 = LR.landing_field(src, wind, window, o5, raster=raster)
            one("STEP_5", o5, LR.landed_on_buildings(u30, fl5, o5).astype(np.float32), fl5.edges,
                hamada_fn(u_ms, wfrom), urban)
            del fl5
            oq = rep(structure_tail_q=0.99)
            one("HIMOTO_Q99", oq, landed, fld.edges, hamada_fn(u_ms, wfrom),
                LR.UrbanSource.build(u30, wind, oq, LR.URBAN_RASTER_M))
            og = rep(gr_structure=5.68)
            one("GR_5.68", og, landed, fld.edges, hamada_fn(u_ms, wfrom),
                LR.UrbanSource.build(u30, wind, og, LR.URBAN_RASTER_M))
            one("NO_HAMADA", base_opts, landed, fld.edges, hamada_fn(u_ms, wfrom, links=False), urban)
            for name, (proxy, dmc, dc) in {f"proxy_{cfg['proxy_alt']}": (cfg["proxy_alt"], cfg["dmc"], cfg["dc"]),
                                           "fwi_dmc60_dc400": (cfg["proxy"], 60.0, 400.0)}.items():
                s2, _ = sources_for(proxy, u_ms, dmc, dc)
                fl2 = LR.landing_field(s2, wind, window, base_opts, raster=raster)
                one(name, base_opts, LR.landed_on_buildings(u30, fl2, base_opts).astype(np.float32),
                    fl2.edges, hamada_fn(u_ms, wfrom), urban)
                del fl2
        del fld, landed
        print(f"member {m:2d} wind {u_ms:5.1f} from {wfrom:5.1f} p_b {draw.p_b:.2f} q_med {float(np.median(draw.q)):.1e} urb_ign {draw.info['ember_ignitions_urban']} n_kg "
              f"{draw.n_scale * LR.N_PER_KG:5.0f} front {int(np.isfinite(t_front).sum())} ember {int(lr.sum())} "
              f"involved dom {int(np.sum((t <= window) & dom))} ring {int(np.sum((t <= window) & ~dom))} | "
              f"{time.time() - tm:.1f} s rss {rss_gb():.2f} GB", flush=True)

    P = {k: np.mean(np.asarray(v), axis=0) for k, v in involved.items()}
    PE = {k: np.mean(np.asarray(v), axis=0) for k, v in ember.items()}

    # baselines (domain units only are seeded / scored; ring units are buildings)
    w_mid = float(np.mean(cfg["wind_speed"])), float(np.mean(cfg["wind_from"]))
    if cfg.get("step3_seeds"):
        w3 = cfg["step3_seeds"]
        wind3 = [WindPeriod(0.0, float(w3["wind_speed_kmh"]), float(w3["wind_from_deg"]))]
        ud = build_units(np.asarray(geoms, dtype=object), neighbour_cutoff_m=30.0, ids=ids)
        _, seed_t_d = S3._seed_mask_times(ud, w3)
        seed_t = np.full(len(u30), np.inf)
        seed_t[:n_dom] = seed_t_d
        wb = float(w3["wind_from_deg"])
    else:
        wind3 = [WindPeriod(0.0, w_mid[0] * 3.6, w_mid[1])]
        seed_t = t_front.copy()
        wb = w_mid[1]
    pa = (hamada_spread(u30, seed_t, wind3, duration_min=window, burnout_min=d66).t_min <= window).astype(float)
    to = math.radians(wb + 180.0)
    proj = u30.x * math.sin(to) + u30.y * math.cos(to)
    band = np.zeros(len(u30), bool)
    di = np.nonzero(dom)[0]
    band[di[np.argsort(proj[di], kind="stable")[:int(math.ceil(0.05 * n_dom))]]] = True
    pb = (hamada_spread(u30, np.where(band, 0.0, np.inf), wind3, duration_min=window,
                        burnout_min=d66).t_min <= window).astype(float)
    pc = np.maximum(0.0, 0.6 * (1.0 - d_edge / 700.0))
    n_v2 = int(np.sum(P["PRIMARY"][sm] >= 0.5))
    pdist = np.zeros(len(u30))
    idx = np.nonzero(sm)[0]
    pdist[idx[tiebreak_top(-d_edge[idx], n_v2)]] = 1.0
    pe_ = np.full(len(u30), float(obs[sm].mean()))
    base = {"a_hamada": pa, "b_windward_edge": pb, "c_chen_mcaneney": pc,
            "d_distance_band": pdist, "e_climatology": pe_}

    blk = L1.blocks_of(u30.x[sm], u30.y[sm])
    os_ = obs[sm]
    res = {"fire": fire, "dry_run": bool(args.dry_run), "prereg_sha256": prereg_hash(),
           "config": {k: v for k, v in cfg.items() if k != "step3_seeds"}, "fmc": fmc,
           "units_domain": n_dom, "units_ring": len(ring), "units_scored": int(sm.sum()),
           "observed_destroyed": int(os_.sum()), "wildland_ha": float(len(sx) * h * h / 1e4),
           "front_contact_units": int(np.isfinite(t_front).sum()), "members_meta": meta,
           "scores": {}, "baselines": {}, "tests": {}, "distance_bands": [],
           "false_alarms_outside": None}
    if len(ring):
        res["false_alarms_outside"] = {
            k: {"ring_footprints": len(ring), "expected_involved": float(P[k][~dom].sum()),
                "expected_ember_ignited": float(PE[k][~dom].sum())} for k in P}
    for k, p in P.items():
        res["scores"][k] = prob_scores(p[sm], os_, eps)
    for k, p in base.items():
        res["baselines"][k] = prob_scores(p[sm], os_, eps)
    pp = P["PRIMARY"][sm]
    kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
    bri = lambda p_, o_: -L1.brier(p_, o_)  # noqa: E731  (positive = first better)
    res["tests"] = {
        "BSS_vs_c (Brier_c - Brier_v2)": L1.boot_diff(blk, bri, pp, pc[sm], os_),
        "kappa_v2_minus_b": L1.boot_diff(blk, kap, pp, pb[sm], os_),
        "kappa_v2_minus_d": L1.boot_diff(blk, kap, pp, pdist[sm], os_),
        "kappa_v2_minus_a": L1.boot_diff(blk, kap, pp, pa[sm], os_),
        "kappa_v2_minus_V1": L1.boot_diff(blk, kap, pp, P["V1"][sm], os_),
        "brier_V1_minus_v2": L1.boot_diff(blk, bri, pp, P["V1"][sm], os_),
        "BSS_vs_e (Brier_e - Brier_v2)": L1.boot_diff(blk, bri, pp, pe_[sm], os_),
    }
    res["bss_vs_c"] = 1.0 - L1.brier(pp, os_) / L1.brier(pc[sm], os_)
    t = res["tests"]
    res["decision"] = {"bss_vs_c_positive_ci": t["BSS_vs_c (Brier_c - Brier_v2)"]["p2.5"] > 0,
                       "kappa_vs_b_positive_ci": t["kappa_v2_minus_b"]["p2.5"] > 0,
                       "kappa_vs_d_positive_ci": t["kappa_v2_minus_d"]["p2.5"] > 0}
    res["decision"]["worth_showing"] = all(res["decision"].values())
    res["decision"]["decides"] = fire == "palisades"
    for lo, hi in ((0, 100), (100, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 1e9)):
        b = sm & (d_edge >= lo) & (d_edge < hi)
        res["distance_bands"].append({"band_m": f"{lo}-{hi:g}", "units": int(b.sum()),
                                      "observed_rate": float(obs[b].mean()) if b.any() else None,
                                      "mean_P_v2": float(P["PRIMARY"][b].mean()) if b.any() else None,
                                      "mean_P_v1": float(P["V1"][b].mean()) if b.any() else None,
                                      "mean_P_c": float(pc[b].mean()) if b.any() else None})
    res["seconds"] = time.time() - t_start
    res["max_rss_gb"] = rss_gb()
    tag = "_DRYRUN" if args.dry_run else ""
    (args.out / f"results_lr2_{fire}{tag}.json").write_text(json.dumps(res, indent=2, default=float))
    (args.out / f"tables_lr2_{fire}{tag}.md").write_text(tables_md(res))
    np.savez_compressed(args.out / f"units_lr2_{fire}{tag}.npz", x=u30.x, y=u30.y, dom=dom, scored=sm,
                        obs=obs, d_edge=d_edge, **{f"P_{k}": v for k, v in P.items()},
                        **{f"B_{k}": v for k, v in base.items()})
    print(f"wrote {args.out} in {res['seconds'] / 60:.1f} min, max rss {res['max_rss_gb']:.2f} GB")


def _no_spread(t0, dur):
    from types import SimpleNamespace

    t0 = np.asarray(t0, float)
    return SimpleNamespace(t_min=np.where(t0 <= dur, t0, np.inf), source=np.where(np.isfinite(t0), 1, 0))


def tables_md(r: dict) -> str:
    L = [f"# Long-range embers v2: {r['fire']}{' (DRY RUN, random outcomes)' if r['dry_run'] else ''}",
         "", "Illustrative — not validated. Aggregate tables only.", "",
         f"Units scored {r['units_scored']}, observed destroyed {r['observed_destroyed']}; ring units "
         f"{r['units_ring']}; wildland source {r['wildland_ha']:.0f} ha; front-contact units "
         f"{r['front_contact_units']}.", "",
         "| Run | Brier | BSS clim | Log | AUC | Σ P | κ@0.5 | pred@0.5 | P@0.5 | R@0.5 | κ count-matched |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for grp in ("scores", "baselines"):
        for k, s in r[grp].items():
            a = s["at_0.5"]
            L.append(f"| {k} | {s['brier']:.4f} | {s['bss_vs_climatology']:+.3f} | {s['log_score']:.3f} | "
                     f"{s['auc']:.3f} | {s['expected_count']:.0f} | {a['kappa']:+.3f} | {a['predicted']} | "
                     f"{J._f(a['precision'], True)} | {J._f(a['recall'], True)} | {s['count_matched']['kappa']:+.3f} |")
    L += ["", f"BSS vs (c): {r['bss_vs_c']:+.3f}", "", "## Pre-registered tests (95 % block bootstrap)", ""]
    for k, d in r["tests"].items():
        L.append(f"- {k}: {d['point']:+.4f} [{d['p2.5']:+.4f}, {d['p97.5']:+.4f}] ({d['blocks']} blocks)")
    L += ["", f"Decision: {r['decision']}", "", f"False alarms outside: {json.dumps(r['false_alarms_outside'])}",
          "", "## Distance from the wildland edge", "",
          "| Band (m) | Units | Observed | Mean P v2 | Mean P v1 | Mean P (c) |", "|---|---|---|---|---|---|"]
    f = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    for b in r["distance_bands"]:
        L.append(f"| {b['band_m']} | {b['units']} | {f(b['observed_rate'])} | {f(b['mean_P_v2'])} | "
                 f"{f(b['mean_P_v1'])} | {f(b['mean_P_c'])} |")
    return "\n".join(L) + "\n"


def prereg_hash() -> str:
    blob = {"PREREG_LR2": PREREG_LR2, "FIRES2": {k: ({kk: vv for kk, vv in v.items() if kk != "step3_seeds"}
                                                     if v else None) for k, v in FIRES2.items()}}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=float).encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("run")
    a.add_argument("--fire", default="palisades", choices=sorted(FIRES2))
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--dry-run", action="store_true", help="random outcomes; the status file is never read")
    a.add_argument("--quick", action="store_true", help="3 members (timing check only)")
    sub.add_parser("prereg")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    else:
        print(json.dumps({"PREREG_LR2": PREREG_LR2}, indent=2, default=float))
        print(prereg_hash())


if __name__ == "__main__":
    main()
