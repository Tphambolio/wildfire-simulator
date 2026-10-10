"""Validation: Edmonton open-data grid vs the City LiDAR grid, the CFS 2026 baseline, St. Albert.

Writes ``FUELGRID_DATA/validation/validation.json`` (all numbers), ``*.md`` tables, PNG maps and the
St. Albert ground-truth site list. Cells are compared where both grids have data inside the
Edmonton boundary. Agreement with the LiDAR grid is not accuracy: the LiDAR grid has never been
field-checked, and part of the disagreement is by design (the stand rule, see the docs).
"""

from __future__ import annotations

import csv
import json
import logging

import numpy as np
from rasterio.features import rasterize
from scipy import ndimage as ndi

try:
    from . import config as C
    from . import labels as L
    from . import rules as R
    from .build import WORK, key_inputs, read_ref
    from .grid import Grid, read_tif
    from .sources import aoi_grid, boundary_geom
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    import labels as L  # type: ignore[no-redef]
    import rules as R  # type: ignore[no-redef]
    from build import WORK, key_inputs, read_ref  # type: ignore[no-redef]
    from grid import Grid, read_tif  # type: ignore[no-redef]
    from sources import aoi_grid, boundary_geom  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.validate")
HA = C.CELL * C.CELL / 1e4
REF_GAP_NORTHING = 5914940.0  # EPSG:3776; see validate_edmonton

# Comparison classes
G_CON, G_DEC, G_MIX, G_GRASS, G_NF, G_CODE13 = "Conifer", "Deciduous", "Mixedwood", "Grass", "Non-fuel", "Code 13"
GROUPS = [G_CON, G_DEC, G_MIX, G_GRASS, G_NF]
FINE = ["C-2", "D-2", "M-2", "O-1a", "O-1b", "NF"]

REF_FINE = {2: "C-2", 12: "D-2", 14: "M-2", 31: "O-1a", 32: "O-1b", 99: "NF"}
OPEN_FINE = {C.C2: "C-2", C.D2: "D-2", C.M2: "M-2", C.O1A: "O-1a", C.O1B: "O-1b", C.NF: "NF", C.WATER: "NF",
             C.D1: "D-2", C.M1: "M-2"}
FINE_GROUP = {"C-2": G_CON, "D-2": G_DEC, "M-2": G_MIX, "O-1a": G_GRASS, "O-1b": G_GRASS, "NF": G_NF}


def cfs_group(code: np.ndarray, code13: str | None) -> np.ndarray:
    """CFS 2026 codes -> comparison groups. code13: None (separate), or a group name."""
    out = np.full(code.shape, "", object)
    out[(code >= 1) & (code <= 7)] = G_CON
    out[np.isin(code, [11, 12])] = G_DEC
    out[code == 13] = G_CODE13 if code13 is None else code13
    out[np.isin(code, [40, 50, 60]) | ((code >= 400) & (code < 700))] = G_MIX
    out[np.isin(code, [31, 32])] = G_GRASS
    out[np.isin(code, [21, 22, 23])] = G_CON
    out[(code >= 100) & (code < 200)] = G_NF
    return out


def cfs_fine(code: np.ndarray) -> np.ndarray:
    out = np.full(code.shape, "", object)
    out[(code >= 1) & (code <= 7)] = "C-2"
    out[np.isin(code, [11, 12, 13])] = "D-2"
    out[np.isin(code, [40, 50, 60]) | ((code >= 400) & (code < 700))] = "M-2"
    out[code == 31] = "O-1a"
    out[code == 32] = "O-1b"
    out[(code >= 100) & (code < 200)] = "NF"
    return out


def mapcodes(a: np.ndarray, table: dict) -> np.ndarray:
    out = np.full(a.shape, "", object)
    for k, v in table.items():
        out[a == k] = v
    return out


def compare(ref: np.ndarray, pred: np.ndarray, labels: list[str]) -> dict:
    """Agreement block for two label arrays (object dtype, '' = no data)."""
    m = (ref != "") & (pred != "")
    cm = _cm(ref[m], pred[m], labels)
    a = R.agreement(cm)
    return {"labels": labels, "confusion_cells": cm.tolist(),
            "confusion_ha": (cm * HA).round(1).tolist(), **a,
            "area_ref_ha": dict(zip(labels, (cm.sum(1) * HA).round(1).tolist())),
            "area_pred_ha": dict(zip(labels, (cm.sum(0) * HA).round(1).tolist()))}


