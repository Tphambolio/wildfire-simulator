#!/usr/bin/env python3
"""Structure step 3 validation: the vegetation-bridged cutoff and the combustible-roof scenario.

Specification: ``docs/structure-spread-spec.md`` §4.6, §6.2, §8. **Illustrative — not validated
in Canada.** Structure-only runs (no wildland front: seeds imposed, as in
``scripts/jasper_structure_validation.py``), scored building by building against observed damage.

Two tests, both fixed in ``PREREG_STEP3`` below **before any score was computed** (the commit
that adds this file precedes every scored run; do not edit after: add a dated block):

``independent``  CAL FIRE Damage Inspection (DINS) fires prepared under
                 ``~/dev/wildfire/validation-data/calfire-dins/`` (public CAL FIRE data; footprints
                 Microsoft US Building Footprints, ODbL; canopy Meta 1 m CHM, CC BY 4.0). This is
                 the confirmatory test: none of the step-3 values came from these fires.
``jasper``       Jasper 2024 (Municipality of Jasper damage layer, local use only), a disclosed
                 **non-independent** consistency check: the 20 % woody-cover threshold and the
                 10 m zone come from FPInnovations' study of the same fire.

Data never enter the repository; outputs (JSON + Markdown, aggregate tables only, no address or
per-building list) go to ``--out`` outside the repo.

Usage (from the repo root):

    PYTHONPATH=engine/src python3 scripts/structure_step3_validation.py jasper \\
        --out ~/dev/wildfire/validation-data/jasper2024/results-step3-2026-10-10
    PYTHONPATH=engine/src python3 scripts/structure_step3_validation.py independent --fire eaton \\
        --out ~/dev/wildfire/validation-data/calfire-dins/results-step3-2026-10-10
"""

from __future__ import annotations

import argparse
import csv
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
from firesim.structures import roofs as R  # noqa: E402
from firesim.structures import vegetation as V  # noqa: E402
from firesim.structures.embers import EmberOptions  # noqa: E402
from firesim.structures.spread import (  # noqa: E402
    SOURCE_EMBER,
    WindPeriod,
    burnout_minutes,
    hamada_spread,
    spread_with_embers,
)
from firesim.structures.units import build_units  # noqa: E402

CALFIRE_DIR = Path.home() / "dev" / "wildfire" / "validation-data" / "calfire-dins"
JASPER_VEG = (Path.home() / "dev" / "wildfire" / "structure-veg-data" / "jasper"
              / "jasper_footprint_veg_metrics.csv")
JASPER_CHM = Path.home() / "dev" / "wildfire" / "structure-veg-data" / "raw" / "meta_chm_021211222.tif"

