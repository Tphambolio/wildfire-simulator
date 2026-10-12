#!/usr/bin/env python3
"""Long-range ember chain: pre-registered building-level test on CAL FIRE DINS fires.

Specification: ``docs/structure-spread-spec.md`` §6.3; engine ``firesim.structures.long_range``.
**Illustrative — not validated in Canada.** The literature review *Long-range ember ignition
literature 2026-10-11* (§5.4) set the outline; ``PREREG_LR`` below fixes every value **before
any outcome is read** (commit and push precede the first scored run; do not edit after: add a
dated block and report both).

Eaton 2025 is primary, Camp 2018 secondary. Jasper 2024 is not run (no digitised fire
positions or perimeter; disclosed). The wildland fire is imposed (it is the ember source), not
predicted: there is no public timed perimeter series for either fire, so the source is the
vegetated, building-free part of the final CAL FIRE FRAP perimeter with burn times from a
documented progression rule (narrative front positions), and intensity from a pre-registered
FBP proxy fuel type.

Data stay outside the repository; outputs (aggregate JSON + Markdown, no per-building list,
no address) go to ``--out`` outside the repo.

Usage (repo root):
    PYTHONPATH=engine/src python3 scripts/long_range_ember_validation.py run --fire eaton \\
        --out ~/dev/wildfire/validation-data/calfire-dins/results-long-range-2026-10-11 [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import resource
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import jasper_structure_validation as J  # noqa: E402
import structure_step3_validation as S3  # noqa: E402
from firesim.fbp.calculator import calculate_fbp, calculate_foliar_moisture  # noqa: E402
from firesim.fwi.calculator import hourly_ffmc  # noqa: E402
from firesim.structures import long_range as LR  # noqa: E402
from firesim.structures import roofs as R  # noqa: E402
from firesim.structures import vegetation as V  # noqa: E402
from firesim.structures.spread import (  # noqa: E402
    SOURCE_EMBER,
    WindPeriod,
    burnout_minutes,
    hamada_spread,
)
from firesim.structures.units import build_units  # noqa: E402

CALFIRE_DIR = Path.home() / "dev" / "wildfire" / "validation-data" / "calfire-dins"

# --------------------------------------------------------------------------------------------
# Pre-registration (fixed 2026-10-11 before any outcome of this test was read).
# --------------------------------------------------------------------------------------------
PREREG_LR = {
    "date_fixed": "2026-10-11",
    "question": "Does the literature-only long-range ember chain (generation from torching/"
                "crowning wildland cells, untruncated Sardoy transport capped by Albini, "
                "probabilistic per-building ignition, then the Hamada structure stage) give "
                "better calibrated per-building destruction probabilities on Eaton 2025 than "
                "a Chen & McAneney distance-decay rule, and better kappa than windward-edge "
                "seeding and a count-matched distance band?",
    "chain_primary": {
        "n_per_kg": LR.N_PER_KG, "f_surface": LR.F_SURFACE, "k_glowing": LR.K_GLOWING,
        "p_building": LR.P_BUILDING, "ring_m": LR.RING_M, "fuel_category": "grass_summer",
        "tail": "cap (Albini 2012 Table 1 crowning / Albini 1979 torching; beyond-cap embers dropped)",
        "sigma_y": "0.92 x source cell size", "delay_s": LR.DELAY_S, "bin_min": LR.DEFAULT_BIN_MIN,
        "raster_m": {"eaton": 20.0, "camp": 40.0},
        "structure_stage": "Hamada cutoff 30 m, combustible fraction 1, burn-out 66 min (150 kW/m2 "
                           "design fire), no short-range ember pile stage",
        "members": 50, "member_seeds": "numpy default_rng([20261011, m]) for ignition draws; "
                                       "wind draws from default_rng([20261011, 1000 + m])",
    },
    "fires": {
        "eaton": {
            "role": "primary (decides)",
            "units": "13,545 pre-fire Microsoft footprints in FRAP perimeter + 300 ft; outcome "
                     "DAMAGE 'Destroyed (>50%)' = 1; unsurveyed and Inaccessible excluded from "
                     "scoring, kept as fuel",
            "window": "18:30 7 Jan to 06:00 8 Jan PST (0-690 min)",
            "wildland_source": "20 m raster cells inside the FRAP final perimeter, ESA WorldCover "
                               "2021 tree/shrub/grass (10/20/30), centre >= 50 m from every "
                               "Microsoft footprint (raster distance), in 8-connected patches "
                               ">= 25 ha [H]",
            "progression": "What is available: final perimeter, ignition 18:11 at 34.19,-118.09, "
                           "and narrative front positions (AAR pp.18, 123; investigation report); "
                           "no timed perimeters. Rule [H]: burn time is piecewise linear in "
                           "westward distance from the origin through (origin, -19 min), "
                           "(-118.12, 210 min = 22:00, 'front on the foothills' 21:30-22:30), "
                           "(-118.14, 468 min = 02:18, front north of Farnsworth Park), "
                           "extrapolated at the last rate; cells east of the origin at the first "
                           "segment's rate. Each cell burns for 20 m / ROS",
            "proxy_fuel": "C4 (dense, low-crown immature pine as a chaparral analogue) [H]; "
                          "sensitivity C2",
            "fwi": "FFMC: hourly FFMC (Van Wagner 1977) over San Rafael Hills RAWS hourly "
                   "records from 7 Jan 00 UTC starting at 85, mean over the window; also the "
                   "DFFMC for p_e. DMC 100, DC 800 (no rain since spring 2024) [H]; "
                   "sensitivity DMC 60, DC 400. FMC from ST-X-3 (34.2 N, 500 m, day 7). "
                   "Flat terrain",
            "wind": "per member: speed U(9, 18) m/s (KBUR low, Henninger high; SCE Eaton Canyon "
                    "13 m/s in the middle), from U(10, 30) deg (KBUR, Mill Creek, San Rafael "
                    "Hills), constant over the window",
            "front_contact": "a unit whose centroid-cell distance to a source cell, minus half its "
                             "size and half a cell, is <= 60 m is reached by the front at that "
                             "cell's burn time [H]",
        },
        "camp": {
            "role": "secondary (reported, does not decide)",
            "units": "23,256 DINS points as 169 m2 squares (PREREG_STEP3_CAMP rule)",
            "window": "07:30-15:30 8 Nov PST (0-480 min)",
            "wildland_source": "40 m raster cells inside the FRAP perimeter, centre >= 50 m from "
                               "every DINS square, patches >= 25 ha; no vegetation filter "
                               "(WorldCover and CHM are post-fire) [H]",
            "progression": "Rule [H]: burn time linear in distance along the NE->SW axis (225 deg) "
                           "from the origin (39.815,-121.434, 06:20 = -70 min) through Pentz Rd "
                           "front 09:07 (39.757,-121.571; TN2135 pp.xix, 63) and west Paradise "
                           "10:00 (39.76,-121.62; p.xxvii), extrapolated; cells behind the "
                           "origin at the first segment's rate",
            "proxy_fuel": "C7 (ponderosa pine - Douglas-fir, the FBP analogue) [H]; sensitivity C4",
            "fwi": "FFMC 92 (Jarbo Gap RH 11-23 %) [H]; DMC 100, DC 800 [H]; FMC ST-X-3",
            "wind": "per member: U(5, 12) m/s from U(35, 55) deg (Jarbo Gap / NIST NE)",
            "front_contact": "as Eaton",
        },
        "jasper": "not run: no digitised 2024 fire positions or perimeter in the local data; "
                  "disclosed as a gap (its statuses were seen in R10/R12/R22)",
    },
    "metrics": {
        "primary": "Brier score of P = share of members with the unit involved by the window end; "
                   "Brier skill score against (c) and against climatology (e)",
        "secondary": "log score (P clipped to [1/(2M), 1 - 1/(2M)], baselines clipped the same "
                     "way); AUC; reliability (10 equal-width bins); kappa, precision, recall at "
                     "P >= 0.5 and at the count-matched threshold (top N, N = observed destroyed); "
                     "predicted (sum P) vs observed count; distance-band table",
        "bootstrap": "250 m blocks, 2,000 resamples, seed 20261011",
        "out_of_domain": "Eaton false-alarm tally: expected number of long-range ember "
                         "ignitions (sum of the member-mean 1 - exp(-sum h), primary "
                         "configuration, no Hamada) among Microsoft footprints within 1 km "
                         "outside the domain (DINS did not survey them; almost all undamaged)",
    },
    "baselines": {
        "a_hamada_documented_seeds": "PREREG_STEP3_EATON H30 run (I0 seeds, 13 m/s from 20 deg); "
                                     "P in {0, 1}",
        "b_windward_edge": "upwind 5 % of units seeded + Hamada H30 (step-3 I2); P in {0, 1}",
        "c_chen_mcaneney": "P = max(0, 0.6 (1 - d / 700 m)), d = distance from the wildland "
                           "edge = max(0, centroid-cell distance to a source cell - half size - "
                           "half cell - 50 m)",
        "d_distance_band": "count-matched: the N units with the smallest d (N = chain's count at "
                           "P >= 0.5); P in {0, 1}",
        "e_random": "P = observed destroyed rate in the scored units (climatology)",
    },
    "decision_rule": "Worth showing only if on Eaton BSS(chain vs c) > 0 AND kappa(chain at P >= "
                     "0.5) - kappa(b) > 0 AND kappa(chain) - kappa(d) > 0, each with the 95 % "
                     "block-bootstrap interval excluding zero. Camp is reported, never decides. "
                     "No configuration may be promoted to primary after scoring.",
    "variants": {
        "VB": "chain + vegetation-bridged cutoff (Eaton CHM; Camp NAIP pseudo-CHM), 50 members",
        "ROOF_s{0.05,0.15,0.30}": "chain + roof scenario (p_b x 1/0.375 on assigned units; "
                                  "assignment seed = member seed), 50 members",
        "DIRECT": "ember ignitions only (no Hamada), P = 1 - exp(-sum h) analytically and by members",
    },
    "sensitivity_20_members": {
        "p_building": [0.01, 0.001, 0.0001],
        "n_per_kg": [LR.N_PER_KG_THOMPSON, LR.N_PER_KG_LOW],
        "k_glowing": [1.0, 0.1],
        "tail": ["q99", "x2"],
        "sigma_y_factor": [2.0],
        "f_surface": [1.0],
        "proxy_fuel_alt": True,
        "fwi_dmc60_dc400": True,
    },
    "expectation_from_a_model_only_dry_run": "Eaton, 3 members, random outcomes (the status "
        "file not read), 2026-10-11: C4 proxy HFI 93-141 MW/m, CFB 1, Albini 2012 cap ~1.3 km, "
        "Sardoy median ~660 m, 74 % of emitted embers land inside the cap; 4,404 ha of wildland "
        "source; 324 front-contact units; 7,458-8,816 of 13,545 units ember-ignited and "
        "8,743-10,237 involved per member. The per-building probability saturates inside the "
        "cap: p_b down to 1e-4, n_kg and k_g barely change the counts, so the chain behaves "
        "like a downwind distance band of about the cap width; the q99 and x2 tails involve "
        "almost every unit. The roof scenario changes almost nothing. ~45 s per member with "
        "the sensitivities, ~9 s without; peak RSS 1.17 GB. Camp, 3 members: 53,244 ha of source "
        "(the building-free forest inside and around Paradise), 3,922 front-contact units; the "
        "C7 proxy does not torch below ~6-7 m/s (no long-range ignition in those members) and "
        "torches/crowns above (10,505 ember-ignited at 8.6 m/s); peak RSS 2.42 GB (canopy).",
    "disclosure": "Seen before fixing this block: the DINS data report (aggregate damage counts, "
                  "roof table), the step-3 Eaton/Camp/Jasper results (Hamada kappa ~ -0.02 at "
                  "Eaton, windward-edge seeding 0.16-0.35, Camp 0.002-0.010, Jasper 0.47), and "
                  "the literature report's expectation (modest kappa, risk of not beating a "
                  "distance baseline). No value of the chain came from Eaton, Camp or Jasper; "
                  "the proxy fuels, progression rule, wildland mask and FWI values are [H] fixed "
                  "here without outcomes. A dry run with random outcomes (--dry-run; the status "
                  "file is not read) is allowed before scoring for timing and memory only.",
}

FIRES = {
    "eaton": {
        "perimeter": "raw/frap_perimeter_eaton.geojson", "footprints": "derived/ms_usv2_eaton.geojsonl",
        "worldcover": "raw/worldcover2021_eaton.tif", "chm": "raw/meta_chm_eaton.tif",
        "h": 20.0, "window": 690.0, "origin": (34.19, -118.09), "t0": -19.0,
        "anchors_lng": [(-118.12, 210.0), (-118.14, 468.0)],
        "wind_speed": (9.0, 18.0), "wind_from": (10.0, 30.0), "proxy": "C4", "proxy_alt": "C2",
        "lat": 34.2, "elev": 500.0, "doy": 7, "ffmc": None, "dmc": 100.0, "dc": 800.0,
        "step3_seeds": S3.PREREG_STEP3_EATON["run_config"],
    },
    "camp": {
        "perimeter": "raw/frap_perimeter_camp.geojson", "footprints": None, "worldcover": None,
        "chm": "derived/naip2018_pseudochm_camp.tif", "square_m2": 169.0,
        "h": 40.0, "window": 480.0, "origin": (39.815, -121.434), "t0": -70.0,
        "axis_deg": 225.0, "anchors_axis": [((39.757, -121.571), 97.0), ((39.76, -121.62), 150.0)],
        "wind_speed": (5.0, 12.0), "wind_from": (35.0, 55.0), "proxy": "C7", "proxy_alt": "C4",
        "lat": 39.8, "elev": 600.0, "doy": 312, "ffmc": 92.0, "dmc": 100.0, "dc": 800.0,
        "step3_seeds": S3.PREREG_STEP3_CAMP["run_config"],
    },
}
WILDLAND_MIN_DIST_M = 50.0
WILDLAND_MIN_PATCH_HA = 25.0
FRONT_CONTACT_M = 60.0
BOOT = {"block_m": 250.0, "resamples": 2000, "seed": 20261011}


def rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


# ---------------------------------------------------------------------------------- inputs


def eaton_ffmc() -> float:
    """Mean hourly FFMC over the window from San Rafael Hills RAWS (pre-registered rule)."""
    path = glob.glob(str(CALFIRE_DIR / "refs" / "eaton" / "raws_FEMS*.csv"))[0]
    rows = [r for r in csv.DictReader(open(path)) if r["StationName"].startswith("SAN RAFAEL")]
    rows.sort(key=lambda r: r["DateTime"])
    f, vals = 85.0, []
    for r in rows:
        if r["DateTime"] > "2025-01-08T14:00":
            break
        t = (float(r["Temperature(F)"]) - 32.0) * 5.0 / 9.0
        f = hourly_ffmc(t, float(r["RelativeHumidity(%)"]), float(r["WindSpeed(mph)"]) * 1.609,
                        float(r["Precipitation(in)"]) * 25.4, f)
        if r["DateTime"] >= "2025-01-08T03:00":  # 19:00 PST onward (hour ending)
            vals.append(f)
    return float(np.mean(vals))


def load_units(fire: str, cfg: dict):
    import geopandas as gpd

    g = gpd.read_file(CALFIRE_DIR / "derived" / f"{fire}_units.gpkg", columns=["unit_id"])
    ids = g.unit_id.to_numpy(dtype=np.int64)
    if (g.geometry.geom_type == "Point").all():
        geoms = S3._squares(g.geometry.x.to_numpy(), g.geometry.y.to_numpy(), cfg["square_m2"])
    else:
        g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        ids = g.unit_id.to_numpy(dtype=np.int64)
        geoms = np.asarray(g.geometry.values, dtype=object)
    return ids, geoms


def outcomes(fire: str, ids, dry_run: bool):
    if dry_run:
        rng = np.random.default_rng(1)
        st = rng.choice(["Destroyed (>50%)", "No Damage", "UNSURVEYED"], size=len(ids), p=[0.5, 0.47, 0.03])
    else:
        status = {}
        with open(CALFIRE_DIR / "derived" / f"{fire}_units_status.csv", newline="") as f:
            for row in csv.DictReader(f):
                status[int(row["unit_id"])] = row["DAMAGE"]
        st = np.array([status.get(int(i), "UNSURVEYED") for i in ids])
    scored = ~np.isin(st, ["UNSURVEYED", "Inaccessible"])
    return st == "Destroyed (>50%)", scored


def wildland_mask(fire: str, cfg: dict, frame, unit_geoms_local, raster: LR.Raster):
    """Source cells (bool raster) per the pre-registered rule, and each cell's centre."""
    import shapely
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy import ndimage

    h = raster.h
    tr = from_origin(raster.x0, raster.y_top, h, h)
    perim = json.load(open(CALFIRE_DIR / cfg["perimeter"]))
    polys = [shapely.geometry.shape(f["geometry"]) for f in perim["features"]]
    per = shapely.union_all(polys)
    per_local = shapely.transform(per, lambda c: np.column_stack(frame.to_local(c[:, 1], c[:, 0])))
    inside = rasterize([(per_local, 1)], out_shape=(raster.rows, raster.cols), transform=tr,
                       fill=0, dtype="uint8").astype(bool)
    # buildings: all footprints (Eaton: every Microsoft footprint; Camp: the DINS squares)
    if cfg["footprints"]:
        bl = []
        x0, y0 = raster.x0, raster.y_top - raster.rows * h
        x1, y1 = raster.x0 + raster.cols * h, raster.y_top
        with open(CALFIRE_DIR / cfg["footprints"]) as f:
            for line in f:
                g = shapely.geometry.shape(json.loads(line)["geometry"])
                c = g.centroid
                lx, ly = frame.to_local(c.y, c.x)
                if x0 - 100 <= lx <= x1 + 100 and y0 - 100 <= ly <= y1 + 100:
                    bl.append(shapely.transform(g, lambda c: np.column_stack(frame.to_local(c[:, 1], c[:, 0]))))
        bgeoms = bl
    else:
        bgeoms = list(unit_geoms_local)
    bmask = rasterize(((g, 1) for g in bgeoms), out_shape=(raster.rows, raster.cols), transform=tr,
                      fill=0, dtype="uint8", all_touched=True).astype(bool)
    dist = ndimage.distance_transform_edt(~bmask) * h
    m = inside & (dist >= WILDLAND_MIN_DIST_M)
    if cfg["worldcover"]:
        import rasterio
        from rasterio.warp import Resampling, reproject

        wc = np.zeros((raster.rows, raster.cols), dtype=np.uint8)
        # local frame -> lng/lat affine (small area, linear)
        lat_top = frame.lat0 + raster.y_top / 111320.0
        lng_left = frame.lng0 + raster.x0 / frame.m_per_deg_lng
        dst_tr = from_origin(lng_left, lat_top, h / frame.m_per_deg_lng, h / 111320.0)
        with rasterio.open(CALFIRE_DIR / cfg["worldcover"]) as s:
            reproject(s.read(1), wc, src_transform=s.transform, src_crs=s.crs, dst_transform=dst_tr,
                      dst_crs=s.crs, resampling=Resampling.nearest)
        m &= np.isin(wc, [10, 20, 30])
    lab, n = ndimage.label(m, structure=np.ones((3, 3)))
    if n:
        sizes = np.bincount(lab.ravel())
        big = sizes * h * h / 1e4 >= WILDLAND_MIN_PATCH_HA
        big[0] = False
        m = big[lab]
    return m, len(bgeoms)