def _cm(r: np.ndarray, p: np.ndarray, labels: list[str]) -> np.ndarray:
    idx = {k: i for i, k in enumerate(labels)}
    ri = np.array([idx.get(v, -1) for v in r])
    pi = np.array([idx.get(v, -1) for v in p])
    ok = (ri >= 0) & (pi >= 0)
    n = len(labels)
    return np.bincount(ri[ok] * n + pi[ok], minlength=n * n).reshape(n, n)


def full_comparison(ref_fine: np.ndarray, pred_fine: np.ndarray) -> dict:
    grp = lambda a: np.vectorize(lambda v: FINE_GROUP.get(v, ""), otypes=[object])(a)  # noqa: E731
    rg, pg = grp(ref_fine), grp(pred_fine)
    fuel_r = np.where(rg == "", "", np.where(rg == G_NF, "nonfuel", "fuel")).astype(object)
    fuel_p = np.where(pg == "", "", np.where(pg == G_NF, "nonfuel", "fuel")).astype(object)
    forest = np.isin(rg, [G_CON, G_DEC, G_MIX]) & np.isin(pg, [G_CON, G_DEC, G_MIX])
    return {
        "fine6": compare(ref_fine, pred_fine, FINE),
        "groups5": compare(rg, pg, GROUPS),
        "fuel_nonfuel": compare(fuel_r, fuel_p, ["fuel", "nonfuel"]),
        "forest_type_where_both_forest": compare(np.where(forest, rg, ""), np.where(forest, pg, ""),
                                                 [G_CON, G_DEC, G_MIX]),
        "forest_type_on_ref_forest": compare(np.where(np.isin(rg, [G_CON, G_DEC, G_MIX]), rg, ""),
                                             pg, GROUPS),
    }


def _summ(c: dict) -> dict:
    return {k: (round(v["oa"], 3), round(v["kappa"], 3)) for k, v in c.items()}