# --------------------------------------------------------------------------------------------
# Pre-registration (fixed 2026-10-10 before any score was computed; do not edit after the first
# scored run: add a new dated block and report both).
# --------------------------------------------------------------------------------------------
PREREG_STEP3 = {
    "date_fixed": "2026-10-10",
    "questions": {
        "Q1_bridge": "Does the vegetation-bridged cutoff (links <= 20 m; 20-45 m only across >= 20 % "
                     "gap woody cover) improve the building-level end state over plain Hamada "
                     "(30 m cutoff) and over the distance and distance + vegetation baselines?",
        "Q2_roofs": "Does a random combustible-roof scenario (share s, ember threshold x k) change "
                    "the end state, and does any share beat the same run without roofs? Random "
                    "placement has no spatial skill, so building-level gains are not expected; "
                    "the test is of the envelope of counts and of kappa.",
    },
    "fixed_values": {
        "bridge": {"base_cutoff_m": V.BRIDGE_BASE_CUTOFF_M, "max_cutoff_m": V.BRIDGE_MAX_CUTOFF_M,
                   "min_gap_cover": V.BRIDGE_MIN_GAP_COVER,
                   "gap_cover": "convex hull of the pair minus both footprints; 1 m lattice; "
                                "other buildings = 0; open ground = canopy share (CHM >= 2 m) of "
                                "the non-building pixels; nan -> not bridged (spec §4.6)",
                   "canopy_source": "Meta/WRI 1 m CHM (Tolan et al. 2024) on its native "
                                    "EPSG:3857 grid, block 1 (no aggregation) for the "
                                    "validation fires"},
        "roofs": {"shares": [0.05, 0.15, 0.30], "psi_factor_primary": R.ROOF_PSI_FACTOR,
                  "psi_factor_sensitivity": list(R.ROOF_PSI_FACTOR_SENSITIVITY),
                  "psi_factor_source": R.ROOF_SOURCE,
                  "assignment_seeds": list(range(20261010, 20261030)),  # 20 draws per share
                  "note": "no roof data from Jasper or CAL FIRE set k or the shares; DINS "
                          "ROOFCONSTRUCTION is not read by this script"},
        "engine": {"hamada_cutoff_m": 30.0, "combustible_fraction": 1.0,
                   "burnout_min": 66.0, "design_fire_kw_m2": 150, "gr_structure": 10.0,
                   "embers_from_wildland": False},
    },
    "runs": {
        "H30": "Hamada, cutoff 30 m, burn-out 66 min (engine defaults) - reference",
        "H20": "Hamada, cutoff 20 m, burn-out (what the bridge reduces to with no cover)",
        "H45": "Hamada, cutoff 45 m, burn-out (upper envelope of the bridge)",
        "VB": "vegetation-bridged cutoff, burn-out (Q1 primary)",
        "E": "Hamada (30 m) + embers (150 kW/m2, GR' 10, structure sources only), burn-out",
        "R_s{share}_k0.375": "E + roof scenario, 20 assignment seeds each (Q2 primary)",
        "R_s0.15_k{0.5,1.0}": "sensitivity of k (k = 1 must reproduce E exactly)",
        "VBR_s0.15": "bridge + embers + roofs 0.15, k 0.375 (combined)",
        "jasper_only": "E and R repeated at 27 km/h (PREREG_EMBERS E9 gust case), because no "
                       "ember ignition occurred at 15 km/h in R10",
    },
    "baselines": {
        "a_distance_band_count_matched": "N nearest by edge distance to the seed footprints, "
                                         "N = the run's predicted count",
        "e1_distance_vegetation": "N highest out-of-fold logistic scores from log(1 + distance) "
                                  "+ canopy cover 0-10 m; 250 m blocks, 10 folds, 10 repeats, "
                                  "seed 20261010 (distance_vegetation_scores)",
        "e2_distance_vegetation_10_30": "as e1 with canopy cover 0-10 and 10-30 m (secondary)",
        "d_seeds_only": "the seeds",
    },
    "metrics": "confusion counts, precision, recall, F1, Cohen's kappa (outcome destroyed = 1); "
               "kappa differences with a 250 m block bootstrap (2,000 resamples, seed 20261010): "
               "VB - H30, VB - (a), VB - (e1); R - E per assignment seed (median and range); "
               "roof runs: mean, 2.5 and 97.5 % of kappa and of the involved count over seeds",
    "decision_rule": "The bridge is adopted for display only if, on the independent fire, kappa(VB) "
                     "exceeds kappa(H30), (a) and (e1) with each 95 % block-bootstrap interval of "
                     "the difference excluding zero. The roof scenario stays a labelled scenario "
                     "whatever the result (random placement); it is reported as 'changes counts "
                     "by X' only. Jasper results are consistency checks and cannot adopt anything.",
    "independent": {
        "fires": "every fire under calfire-dins/derived with polygon units (<fire>_units.gpkg), "
                 "<fire>_units_status.csv, <fire>_unit_veg_metrics.csv, a Meta CHM "
                 "(raw/meta_chm_<fire>.tif) and a run config <fire>_run_config.json; at the time "
                 "of fixing, only Eaton 2025 has polygon units (Camp units are DINS points, "
                 "Tubbs not yet prepared)",
        "outcome": "DAMAGE 'Destroyed (>50%)' = 1; 'No Damage', 'Affected', 'Minor', 'Major' = 0; "
                   "UNSURVEYED and Inaccessible units take part in the spread but are not scored",
        "run_config": "seed points (lat, lng) and nearest_per_point, wind_speed_kmh (10 m), "
                      "wind_from_deg, window_min, each with its source, written by the data "
                      "preparation from published accounts without reading DINS outcomes. Its "
                      "sha256 is recorded with the results. If it does not exist the runner "
                      "stops (scoring pending)",
        "sensitivity_seeds": "I4 windward band: the upwind-most 5 % of units along the wind "
                             "axis (no document locations)",
    },
    "jasper": {
        "base": "PREREG primary (R10): I0 seeds (15), 1,119 townsite units, 15 km/h from 225 "
                "deg, window 360 min, outcome Destroyed",
        "canopy": "Meta CHM tile 021211222 (imagery 2011-09-07, 13 years before the fire)",
        "vegetation": "structure-veg-data/jasper/jasper_footprint_veg_metrics.csv (OBJECTID)",
        "status": "NOT independent: the 20 % threshold is FPI's, from the same fire; the "
                  "post hoc 10-30 m signal (step-3 data report §3.3) is also from Jasper",
    },
    "expectation_from_a_model_only_dry_run": "Jasper geometry with random statuses (no observed "
        "status read, 2026-10-10): VB involves 306 units against 124 (H20), 396 (H30) and 579 "
        "(H45); E and every roof run involve the same 396 units at 15 and 27 km/h, with no ember "
        "ignition: with buildings as the only ember sources the 0.375 factor does not bring "
        "pooled loads to psi*. The roof scenario is expected to change nothing at Jasper.",
    "disclosure": "Before fixing this block the author saw the CAL FIRE preparation's coverage "
                  "files (total damage-class counts per fire) and the column names of the "
                  "status and vegetation tables; no model, baseline or score was computed.",
}