def out_of_domain_units(fire: str, cfg: dict, frame):
    """Microsoft footprints within 1 km outside the DINS domain (Eaton only), as units."""
    import shapely

    if not cfg["footprints"]:
        return None
    dom = json.load(open(CALFIRE_DIR / "derived" / f"{fire}_domain.geojson"))
    d = shapely.union_all([shapely.geometry.shape(f["geometry"]) for f in dom["features"]])
    d_local = shapely.transform(d, lambda c: np.column_stack(frame.to_local(c[:, 1], c[:, 0])))
    ring = shapely.difference(shapely.buffer(d_local, 1000.0), d_local)
    shapely.prepare(ring)
    out = []
    with open(CALFIRE_DIR / cfg["footprints"]) as f:
        for line in f:
            g = shapely.geometry.shape(json.loads(line)["geometry"])
            c = g.centroid
            lx, ly = frame.to_local(c.y, c.x)
            if ring.contains(shapely.Point(float(lx), float(ly))):
                out.append(g)
    return build_units(out, neighbour_cutoff_m=30.0, frame=frame) if out else None


def burn_times(fire: str, cfg: dict, frame, xs, ys) -> np.ndarray:
    """Imposed burn minute of each source cell (pre-registered progression rule)."""
    olat, olng = cfg["origin"]
    ox, oy = frame.to_local(olat, olng)
    if fire == "eaton":
        s = -(np.asarray(xs) - float(ox))  # westward metres
        pts = [(0.0, cfg["t0"])] + [(-(float(frame.to_local(olat, lng)[0]) - float(ox)), t)
                                     for lng, t in cfg["anchors_lng"]]
    else:
        to = math.radians(cfg["axis_deg"])
        ux, uy = math.sin(to), math.cos(to)
        s = (np.asarray(xs) - float(ox)) * ux + (np.asarray(ys) - float(oy)) * uy
        pts = [(0.0, cfg["t0"])]
        for (la, ln), t in cfg["anchors_axis"]:
            ax, ay = frame.to_local(la, ln)
            pts.append(((float(ax) - float(ox)) * ux + (float(ay) - float(oy)) * uy, t))
    d = np.array([p[0] for p in pts])
    t = np.array([p[1] for p in pts])
    r0 = (d[1] - d[0]) / (t[1] - t[0])
    rl = (d[-1] - d[-2]) / (t[-1] - t[-2])
    out = np.interp(s, d, t)
    out = np.where(s > d[-1], t[-1] + (s - d[-1]) / rl, out)
    out = np.where(s < 0, t[0] + np.abs(s) / r0, out)
    return out