# ── Edmonton ─────────────────────────────────────────────────────────────────
def validate_edmonton() -> dict:
    eg = aoi_grid("edmonton")
    ref = read_ref(eg)
    work = np.load(WORK / "edmonton.npz")
    cls, stands = work["cls"], work["stands"]
    dom = (ref != 0) & (cls != C.NODATA)
    ref_f = np.where(dom, mapcodes(ref, REF_FINE), "").astype(object)
    res: dict = {"domain_ha": float(dom.sum() * HA),
                 "ref_inside_boundary_ha": float((ref != 0).sum() * HA)}

    open_f = np.where(dom, mapcodes(cls, OPEN_FINE), "").astype(object)
    res["open_vs_lidar"] = full_comparison(ref_f, open_f)
    for name in ("insample", "scanfi_rule"):
        v = np.load(WORK / f"edmonton_{name}.npz")["cls"]
        res[f"variant_{name}_vs_lidar"] = full_comparison(ref_f, np.where(dom, mapcodes(v, OPEN_FINE), "").astype(object))

    cfs = read_tif(C.REGION_DIR / "cfs_fbp_2026.tif", eg)
    cfs_f = np.where(dom, cfs_fine(cfs), "").astype(object)
    res["cfs_vs_lidar"] = full_comparison(ref_f, cfs_f)
    # audit-style 5 groups with code 13 excluded / counted as disagreement (2026-10-07 audit)
    rg = np.vectorize(lambda v: FINE_GROUP.get(v, ""), otypes=[object])(ref_f)
    cg = np.where(dom, cfs_group(cfs, None), "").astype(object)
    res["cfs_vs_lidar_groups5_code13_excluded"] = compare(rg, cg, GROUPS)
    cm6 = _cm(rg[(rg != "") & (cg != "")], cg[(rg != "") & (cg != "")], GROUPS + [G_CODE13])
    tot = cm6.sum()
    res["cfs_vs_lidar_groups5_code13_as_disagreement_oa"] = float(np.trace(cm6[:5, :5]) / tot)
    res["cfs_code13_ha"] = float(((cfs == 13) & dom).sum() * HA)
    # Same comparison for the open grid with the 5 groups, and open vs CFS
    og = np.vectorize(lambda v: FINE_GROUP.get(v, ""), otypes=[object])(open_f)
    res["open_vs_cfs_groups5"] = compare(np.where(dom, cfs_group(cfs, G_DEC), "").astype(object), og, GROUPS)

    # Spatial-block hold-out: grid agreement per fold of the out-of-fold grid
    var = np.load(WORK / "edmonton_conifer_variants.npz")
    folds = var["folds"]
    per_fold = []
    for k in range(C.PARAMS["n_folds"]):
        fm = folds == k
        c = full_comparison(np.where(fm, ref_f, "").astype(object), np.where(fm, open_f, "").astype(object))
        cc = full_comparison(np.where(fm, ref_f, "").astype(object), np.where(fm, cfs_f, "").astype(object))
        per_fold.append({"fold": k, "domain_ha": float((fm & dom).sum() * HA), "open": _summ(c), "cfs": _summ(cc)})
    res["per_fold"] = per_fold

    # Leaf-off areas, class areas
    res["areas_ha"] = {
        "lidar": {v: float(((ref == k) & dom).sum() * HA) for k, v in REF_FINE.items()},
        "open_leafon": {C.CLASS_NAMES[k]: float(((cls == k) & dom).sum() * HA)
                        for k in (C.C2, C.D2, C.M2, C.O1A, C.O1B, C.NF, C.WATER)},
        "open_leafoff": {C.CLASS_NAMES[k]: float(((R.leaf_off(cls) == k) & dom).sum() * HA)
                         for k in (C.C2, C.D1, C.M1, C.O1A, C.O1B, C.NF, C.WATER)},
        "cfs": {g: float(((cfs_group(cfs, None) == g) & dom).sum() * HA) for g in GROUPS + [G_CODE13]},
        "cfs_water_ha": float(((cfs == 102) & dom).sum() * HA),
    }
    res["rules_ha"] = {C.RULES[k]: float(((work["rule"] == k) & dom).sum() * HA) for k in C.RULES if k}

    # Contiguous-stand effect
    lab = L.load_labels()
    sm = ndi.uniform_filter(lab["cover"], 3)
    st = (sm >= 0.4) & (ref != 0)
    lb, n = ndi.label(st)
    sizes = np.bincount(lb.ravel())
    big = (sizes >= 25)[lb] & (lb > 0)
    forest_open = np.isin(cls, [C.C2, C.D2, C.M2])
    res["stand_effect"] = {
        "lidar_stands_ge1ha_ha": float(big.sum() * HA),
        "lidar_stands_ge1ha_on_lidar_nonfuel_ha": float((big & (ref == 99)).sum() * HA),
        "of_which_open_typed_forest_ha": float((big & (ref == 99) & forest_open).sum() * HA),
        "of_which_open_typed_O1b_ha": float((big & (ref == 99) & (cls == C.O1B)).sum() * HA),
        "open_stands_ha": float((stands & dom).sum() * HA),
        "open_forest_ha": float((forest_open & dom).sum() * HA),
        "open_forest_on_lidar_nonfuel_ha": float((forest_open & (ref == 99)).sum() * HA),
        "open_forest_on_lidar_grass_ha": float((forest_open & np.isin(ref, [31, 32])).sum() * HA),
        "lidar_forest_open_nonfuel_ha": float((np.isin(ref, [2, 12, 14]) & np.isin(cls, [C.NF, C.WATER])).sum() * HA),
        "lidar_forest_open_grass_ha": float((np.isin(ref, [2, 12, 14]) & np.isin(cls, [C.O1A, C.O1B])).sum() * HA),
    }
    # Why the open-data stand rule leaves most of the 1,331 ha out: built context or CHM cover
    x = key_inputs("edmonton", eg, np.zeros(eg.shape, np.float32))
    built = R.built_mask(x.bld_frac, x.wc[50])
    low = R.mean3x3(x.cover) < C.PARAMS["stand_cover"]
    gap = big & (ref == 99)
    res["stand_effect"].update({
        "gap_cells_built_context_ha": float((gap & built).sum() * HA),
        "gap_cells_not_built_but_chm_cover_below_40pct_ha": float((gap & ~built & low).sum() * HA),
        "gap_cells_not_built_chm_ok_ha": float((gap & ~built & ~low).sum() * HA),
    })
    # Reference coverage gap (post hoc, found on the map): south of northing REF_GAP_NORTHING
    # (about 41 Ave SW, the 2019 annexation) the LiDAR grid is almost all non-fuel while AAFC and
    # CFS both map mostly cropland. Reported as a sensitivity, not used to pick any parameter.
    rows_y = eg.y0 - (np.arange(eg.height) + 0.5) * eg.cell
    gapzone = (rows_y < REF_GAP_NORTHING)[:, None] & dom
    aci = read_tif(C.REGION_DIR / "aci2025.tif", eg)
    res["reference_gap_zone"] = {
        "northing_below": REF_GAP_NORTHING, "area_ha": float(gapzone.sum() * HA),
        "lidar_nonfuel_share": float((ref[gapzone] == 99).mean()),
        "aci_crop_share": float(np.isin(aci[gapzone], list(C.ACI_CROP | C.ACI_PASTURE)).mean()),
        "cfs_grass_share": float(np.isin(cfs[gapzone], [31, 32]).mean()),
        "open_O1a_share": float((cls[gapzone] == C.O1A).mean()),
    }
    keep = np.where(gapzone, "", ref_f).astype(object)
    res["open_vs_lidar_excl_gap_zone"] = full_comparison(keep, np.where(gapzone, "", open_f).astype(object))
    res["cfs_vs_lidar_excl_gap_zone"] = full_comparison(keep, np.where(gapzone, "", cfs_f).astype(object))
    # Canopy cover check: Meta CHM cover vs LiDAR crown cover
    chm = read_tif(C.REGION_DIR / "meta_chm.tif", eg)
    ok = dom & (chm[0] >= 0)
    for i, nm in ((0, "cover_ge5m"), (1, "cover_ge2m")):
        x, yv = chm[i][ok], lab["cover"][ok]
        res[f"chm_{nm}_vs_lidar_cover"] = {"r": float(np.corrcoef(x, yv)[0, 1]),
                                           "mean_chm": float(x.mean()), "mean_lidar": float(yv.mean()),
                                           "bias": float((x - yv).mean())}
    res["conifer_model"] = json.loads((C.VAL_DIR / "conifer_model.json").read_text())
    # Label noise: how well does a 3x3 mean of the LiDAR conifer fraction (a smoother version of
    # the same labels) reproduce the cell labels' C/M/D class? A rough ceiling for any 20 m model.
    tr, yl = var["train"], var["label"]
    ys = ndi.uniform_filter(np.where(tr, yl, 0), 3) / np.maximum(ndi.uniform_filter(tr.astype(float), 3), 1e-9)
    cm = R.confusion(R.forest_type(yl[tr]), R.forest_type(ys[tr]), [C.C2, C.M2, C.D2])
    res["label_noise_check"] = {"cmd_confusion": cm.tolist(), **R.agreement(cm)}
    return res