# ---------------------------------------------------------------------------------- helpers


def canopy_for(units_lnglat, chm_path: Path, pad_m: float = 150.0) -> V.CanopyWindow:
    """A 1 m-native CanopyWindow over the units' extent (buildings masked), from the Meta CHM."""
    import rasterio
    import shapely
    from rasterio.windows import Window

    g3857 = shapely.transform(np.asarray(units_lnglat, dtype=object),
                              lambda c: np.column_stack(V.lnglat_to_3857(c[:, 0], c[:, 1])))
    b = shapely.total_bounds(g3857)
    with rasterio.open(chm_path) as s:
        inv = ~s.transform
        c0, r0 = inv * (b[0] - pad_m, b[3] + pad_m)
        c1, r1 = inv * (b[2] + pad_m, b[1] - pad_m)
        c0, r0 = max(int(c0), 0), max(int(r0), 0)
        c1, r1 = min(int(math.ceil(c1)), s.width), min(int(math.ceil(r1)), s.height)
        win = Window(c0, r0, c1 - c0, r1 - r0)
        chm = s.read(1, window=win)
        tr = s.window_transform(win)
    return V.canopy_window_from_chm(chm, tr, g3857, block=1)


def baseline_preds(n: int, dist, veg_scores: dict, seed, mask) -> dict:
    """Count-matched baselines over the scored units (``mask``): (a) the ``n`` nearest to the
    seeds, (e1)/(e2) the ``n`` highest distance + vegetation scores, (d) the seeds."""
    idx = np.nonzero(mask)[0]
    out = {}
    order = idx[np.lexsort((idx, np.asarray(dist, float)[idx]))]
    a = np.zeros(len(mask), dtype=bool)
    a[order[:n]] = True
    out["a_distance_band_count_matched"] = a
    for name, sc in veg_scores.items():
        e = np.zeros(len(mask), dtype=bool)
        e[idx[J.top_n(np.asarray(sc, float)[idx], n)]] = True
        out[name] = e
    out["d_seeds_only"] = np.asarray(seed, bool).copy()
    return out


def scored_block(units, seed, pred, obs, mask, dist, veg_scores):
    """Scores of one prediction and its count-matched baselines on the scored units."""
    n = int(pred[mask].sum())
    bl = baseline_preds(n, dist, veg_scores, seed, mask)
    out = {"predicted_scored": n, "firesim": J.score(pred, obs, mask),
           **{k: J.score(v, obs, mask) for k, v in bl.items()}}
    return out, bl


def masked_diff(units, mask, a, b, obs) -> dict:
    """Block-bootstrap kappa difference on the scored units only."""
    from types import SimpleNamespace

    sub = SimpleNamespace(x=units.x[mask], y=units.y[mask])
    return J.block_bootstrap_kappa_diff(sub, a[mask], b[mask], obs[mask])