# ---------------------------------------------------------------------------------- scoring


def blocks_of(x, y):
    bx = np.floor(np.asarray(x) / BOOT["block_m"]).astype(int)
    by = np.floor(np.asarray(y) / BOOT["block_m"]).astype(int)
    _, b = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    return b.ravel()


def brier(p, o):
    return float(np.mean((np.asarray(p, float) - o) ** 2))


def logscore(p, o, eps):
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    return float(np.mean(np.where(o, np.log(p), np.log(1 - p))))


def auc(p, o):
    from scipy.stats import rankdata

    p = np.asarray(p, float)
    r = rankdata(p)
    n1, n0 = int(o.sum()), int((~o).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((r[o].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def reliability(p, o, bins=10):
    p = np.asarray(p, float)
    k = np.minimum((p * bins).astype(int), bins - 1)
    return [{"bin": f"{b / bins:.1f}-{(b + 1) / bins:.1f}", "n": int(np.sum(k == b)),
             "mean_p": float(np.mean(p[k == b])) if np.any(k == b) else None,
             "observed": float(np.mean(o[k == b])) if np.any(k == b) else None} for b in range(bins)]


def boot_diff(blocks, fn, a, b, o):
    """Point and 95 % block-bootstrap interval of fn(a, o) - fn(b, o)."""
    nb = int(blocks.max()) + 1
    members = [np.nonzero(blocks == i)[0] for i in range(nb)]
    rng = np.random.default_rng(BOOT["seed"])
    d = []
    for _ in range(BOOT["resamples"]):
        idx = np.concatenate([members[i] for i in rng.integers(0, nb, nb)])
        d.append(fn(a[idx], o[idx]) - fn(b[idx], o[idx]))
    d = np.asarray(d)
    return {"point": float(fn(a, o) - fn(b, o)), "p2.5": float(np.nanpercentile(d, 2.5)),
            "p97.5": float(np.nanpercentile(d, 97.5)), "blocks": nb}


def prob_scores(p, o, eps):
    p = np.asarray(p, float)
    n_obs = int(o.sum())
    top = np.zeros(len(p), bool)
    top[np.argsort(-p, kind="stable")[:n_obs]] = True
    clim = float(o.mean())
    return {"brier": brier(p, o), "bss_vs_climatology": 1 - brier(p, o) / brier(np.full(len(o), clim), o),
            "log_score": logscore(p, o, eps), "auc": auc(p, o), "expected_count": float(p.sum()),
            "observed": n_obs, "at_0.5": J.score(p >= 0.5, o), "count_matched": J.score(top, o),
            "reliability": reliability(p, o)}


# ---------------------------------------------------------------------------------- the run


def member_wind(cfg, m):
    rng = np.random.default_rng([20261011, 1000 + m])
    return float(rng.uniform(*cfg["wind_speed"])), float(rng.uniform(*cfg["wind_from"]))


def run(args) -> None:
    S3._guard_out(args.out)
    fire = args.fire
    cfg = dict(FIRES[fire])
    t_start = time.time()
    ids, geoms = load_units(fire, cfg)
    obs, scored = outcomes(fire, ids, args.dry_run)
    u30 = build_units(geoms, neighbour_cutoff_m=30.0, ids=ids)
    u45 = build_units(geoms, neighbour_cutoff_m=45.0, ids=ids)
    assert len(u30) == len(geoms) == len(u45)
    frame = u30.frame
    h = cfg["h"]
    if cfg["ffmc"] is None:
        cfg["ffmc"] = eaton_ffmc()
    fmc = calculate_foliar_moisture(cfg["lat"], cfg["origin"][1], cfg["elev"], cfg["doy"])
    # raster: units' box + the largest cap at the strongest wind + 1 km
    f_max = calculate_fbp(cfg["proxy"], cfg["wind_speed"][1] * 3.6, cfg["ffmc"], cfg["dmc"], cfg["dc"], fmc=fmc)
    kind_max = "crown" if f_max.cfb >= LR.CFB_CROWNING else "torch"
    pad = 2.0 * LR.cap_distance(kind_max, f_max.hfi, f_max.cfb, cfg["wind_speed"][1], cfg["proxy"]) + 1000.0
    raster = LR.Raster.around(u30.x, u30.y, pad, h)
    wl, n_buildings_mask = wildland_mask(fire, cfg, frame, u30.footprints, raster)
    rr, cc = np.nonzero(wl)
    sx = raster.x0 + (cc + 0.5) * h
    sy = raster.y_top - (rr + 0.5) * h
    t_burn = burn_times(fire, cfg, frame, sx, sy)
    print(f"{fire}: units {len(ids)} scored {int(scored.sum())} | wildland cells {len(sx)} "
          f"({len(sx) * h * h / 1e4:.0f} ha) | FFMC {cfg['ffmc']:.1f} FMC {fmc:.0f} | raster "
          f"{raster.rows}x{raster.cols} | rss {rss_gb():.2f} GB", flush=True)

    # distance from each unit to the wildland (edge distance, front contact)
    from scipy import ndimage

    dist_px, (ir, ic) = ndimage.distance_transform_edt(~wl, return_indices=True)
    ur, uc, ok = raster.index(u30.x, u30.y)
    d_cell = np.where(ok, dist_px[np.clip(ur, 0, raster.rows - 1), np.clip(uc, 0, raster.cols - 1)] * h, np.inf)
    d_edge_raw = np.maximum(d_cell - u30.size_m / 2.0 - h / 2.0, 0.0)
    t_grid = np.full(wl.shape, np.inf)
    t_grid[rr, cc] = t_burn
    nearest_t = np.where(ok, t_grid[ir[np.clip(ur, 0, raster.rows - 1), np.clip(uc, 0, raster.cols - 1)],
                                    ic[np.clip(ur, 0, raster.rows - 1), np.clip(uc, 0, raster.cols - 1)]], np.inf)
    t_front = np.where(d_edge_raw <= FRONT_CONTACT_M, np.maximum(nearest_t, 0.0), np.inf)
    t_front[t_front > cfg["window"]] = np.inf
    d_edge = np.maximum(d_edge_raw - WILDLAND_MIN_DIST_M, 0.0)

    window = cfg["window"]
    d66 = burnout_minutes(150)
    chm = CALFIRE_DIR / cfg["chm"]
    canopy = S3.canopy_for(geoms, chm)
    links = V.BridgedLinks(u45, canopy)
    keys = R.building_keys(u30)
    eps = 1.0 / (2 * 50)

    def sources_for(proxy, u_ms, wfrom, dmc, dc):
        f = calculate_fbp(proxy, u_ms * 3.6, cfg["ffmc"], dmc, dc, fmc=fmc)
        n = len(sx)
        cross = h / max(f.ros_final, 1e-3)
        return LR.EmberSourceCells(sx, sy, t_burn, t_burn + cross, np.full(n, f.hfi), np.full(n, f.sfc),
                                   np.full(n, f.cfc), np.full(n, f.cfb), np.full(n, proxy, dtype=object), h), f

    def wind_list(u_ms, wfrom):
        return [(0.0, u_ms * 3.6, wfrom)]

    def spread30(u_ms, wfrom, link=False):
        w = [WindPeriod(0.0, u_ms * 3.6, wfrom)]
        if link:
            return lambda t0: hamada_spread(u45, t0, w, duration_min=window, burnout_min=d66, link_filter=links)
        return lambda t0: hamada_spread(u30, t0, w, duration_min=window, burnout_min=d66)

    def no_spread(t0):
        from types import SimpleNamespace

        return SimpleNamespace(t_min=np.where(t0 <= window, t0, np.inf),
                               source=np.where(np.isfinite(t0), 1, 0))

    u_ood = out_of_domain_units(fire, cfg, frame)
    ood = []
    involved = {}  # variant -> (members, n) bool
    p_direct = {}
    meta = {"members": []}
    variants_main = ["PRIMARY", "VB", "ROOF_s0.05", "ROOF_s0.15", "ROOF_s0.3", "DIRECT"]
    sens = {f"p_building_{v:g}": ("scale", LR.P_BUILDING and v / LR.P_BUILDING) for v in PREREG_LR["sensitivity_20_members"]["p_building"]}
    sens.update({f"n_per_kg_{v:g}": ("scale", v / LR.N_PER_KG) for v in PREREG_LR["sensitivity_20_members"]["n_per_kg"]})
    sens.update({f"k_glowing_{v:g}": ("scale", v / LR.K_GLOWING) for v in PREREG_LR["sensitivity_20_members"]["k_glowing"]})
    sens.update({"tail_q99": ("opts", {"tail": "q99"}), "tail_x2": ("opts", {"tail": "x2"}),
                 "sigma_y_x2": ("opts", {"sigma_y_factor": 2.0}), "f_surface_1": ("opts", {"f_surface": 1.0}),
                 f"proxy_{cfg['proxy_alt']}": ("proxy", cfg["proxy_alt"]), "fwi_dmc60_dc400": ("fwi", (60.0, 400.0))})
    n_main = 50 if not args.quick else 3
    n_sens = 20 if not args.quick else 2
    for m in range(n_main):
        tm = time.time()
        u_ms, wfrom = member_wind(cfg, m)
        src, f = sources_for(cfg["proxy"], u_ms, wfrom, cfg["dmc"], cfg["dc"])
        opts = LR.LongRangeOptions(members=1, seed=20261011, raster_m=h)
        fld = LR.landing_field(src, wind_list(u_ms, wfrom), window, opts, raster=raster)
        haz = LR.building_hazard(u30, fld, opts, cfg["ffmc"])

        def one(name, hz, spread_fn, roof=None):
            o1 = LR.LongRangeOptions(members=1, seed=20261011, raster_m=h)
            rng = np.random.default_rng([20261011, m])
            t_ign = LR.draw_ignition_times(hz, fld.edges, rng) + o1.delay_s / 60.0
            t_ign[t_ign > window] = np.inf
            res = spread_fn(np.minimum(t_front, t_ign))
            inv = np.asarray(res.t_min) <= window
            involved.setdefault(name, []).append(inv)
            return inv, t_ign

        inv, t_ign = one("PRIMARY", haz, spread30(u_ms, wfrom))
        p_direct.setdefault("PRIMARY", []).append(1.0 - np.exp(-haz.sum(axis=0)))
        if u_ood is not None:
            ood.append(float(np.sum(1.0 - np.exp(-LR.building_hazard(u_ood, fld, opts, cfg["ffmc"]).sum(axis=0)))))
        one("VB", haz, spread30(u_ms, wfrom, link=True))
        for s in (0.05, 0.15, 0.30):
            roof = R.assign_combustible_roofs(keys, s, 20261011 + m)
            hz = LR.building_hazard(u30, fld, opts, cfg["ffmc"], combustible_roof=roof)
            one(f"ROOF_s{s:g}", hz, spread30(u_ms, wfrom))
        one("DIRECT", haz, no_spread)
        meta["members"].append({"m": m, "wind_ms": round(u_ms, 2), "wind_from": round(wfrom, 1),
                                "hfi_kw_m": round(f.hfi), "cfb": round(f.cfb, 3), "ros": round(f.ros_final, 1),
                                "sfc": round(f.sfc, 2), "cfc": round(f.cfc, 2),
                                "classes": fld.classes, "emitted": fld.emitted_total,
                                "landed_share": fld.landed_total / max(fld.emitted_total, 1.0),
                                "front_contact": int(np.isfinite(t_front).sum()),
                                "ember_ignited": int(np.isfinite(t_ign).sum()),
                                "involved_primary": int(inv.sum())})
        if m < n_sens:
            for name, (kind, val) in sens.items():
                if kind == "scale":
                    one(name, haz * val, spread30(u_ms, wfrom))
                elif kind == "opts":
                    o2 = LR.LongRangeOptions(members=1, seed=20261011, raster_m=h, **val)
                    s2 = src
                    fl2 = LR.landing_field(s2, wind_list(u_ms, wfrom), window, o2, raster=raster)
                    one(name, LR.building_hazard(u30, fl2, o2, cfg["ffmc"]), spread30(u_ms, wfrom))
                else:
                    if kind == "proxy":
                        s2, _ = sources_for(val, u_ms, wfrom, cfg["dmc"], cfg["dc"])
                    else:
                        s2, _ = sources_for(cfg["proxy"], u_ms, wfrom, *val)
                    fl2 = LR.landing_field(s2, wind_list(u_ms, wfrom), window, opts, raster=raster)
                    one(name, LR.building_hazard(u30, fl2, opts, cfg["ffmc"]), spread30(u_ms, wfrom))
        print(f"member {m:2d} wind {u_ms:5.1f} m/s from {wfrom:4.1f} hfi {f.hfi:7.0f} cfb {f.cfb:.2f} "
              f"front {int(np.isfinite(t_front).sum())} ember {int(np.isfinite(t_ign).sum())} "
              f"involved {int(inv.sum())} | {time.time() - tm:.1f} s rss {rss_gb():.2f} GB", flush=True)

    P = {k: np.mean(np.asarray(v), axis=0) for k, v in involved.items()}
    Pd = float(np.mean(np.asarray(p_direct["PRIMARY"]), axis=0).mean())

    # baselines
    o, sm = obs, scored
    w = cfg["step3_seeds"]
    wind3 = [WindPeriod(0.0, float(w["wind_speed_kmh"]), float(w["wind_from_deg"]))]
    _, seed_t = S3._seed_mask_times(u30, w)
    pa = (hamada_spread(u30, seed_t, wind3, duration_min=window, burnout_min=d66).t_min <= window).astype(float)
    band = S3._windward(u30, 0.05, float(w["wind_from_deg"]))
    pb = (hamada_spread(u30, np.where(band, 0.0, np.inf), wind3, duration_min=window,
                        burnout_min=d66).t_min <= window).astype(float)
    pc = np.maximum(0.0, 0.6 * (1.0 - d_edge / 700.0))
    n_chain = int(np.sum(P["PRIMARY"][sm] >= 0.5))
    idx = np.nonzero(sm)[0]
    order = idx[np.lexsort((idx, d_edge[idx]))]
    pdist = np.zeros(len(o))
    pdist[order[:n_chain]] = 1.0
    pe_ = np.full(len(o), float(o[sm].mean()))
    base = {"a_hamada_documented_seeds": pa, "b_windward_edge": pb, "c_chen_mcaneney": pc,
            "d_distance_band": pdist, "e_random": pe_}

    blk = blocks_of(u30.x[sm], u30.y[sm])
    os_ = o[sm]
    res = {"fire": fire, "dry_run": bool(args.dry_run), "prereg": PREREG_LR, "config": {
        k: v for k, v in cfg.items() if k != "step3_seeds"}, "fmc": fmc,
        "units": len(ids), "units_scored": int(sm.sum()), "observed_destroyed": int(os_.sum()),
        "wildland_cells": int(len(sx)), "wildland_ha": float(len(sx) * h * h / 1e4),
        "buildings_in_mask": n_buildings_mask, "front_contact_units": int(np.isfinite(t_front).sum()),
        "p_direct_mean": Pd,
        "out_of_domain": ({"footprints": len(u_ood), "expected_ember_ignitions": float(np.mean(ood))}
                          if u_ood is not None else None), "members_meta": meta["members"], "scores": {}, "baselines": {},
        "tests": {}, "distance_bands": []}
    for k, p in P.items():
        res["scores"][k] = prob_scores(p[sm], os_, eps)
    for k, p in base.items():
        res["baselines"][k] = prob_scores(p[sm], os_, eps)
    pp = P["PRIMARY"][sm]
    kap = lambda p_, o_: J.kappa_of(p_ >= 0.5, o_)  # noqa: E731
    bri = lambda p_, o_: -brier(p_, o_)  # noqa: E731  (positive = chain better)
    res["tests"] = {
        "BSS_vs_c (Brier_c - Brier_chain)": boot_diff(blk, bri, pp, pc[sm], os_),
        "kappa_chain_minus_b": boot_diff(blk, kap, pp, pb[sm], os_),
        "kappa_chain_minus_d": boot_diff(blk, kap, pp, pdist[sm], os_),
        "kappa_chain_minus_a": boot_diff(blk, kap, pp, pa[sm], os_),
        "VB_minus_PRIMARY_kappa": boot_diff(blk, kap, P["VB"][sm], pp, os_),
        "VB_minus_PRIMARY_brier": boot_diff(blk, bri, P["VB"][sm], pp, os_),
    }
    bss_c = 1.0 - brier(pp, os_) / brier(pc[sm], os_)
    t = res["tests"]
    res["bss_vs_c"] = bss_c
    res["decision"] = {
        "bss_vs_c_positive_ci": t["BSS_vs_c (Brier_c - Brier_chain)"]["p2.5"] > 0,
        "kappa_vs_b_positive_ci": t["kappa_chain_minus_b"]["p2.5"] > 0,
        "kappa_vs_d_positive_ci": t["kappa_chain_minus_d"]["p2.5"] > 0}
    res["decision"]["worth_showing"] = all(res["decision"].values())
    for lo, hi in ((0, 100), (100, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 1e9)):
        b = sm & (d_edge >= lo) & (d_edge < hi)
        res["distance_bands"].append({"band_m": f"{lo}-{hi:g}", "units": int(b.sum()),
                                      "observed_rate": float(o[b].mean()) if b.any() else None,
                                      "mean_P_chain": float(P["PRIMARY"][b].mean()) if b.any() else None,
                                      "mean_P_c": float(pc[b].mean()) if b.any() else None})
    res["seconds"] = time.time() - t_start
    res["max_rss_gb"] = rss_gb()
    tag = "_DRYRUN" if args.dry_run else ""
    (args.out / f"results_long_range_{fire}{tag}.json").write_text(json.dumps(res, indent=2, default=float))
    (args.out / f"tables_long_range_{fire}{tag}.md").write_text(tables_md(res))
    print(f"wrote {args.out} in {res['seconds'] / 60:.1f} min, max rss {res['max_rss_gb']:.2f} GB")


def tables_md(r: dict) -> str:
    L = [f"# Long-range ember chain: {r['fire']}{' (DRY RUN, random outcomes)' if r['dry_run'] else ''}",
         "", "Illustrative — not validated in Canada. Aggregate tables only.", "",
         f"Units scored {r['units_scored']}, observed destroyed {r['observed_destroyed']}; wildland "
         f"source {r['wildland_ha']:.0f} ha; front-contact units {r['front_contact_units']}.",
         f"Out-of-domain false-alarm tally: {r['out_of_domain']}", "",
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
    L += ["", f"Decision: {r['decision']}", "", "## Distance from the wildland edge", "",
          "| Band (m) | Units | Observed | Mean P chain | Mean P (c) |", "|---|---|---|---|---|"]
    for b in r["distance_bands"]:
        f = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
        L.append(f"| {b['band_m']} | {b['units']} | {f(b['observed_rate'])} | {f(b['mean_P_chain'])} | {f(b['mean_P_c'])} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("run")
    a.add_argument("--fire", default="eaton", choices=sorted(FIRES))
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--dry-run", action="store_true", help="random outcomes; the status file is never read")
    a.add_argument("--quick", action="store_true", help="3 members (timing check only)")
    sub.add_parser("prereg")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args)
    else:
        print(json.dumps(PREREG_LR, indent=2, default=float))
        print(hashlib.sha256(json.dumps(PREREG_LR, sort_keys=True, default=float).encode()).hexdigest())


if __name__ == "__main__":
    main()