# ── St. Albert ───────────────────────────────────────────────────────────────
def validate_st_albert() -> dict:
    sg = aoi_grid("st_albert")
    work = np.load(WORK / "st_albert.npz")
    cls, cf, stands = work["cls"], work["conifer_frac"], work["stands"]
    buf = cls != C.NODATA
    inside = rasterize([(boundary_geom("st_albert"), 1)], out_shape=sg.shape, transform=sg.transform,
                       fill=0, dtype="uint8").astype(bool)
    cfs = read_tif(C.REGION_DIR / "cfs_fbp_2026.tif", sg)
    res = {"grid": {"x0": sg.x0, "y0": sg.y0, "width": sg.width, "height": sg.height},
           "boundary_ha": float(inside.sum() * HA), "with_buffer_ha": float(buf.sum() * HA)}
    for nm, m in (("inside_boundary", inside), ("with_buffer", buf)):
        res[f"areas_{nm}_ha"] = {
            "leafon": {C.CLASS_NAMES[k]: float(((cls == k) & m).sum() * HA) for k in (C.C2, C.D2, C.M2, C.O1A, C.O1B, C.NF, C.WATER)},
            "leafoff": {C.CLASS_NAMES[k]: float(((R.leaf_off(cls) == k) & m).sum() * HA) for k in (C.C2, C.D1, C.M1, C.O1A, C.O1B, C.NF, C.WATER)},
            "cfs": {g: float(((cfs_group(cfs, None) == g) & m).sum() * HA) for g in GROUPS + [G_CODE13]},
        }
    og = np.where(buf, np.vectorize(lambda v: FINE_GROUP.get(v, ""), otypes=[object])(mapcodes(cls, OPEN_FINE)), "").astype(object)
    cg = np.where(buf, cfs_group(cfs, G_DEC), "").astype(object)
    res["open_vs_cfs_groups5_with_buffer"] = compare(cg, og, GROUPS)
    res["open_vs_cfs_groups5_inside"] = compare(np.where(inside, cg, "").astype(object),
                                                np.where(inside, og, "").astype(object), GROUPS)
    res["rules_ha"] = {C.RULES[k]: float(((work["rule"] == k) & buf).sum() * HA) for k in C.RULES if k}
    res["sites"] = ground_truth_sites(sg, cls, cf, stands, og, cg, inside)
    return res