def run_set(units45, units_by_cut, seed, obs, mask, wind_kmh, wind_from, window, canopy,
            veg_scores, dist, log, extra_winds=()) -> dict:
    """All PREREG_STEP3 runs on one fire; returns the results dict."""
    P = PREREG_STEP3
    d66 = burnout_minutes(150)
    tf = {c: np.where(seed[c], 0.0, np.inf) for c in seed}
    res = {"runs": {}, "roof_runs": {}}

    def wind(kmh):
        return [WindPeriod(0.0, kmh, wind_from)]

    def keep(name, u_key, pred, src=None):
        u = units_by_cut[u_key]
        sb, _ = scored_block(u, seed[u_key], pred, obs, mask, dist, veg_scores)
        res["runs"][name] = {"predicted": int(pred.sum()), "scores": sb}
        if src is not None:
            res["runs"][name]["ember_units"] = int(np.sum(pred & (src == SOURCE_EMBER)))
        f = sb["firesim"]
        log(f"{name:22s} pred={int(pred.sum()):5d} kappa={f['kappa']:+.3f} "
            f"P={f['precision']:.3f} R={f['recall']:.3f}  (a)={sb['a_distance_band_count_matched']['kappa']:+.3f}"
            f"  (e1)={sb['e1_distance_vegetation']['kappa']:+.3f}")
        return pred

    preds = {}
    for cut in (30.0, 20.0, 45.0):
        u = units_by_cut[cut]
        r = hamada_spread(u, tf[cut], wind(wind_kmh), duration_min=window, burnout_min=d66)
        preds[f"H{cut:g}"] = keep(f"H{cut:g}", cut, r.t_min <= window)
    links = V.BridgedLinks(units45, canopy)
    r = hamada_spread(units45, tf[45.0], wind(wind_kmh), duration_min=window, burnout_min=d66,
                      link_filter=links)
    preds["VB"] = keep("VB", 45.0, r.t_min <= window)
    res["runs"]["VB"]["bridge"] = links.stats()
    u30 = units_by_cut[30.0]

    def bl(name, key):
        return baseline_preds(int(preds[name][mask].sum()), dist, veg_scores, seed[30.0], mask)[key]

    res["diffs"] = {
        "VB_minus_H30": masked_diff(u30, mask, preds["VB"], preds["H30"], obs),
        "VB_minus_a": masked_diff(u30, mask, preds["VB"], bl("VB", "a_distance_band_count_matched"), obs),
        "VB_minus_e1": masked_diff(u30, mask, preds["VB"], bl("VB", "e1_distance_vegetation"), obs),
        "H30_minus_a": masked_diff(u30, mask, preds["H30"], bl("H30", "a_distance_band_count_matched"), obs),
        "H30_minus_e1": masked_diff(u30, mask, preds["H30"], bl("H30", "e1_distance_vegetation"), obs),
    }

    opts = EmberOptions(design_fire_kw_m2=150, gr_structure=10.0, from_wildland=False)
    keys = R.building_keys(u30)
    for kmh in (wind_kmh, *extra_winds):
        tag = "" if kmh == wind_kmh else f"_w{kmh:g}"
        e = spread_with_embers(u30, tf[30.0], wind(kmh), duration_min=window, embers=opts,
                               burnout_min=d66)
        pe = keep(f"E{tag}", 30.0, e.t_min <= window, e.source)
        variants = [(s, R.ROOF_PSI_FACTOR) for s in P["fixed_values"]["roofs"]["shares"]]
        variants += [(0.15, k) for k in P["fixed_values"]["roofs"]["psi_factor_sensitivity"]]
        for s, k in variants:
            name = f"R_s{s:g}_k{k:g}{tag}"
            kap, cnt, dk, emb = [], [], [], []
            for rs in P["fixed_values"]["roofs"]["assignment_seeds"]:
                roof = R.assign_combustible_roofs(keys, s, rs)
                rr = spread_with_embers(u30, tf[30.0], wind(kmh), duration_min=window,
                                        embers=opts, burnout_min=d66,
                                        psi_factor=R.psi_factors(roof, k))
                pr = rr.t_min <= window
                if k == 1.0:
                    assert np.array_equal(pr, pe), "k = 1 must reproduce E exactly"
                kap.append(J.score(pr, obs, mask)["kappa"])
                cnt.append(int(pr.sum()))
                emb.append(int(np.sum(pr & (rr.source == SOURCE_EMBER))))
                dk.append(kap[-1] - J.score(pe, obs, mask)["kappa"])
            q = lambda v, p: float(np.nanpercentile(v, p))  # noqa: E731
            res["roof_runs"][name] = {
                "share": s, "psi_factor": k, "wind_kmh": kmh, "seeds": len(kap),
                "label": R.roof_label(s),
                "kappa": {"mean": float(np.nanmean(kap)), "p2.5": q(kap, 2.5), "p97.5": q(kap, 97.5)},
                "predicted": {"mean": float(np.mean(cnt)), "min": int(min(cnt)), "max": int(max(cnt))},
                "ember_units": {"mean": float(np.mean(emb)), "max": int(max(emb))},
                "kappa_minus_E": {"median": float(np.nanmedian(dk)), "min": float(np.nanmin(dk)),
                                  "max": float(np.nanmax(dk))},
            }
            rr_ = res["roof_runs"][name]
            log(f"{name:22s} pred mean={rr_['predicted']['mean']:.1f} [{rr_['predicted']['min']}-"
                f"{rr_['predicted']['max']}] kappa mean={rr_['kappa']['mean']:+.3f} "
                f"dk median={rr_['kappa_minus_E']['median']:+.4f} ember max={rr_['ember_units']['max']}")
    # combined: bridge + embers + roofs 0.15
    keys45 = R.building_keys(units45)
    kap, cnt = [], []
    for rs in P["fixed_values"]["roofs"]["assignment_seeds"]:
        roof = R.assign_combustible_roofs(keys45, 0.15, rs)
        rr = spread_with_embers(units45, tf[45.0], wind(wind_kmh), duration_min=window,
                                embers=opts, burnout_min=d66, link_filter=V.BridgedLinks(units45, canopy),
                                psi_factor=R.psi_factors(roof))
        pr = rr.t_min <= window
        kap.append(J.score(pr, obs, mask)["kappa"])
        cnt.append(int(pr.sum()))
    res["roof_runs"]["VBR_s0.15"] = {"kappa": {"mean": float(np.nanmean(kap)),
                                               "p2.5": float(np.nanpercentile(kap, 2.5)),
                                               "p97.5": float(np.nanpercentile(kap, 97.5))},
                                     "predicted": {"mean": float(np.mean(cnt)), "min": int(min(cnt)),
                                                   "max": int(max(cnt))}}
    return res