def ground_truth_sites(grid: Grid, cls, cf, stands, og, cg, inside, max_sites: int = 30) -> list[dict]:
    """Places where a field check would most change the grid: large disagreements with CFS,
    stands whose conifer fraction sits near a class threshold, and the largest stands."""
    from pyproj import Transformer
    tr = Transformer.from_crs(C.CRS, "EPSG:4326", always_xy=True)
    sites = []

    def centroid(mask):
        rr, cc = np.nonzero(mask)
        r, c = rr.mean(), cc.mean()
        # nearest member cell to the mean, so the point falls inside the patch
        i = np.argmin((rr - r) ** 2 + (cc - c) ** 2)
        x = grid.x0 + (cc[i] + 0.5) * grid.cell
        y = grid.y0 - (rr[i] + 0.5) * grid.cell
        lon, lat = tr.transform(x, y)
        return round(lat, 5), round(lon, 5)

    # 1. Disagreement patches (open-data vs CFS, 5 groups) >= 2 ha
    dis = (og != "") & (cg != "") & (og != cg)
    for (a, b) in sorted({(x, y) for x, y in zip(og[dis], cg[dis])}):
        m = dis & (og == a) & (cg == b)
        lb, n = ndi.label(m, structure=np.ones((3, 3), bool))
        if n == 0:
            continue
        sizes = np.bincount(lb.ravel())[1:]
        for j in np.argsort(-sizes)[:3]:
            if sizes[j] * HA < 2:
                break
            pm = lb == j + 1
            lat, lon = centroid(pm)
            sites.append({"type": "open-data vs CFS disagreement", "open_data": a, "cfs": b,
                          "area_ha": round(sizes[j] * HA, 1), "lat": lat, "lon": lon,
                          "inside_boundary": bool(inside[pm].mean() > 0.5),
                          "why": f"open-data says {a}, CFS 2026 says {b}"})
    # 2. Stands with conifer fraction near a threshold (0.75 C/M or 0.25 M/D), patch-mean
    lb, n = ndi.label(stands, structure=np.ones((3, 3), bool))
    treed = np.isin(cls, [C.C2, C.D2, C.M2])
    for j in range(1, n + 1):
        pm = (lb == j) & treed
        if pm.sum() * HA < 2:
            continue
        mcf = float(np.nanmean(cf[pm]))
        near = min(abs(mcf - C.PARAMS["conifer_c"]), abs(mcf - C.PARAMS["conifer_d"]))
        if near < 0.07:
            lat, lon = centroid(pm)
            sites.append({"type": "stand near a conifer threshold", "open_data": "stand",
                          "cfs": "", "area_ha": round(pm.sum() * HA, 1), "lat": lat, "lon": lon,
                          "inside_boundary": bool(inside[pm].mean() > 0.5),
                          "why": f"mean predicted conifer fraction {mcf:.2f} is within 0.07 of a C/M/D cut"})
    # 3. The largest stands of each forest type (check the type where most fuel sits)
    for value, name in ((C.C2, "C-2"), (C.M2, "M-2"), (C.D2, "D-2")):
        lb2, n2 = ndi.label(stands & (cls == value), structure=np.ones((3, 3), bool))
        if n2 == 0:
            continue
        sizes = np.bincount(lb2.ravel())[1:]
        for j in np.argsort(-sizes)[:2]:
            pm = lb2 == j + 1
            lat, lon = centroid(pm)
            sites.append({"type": f"largest {name} patch", "open_data": name, "cfs": "",
                          "area_ha": round(sizes[j] * HA, 1), "lat": lat, "lon": lon,
                          "inside_boundary": bool(inside[pm].mean() > 0.5),
                          "why": f"largest contiguous {name} patches; confirm type and percent conifer"})
    quota = {"open-data vs CFS disagreement": 12, "stand near a conifer threshold": 8}
    out, used = [], {}
    for srt in sorted(sites, key=lambda s: (-s["inside_boundary"], -s["area_ha"])):
        q = quota.get(srt["type"], 2)
        if used.get(srt["type"], 0) < q:
            out.append(srt)
            used[srt["type"]] = used.get(srt["type"], 0) + 1
    sites = out
    return sites[:max_sites]