def tables_md(title: str, r: dict) -> str:
    L = [f"# {title}", "", "Illustrative — not validated in Canada. Aggregate tables only.", "",
         "| Run | Pred | TP | FP | FN | TN | Precision | Recall | F1 | κ | κ (a) | κ (e1) | κ (e2) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, run in r["runs"].items():
        s = run["scores"]["firesim"]
        a = run["scores"]["a_distance_band_count_matched"]["kappa"]
        e1 = run["scores"]["e1_distance_vegetation"]["kappa"]
        e2 = run["scores"]["e2_distance_vegetation_10_30"]["kappa"]
        L.append(f"| {name} | {s['predicted']} | {s['TP']} | {s['FP']} | {s['FN']} | {s['TN']} | "
                 f"{J._f(s['precision'], True)} | {J._f(s['recall'], True)} | {J._f(s['F1'], True)} | "
                 f"{J._f(s['kappa'])} | {J._f(a)} | {J._f(e1)} | {J._f(e2)} |")
    L += ["", "## κ differences (250 m block bootstrap, 95 %)", ""]
    for k, d in r["diffs"].items():
        L.append(f"- {k}: {d['point']:+.3f} [{d['p2.5']:+.3f}, {d['p97.5']:+.3f}] ({d['blocks']} blocks)")
    if "bridge" in r["runs"].get("VB", {}):
        L.append(f"- bridge links: {r['runs']['VB']['bridge']}")
    L += ["", "## Roof scenarios (scenario: X % combustible roofs, not observed roofs)", "",
          "| Run | Label | Seeds | Pred mean [min-max] | Ember units mean (max) | κ mean [2.5-97.5 %] | κ − E median [min, max] |",
          "|---|---|---|---|---|---|---|"]
    for name, rr in r["roof_runs"].items():
        if "label" not in rr:
            L.append(f"| {name} | scenario: 15 % combustible roofs (not observed roofs) + bridge | 20 | "
                     f"{rr['predicted']['mean']:.1f} [{rr['predicted']['min']}-{rr['predicted']['max']}] | — | "
                     f"{rr['kappa']['mean']:+.3f} [{rr['kappa']['p2.5']:+.3f}, {rr['kappa']['p97.5']:+.3f}] | — |")
            continue
        L.append(f"| {name} | {rr['label']} | {rr['seeds']} | {rr['predicted']['mean']:.1f} "
                 f"[{rr['predicted']['min']}-{rr['predicted']['max']}] | {rr['ember_units']['mean']:.1f} "
                 f"({rr['ember_units']['max']}) | {rr['kappa']['mean']:+.3f} [{rr['kappa']['p2.5']:+.3f}, "
                 f"{rr['kappa']['p97.5']:+.3f}] | {rr['kappa_minus_E']['median']:+.4f} "
                 f"[{rr['kappa_minus_E']['min']:+.4f}, {rr['kappa_minus_E']['max']:+.4f}] |")
    return "\n".join(L) + "\n"


def _veg_matrix(ids, table: dict, cols) -> np.ndarray:
    return np.array([[table.get(i, {}).get(c, np.nan) for c in cols] for i in ids], dtype=float)


def _read_veg(path: Path, key: str) -> dict:
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out[int(float(row[key]))] = {k: (float(v) if v not in ("", "nan") else np.nan)
                                         for k, v in row.items() if k.startswith("cc_")}
    return out


def _guard_out(out: Path) -> None:
    if REPO in out.resolve().parents or out.resolve() == REPO:
        sys.exit("refusing to write validation data inside the repository")
    out.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------------- jasper


def main_jasper(args) -> None:
    _guard_out(args.out)
    path = args.features or J.DEFAULT_DATA_DIR / J.CACHE_NAME
    geoms, status, oid = J.load_features(path)
    assessed = np.isin(status, ["Destroyed", "Visible Damage", "No Visible Damage"])
    geoms, oid = geoms[assessed], oid[assessed]
    obs = status[assessed] == "Destroyed"
    P = J.PREREG
    units = {c: build_units(geoms, neighbour_cutoff_m=c, ids=oid) for c in (20.0, 30.0, 45.0)}
    for u in units.values():
        assert len(u) == len(geoms)
    seed = {c: J.seeds_for(units[c], P["ignition_sets"]["I0_primary"], P["wind_from_deg"])
            for c in units}
    u30 = units[30.0]
    dist = J.edge_distance_to_seeds(u30, seed[30.0])
    veg = _read_veg(args.veg, "fid")
    vm = _veg_matrix(oid, veg, ("cc_0_10", "cc_10_30"))
    veg_scores = {
        "e1_distance_vegetation": J.distance_vegetation_scores(u30, dist, vm[:, :1], obs),
        "e2_distance_vegetation_10_30": J.distance_vegetation_scores(u30, dist, vm, obs),
    }
    canopy = canopy_for(geoms, args.chm)
    mask = np.ones(len(obs), dtype=bool)
    t0 = time.time()
    res = run_set(units[45.0], units, seed, obs, mask, P["wind_speed_kmh"], P["wind_from_deg"],
                  P["window_min"], canopy, veg_scores, dist, print, extra_winds=(27.0,))
    res.update({"prereg_step3": PREREG_STEP3, "prereg_base": P, "units_scored": int(mask.sum()),
                "veg_coverage": int(np.isfinite(vm[:, 0]).sum()), "seconds": time.time() - t0})
    (args.out / "results_step3_jasper.json").write_text(json.dumps(res, indent=2, default=float))
    (args.out / "tables_step3_jasper.md").write_text(
        tables_md("Jasper 2024 step 3 (non-independent consistency check)", res))
    print(f"wrote {args.out}")


# ---------------------------------------------------------------------------------- independent


def main_independent(args) -> None:
    import geopandas as gpd

    _guard_out(args.out)
    d = CALFIRE_DIR / "derived"
    fire = args.fire
    cfg_path = d / f"{fire}_run_config.json"
    needed = [d / f"{fire}_units.gpkg", d / f"{fire}_units_status.csv",
              d / f"{fire}_unit_veg_metrics.csv", CALFIRE_DIR / "raw" / f"meta_chm_{fire}.tif", cfg_path]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        sys.exit("scoring pending: missing " + ", ".join(missing))
    cfg = json.loads(cfg_path.read_text())
    cfg_sha = hashlib.sha256(cfg_path.read_bytes()).hexdigest()
    g = gpd.read_file(needed[0])[["unit_id", "geometry"]]  # geometry + id only (no attributes)
    g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    ids = g.unit_id.to_numpy(dtype=np.int64)
    geoms = np.asarray(g.geometry.values, dtype=object)
    status = {}
    with open(needed[1], newline="") as f:
        for row in csv.DictReader(f):
            status[int(row["unit_id"])] = row["DAMAGE"]
    st = np.array([status.get(int(i), "UNSURVEYED") for i in ids])
    scored = ~np.isin(st, ["UNSURVEYED", "Inaccessible"])
    obs = st == "Destroyed (>50%)"
    units = {c: build_units(geoms, neighbour_cutoff_m=c, ids=ids) for c in (20.0, 30.0, 45.0)}
    for u in units.values():
        assert len(u) == len(geoms), "a footprint was dropped by build_units"
    seed = {}
    for c, u in units.items():
        s = np.zeros(len(u), dtype=bool)
        import shapely

        for lat, lng in cfg["seed_points"]:
            x, y = u.frame.to_local(lat, lng)
            dd = shapely.distance(u.footprints, shapely.Point(float(x), float(y)))
            s[np.argsort(dd, kind="stable")[:int(cfg.get("nearest_per_point", 5))]] = True
        seed[c] = s
    u30 = units[30.0]
    dist = J.edge_distance_to_seeds(u30, seed[30.0])
    veg = _read_veg(needed[2], "fid")
    vm = _veg_matrix(ids, veg, ("cc_0_10", "cc_10_30"))
    sub = {"x": u30.x, "y": u30.y}
    veg_scores = {}
    for name, cols in (("e1_distance_vegetation", vm[:, :1]), ("e2_distance_vegetation_10_30", vm)):
        sc = np.full(len(obs), -np.inf)
        from types import SimpleNamespace

        us = SimpleNamespace(x=sub["x"][scored], y=sub["y"][scored])
        sc[scored] = J.distance_vegetation_scores(us, dist[scored], cols[scored], obs[scored])
        veg_scores[name] = sc
    canopy = canopy_for(geoms, needed[3])
    t0 = time.time()
    res = run_set(units[45.0], units, seed, obs, scored, float(cfg["wind_speed_kmh"]),
                  float(cfg["wind_from_deg"]), float(cfg["window_min"]), canopy, veg_scores, dist,
                  print)
    res.update({"prereg_step3": PREREG_STEP3, "fire": fire, "run_config": cfg,
                "run_config_sha256": cfg_sha, "units": len(ids), "units_scored": int(scored.sum()),
                "veg_coverage": int(np.isfinite(vm[:, 0]).sum()), "seconds": time.time() - t0})
    (args.out / f"results_step3_{fire}.json").write_text(json.dumps(res, indent=2, default=float))
    (args.out / f"tables_step3_{fire}.md").write_text(
        tables_md(f"CAL FIRE {fire}: step 3 independent test", res))
    print(f"wrote {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("jasper")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--features", type=Path, default=None)
    a.add_argument("--veg", type=Path, default=JASPER_VEG)
    a.add_argument("--chm", type=Path, default=JASPER_CHM)
    b = sub.add_parser("independent")
    b.add_argument("--fire", default="eaton")
    b.add_argument("--out", type=Path, required=True)
    sub.add_parser("prereg", help="print the pre-registration")
    args = ap.parse_args()
    if args.cmd == "jasper":
        main_jasper(args)
    elif args.cmd == "independent":
        main_independent(args)
    else:
        print(json.dumps(PREREG_STEP3, indent=2, default=float))


if __name__ == "__main__":
    main()