# ── Maps ─────────────────────────────────────────────────────────────────────
COLORS = {"C-2": "#1b5e20", "D-2": "#9ccc65", "M-2": "#558b2f", "D-1": "#c5b358", "M-1": "#8d8d3a",
          "O-1a": "#fff59d", "O-1b": "#ffcc80", "NF": "#e0e0e0", "Water": "#64b5f6", "Code 13": "#ce93d8"}


def _rgb(lbl: np.ndarray) -> np.ndarray:
    from matplotlib.colors import to_rgb
    out = np.ones(lbl.shape + (3,))
    for k, c in COLORS.items():
        out[lbl == k] = to_rgb(c)
    return out


def maps() -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    files = []
    eg = aoi_grid("edmonton")
    ref = read_ref(eg)
    cls = np.load(WORK / "edmonton.npz")["cls"]
    cfs = read_tif(C.REGION_DIR / "cfs_fbp_2026.tif", eg)
    dom = (ref != 0)
    names = {**{k: v for k, v in C.CLASS_NAMES.items()}}
    ref_l = np.where(dom, mapcodes(ref, REF_FINE), "").astype(object)
    open_l = np.where(dom, mapcodes(cls, names), "").astype(object)
    cfs_l = np.where(dom, cfs_fine(cfs), "").astype(object)
    cfs_l[(cfs == 13) & dom] = "Code 13"
    cfs_l[(cfs == 102) & dom] = "Water"
    fig, ax = plt.subplots(1, 3, figsize=(18, 8.5))
    for a, (t, l) in zip(ax, [("City LiDAR grid (reference)", ref_l), ("Open-data grid, leaf-on (held-out)", open_l),
                              ("CFS national FBP 2026 (30 m)", cfs_l)]):
        a.imshow(_rgb(l), interpolation="nearest")
        a.set_title(t)
        a.axis("off")
    fig.legend(handles=[Patch(color=c, label=k) for k, c in COLORS.items()], loc="lower center", ncol=10)
    fig.suptitle("Edmonton, 20 m, EPSG:3776")
    p = C.VAL_DIR / "map_edmonton_three_grids.png"
    fig.savefig(p, dpi=110, bbox_inches="tight")
    plt.close(fig)
    files.append(str(p))
    # disagreement map
    grp = lambda a: np.vectorize(lambda v: FINE_GROUP.get(v, ""), otypes=[object])(a)  # noqa: E731
    rg, og = grp(ref_l), grp(np.where(dom, mapcodes(cls, OPEN_FINE), "").astype(object))
    d = np.zeros(ref.shape + (3,)) + 1
    agree = (rg == og) & (rg != "")
    d[agree & (rg == G_NF)] = (0.92, 0.92, 0.92)
    d[agree & (rg != G_NF)] = (0.55, 0.75, 0.55)
    d[(rg == G_NF) & np.isin(og, [G_CON, G_DEC, G_MIX])] = (0.85, 0.2, 0.2)
    d[np.isin(rg, [G_CON, G_DEC, G_MIX]) & (og == G_NF)] = (0.2, 0.3, 0.85)
    d[np.isin(rg, [G_CON, G_DEC, G_MIX]) & np.isin(og, [G_CON, G_DEC, G_MIX]) & (rg != og)] = (0.95, 0.6, 0.1)
    d[(rg == G_GRASS) & (og == G_NF) | (rg == G_NF) & (og == G_GRASS)] = (0.7, 0.55, 0.85)
    fig, a = plt.subplots(figsize=(9, 11))
    a.imshow(d, interpolation="nearest")
    a.axis("off")
    a.legend(handles=[Patch(color=(0.55, 0.75, 0.55), label="agree (fuel)"),
                      Patch(color=(0.92, 0.92, 0.92), label="agree (non-fuel)"),
                      Patch(color=(0.85, 0.2, 0.2), label="open-data forest, LiDAR non-fuel (stand rule)"),
                      Patch(color=(0.2, 0.3, 0.85), label="LiDAR forest, open-data non-fuel"),
                      Patch(color=(0.95, 0.6, 0.1), label="both forest, type differs"),
                      Patch(color=(0.7, 0.55, 0.85), label="grass vs non-fuel")], loc="lower left", fontsize=8)
    a.set_title("Edmonton: open-data vs LiDAR grid (5 groups)")
    p = C.VAL_DIR / "map_edmonton_disagreement.png"
    fig.savefig(p, dpi=110, bbox_inches="tight")
    plt.close(fig)
    files.append(str(p))
    # St. Albert
    sg = aoi_grid("st_albert")
    w = np.load(WORK / "st_albert.npz")
    cls_s, cf_s = w["cls"], w["conifer_frac"]
    cfs_s = read_tif(C.REGION_DIR / "cfs_fbp_2026.tif", sg)
    v = cls_s != C.NODATA
    on = np.where(v, mapcodes(cls_s, names), "").astype(object)
    off = np.where(v, mapcodes(R.leaf_off(cls_s), names), "").astype(object)
    cl = np.where(v, cfs_fine(cfs_s), "").astype(object)
    cl[(cfs_s == 13) & v] = "Code 13"
    cl[(cfs_s == 102) & v] = "Water"
    sites = json.loads((C.VAL_DIR / "validation.json").read_text())["st_albert"]["sites"] \
        if (C.VAL_DIR / "validation.json").exists() else []
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", C.CRS, always_xy=True)
    fig, ax = plt.subplots(2, 2, figsize=(15, 14))
    for a, (t, l) in zip(ax.ravel()[:3], [("Open-data, leaf-on", on), ("Open-data, leaf-off", off), ("CFS 2026", cl)]):
        a.imshow(_rgb(l), interpolation="nearest")
        a.set_title(t)
        a.axis("off")
    pc = np.where(np.isin(cls_s, [C.C2, C.D2, C.M2]), cf_s * 100, np.nan)
    im = ax[1, 1].imshow(pc, cmap="viridis", vmin=0, vmax=100, interpolation="nearest")
    ax[1, 1].set_title("Predicted percent conifer (treed stand cells); ground-truth sites")
    ax[1, 1].axis("off")
    fig.colorbar(im, ax=ax[1, 1], fraction=0.04)
    for i, s in enumerate(sites):
        x, y = tr.transform(s["lon"], s["lat"])
        c, r = (x - sg.x0) / sg.cell, (sg.y0 - y) / sg.cell
        for a in (ax[0, 0], ax[1, 1]):
            a.plot(c, r, "o", mfc="none", mec="red", ms=9)
            a.text(c + 6, r, str(i + 1), color="red", fontsize=8)
    fig.legend(handles=[Patch(color=c, label=k) for k, c in COLORS.items()], loc="lower center", ncol=10)
    fig.suptitle("St. Albert + 3 km buffer, 20 m, EPSG:3776")
    p = C.VAL_DIR / "map_st_albert.png"
    fig.savefig(p, dpi=110, bbox_inches="tight")
    plt.close(fig)
    files.append(str(p))
    return files


def validate_all() -> dict:
    C.VAL_DIR.mkdir(parents=True, exist_ok=True)
    res = {"edmonton": validate_edmonton(), "st_albert": validate_st_albert()}
    (C.VAL_DIR / "validation.json").write_text(json.dumps(res, indent=2, default=float))
    with open(C.VAL_DIR / "st_albert_ground_truth_sites.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "type", "open_data", "cfs", "area_ha", "lat", "lon",
                                          "inside_boundary", "why"])
        w.writeheader()
        for i, s in enumerate(res["st_albert"]["sites"]):
            w.writerow({"id": i + 1, **s})
    res["maps"] = maps()
    log.info("Edmonton open vs LiDAR: %s", _summ(res["edmonton"]["open_vs_lidar"]))
    log.info("Edmonton CFS vs LiDAR: %s", _summ(res["edmonton"]["cfs_vs_lidar"]))
    return res
