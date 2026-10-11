"""Build the v2 open-data FBP grids (full decision key + disturbance).

* Edmonton and St. Albert: the v1 inputs and v1 conifer fractions (read from the v1 work files;
  Edmonton's are out-of-fold), plus SCANFI species / lichen, canopy-image year, Hansen forest loss
  and Alberta wildfire perimeters. Map year 2026 (current).
* Northern Alberta test areas (``config.TEST_AREAS``): no training labels, so the conifer fraction
  is SCANFI's species composition (tamarack counted as deciduous, as in CFS 2018). Map year = fire
  year, with every time-dependent input dated before it (pre-fire): SCANFI v2 epoch before the
  year, SCANFI v3 of the previous year, burns and forest loss before the year, canopy imagery
  2010-2020, WorldCover 2021.

Outputs go to ``OUT_DIR_V2`` with product name ``fbp_opendata2``; v1 files are never touched.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import date

import numpy as np

try:
    from . import config as C
    from . import key2 as K
    from . import rules as R
    from . import stands as S
    from .grid import Grid, band_names, read_tif, write_tif
    from .sources import aoi_grid, aoi_mask
    from .sources2 import PROV_V2, Region, burn_years, regions, test_area_grid
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    import key2 as K  # type: ignore[no-redef]
    import rules as R  # type: ignore[no-redef]
    import stands as S  # type: ignore[no-redef]
    from grid import Grid, band_names, read_tif, write_tif  # type: ignore[no-redef]
    from sources import aoi_grid, aoi_mask  # type: ignore[no-redef]
    from sources2 import PROV_V2, Region, burn_years, regions, test_area_grid  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.build2")
VERSION_DATE = date.today().strftime("%Y%m%d")
WORK2 = C.VAL_DIR_V2 / "work"
V1_WORK = C.VAL_DIR / "work"


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(C.REPO), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # pragma: no cover
        return "unknown"


def epsg(grid: Grid) -> str:
    return grid.crs.split(":")[1]


def file_stem(key: str, kind: str, grid: Grid) -> str:
    return f"{key}_fbp_opendata2_{kind}_20m_{epsg(grid)}_v{VERSION_DATE}"


def _bands(r: Region, name: str, grid: Grid) -> dict[str, np.ndarray]:
    path = r.dir / name
    a = read_tif(path, grid, region=r.grid)
    a = a if a.ndim == 3 else a[None]
    return dict(zip(band_names(path), a))


def _scanfi_epoch(r: Region, epoch: int, grid: Grid) -> dict[str, np.ndarray]:
    b = _bands(r, f"scanfi_v2_{epoch}.tif", grid)
    return {k.replace("spsCC_", ""): v for k, v in b.items()}


def evidence(r: Region, grid: Grid, year: int, conifer_frac: np.ndarray | None, valid: np.ndarray,
             aci_name: str, alt_gate: bool = False) -> tuple[K.KeyInputsV2, dict]:
    """All decision-key inputs for an AOI grid and map year; returns (inputs, diagnostics)."""
    epoch = max(e for e in C.SCANFI_V2_EPOCHS if e < year)
    sc = _scanfi_epoch(r, epoch, grid)
    sc_prev = _scanfi_epoch(r, epoch - 5, grid)
    v3 = _bands(r, f"scanfi_v3_{min(year - 1, 2025)}.tif", grid)
    chm = read_tif(r.dir / "meta_chm.tif", grid, region=r.grid)
    chm_year = read_tif(r.dir / "meta_chm_year.tif", grid, region=r.grid).astype(np.int32)
    loss = read_tif(r.dir / "hansen_lossyear.tif", grid, region=r.grid).astype(np.int32)
    loss = np.where(loss < year, loss, 0)
    burns = [(y, m) for y, m in burn_years(r, grid) if y < year]
    last_burn = np.zeros(grid.shape, np.int32)
    for y, m in burns:
        last_burn[m] = y
    harvest = K.harvest_year(loss, burns)
    dist_year = np.maximum(last_burn, harvest)

    valid_chm = chm[4] > 0
    stale = K.stale_canopy(chm_year, dist_year) | ~valid_chm
    sc_closure = np.clip(np.nan_to_num(sc["att_closure"]) / 100.0, 0, 1)
    sc_height = np.nan_to_num(sc["att_height"])
    cover5 = np.where(stale, sc_closure, np.clip(chm[0], 0, 1)).astype(np.float32)
    cover2 = np.where(stale, sc_closure, np.clip(chm[1], 0, 1)).astype(np.float32)
    height = np.where(stale, sc_height, np.clip(chm[3], 0, None)).astype(np.float32)

    cf_scanfi, species, _ = K.species_from_scanfi(sc)
    cf = cf_scanfi if conifer_frac is None else conifer_frac
    # pre-harvest composition: the latest SCANFI epoch before the harvest year
    cf_prev, _, pine_prev = K.species_from_scanfi(sc_prev)
    cf_now, _, pine_now = K.species_from_scanfi(sc)
    before_now = harvest > epoch
    pre_cf = np.where(before_now, cf_now, cf_prev)
    pre_pine = np.where(before_now, pine_now, pine_prev)
    pre_closure = np.where(before_now, sc_closure, np.clip(np.nan_to_num(sc_prev["att_closure"]) / 100.0, 0, 1))

    wc = read_tif(r.dir / "worldcover_frac.tif", grid, region=r.grid).astype(np.float32) / 100.0
    base = R.KeyInputs(
        valid=valid, cover=cover5, conifer_frac=cf.astype(np.float32),
        bld_frac=read_tif(r.dir / "buildings_frac.tif", grid, region=r.grid),
        wc={k: wc[i] for i, k in enumerate(C.WC_BANDS)},
        aci=read_tif(r.dir / aci_name, grid, region=r.grid),
        osm=read_tif(r.dir / "osm_landuse.tif", grid, region=r.grid))
    x = K.KeyInputsV2(
        base=base, species=species, height=height, cover2=cover2, closure=R.mean3x3(cover2),
        lichen=np.nan_to_num(v3["nonTreed_lichen"]) / 100.0,
        treed_con=np.nan_to_num(v3["treed_coniferous"]) / 100.0,
        treed_broad=np.nan_to_num(v3["treed_broadleaf"]) / 100.0,
        years_since_fire=K.years_since(last_burn, year), years_since_harvest=K.years_since(harvest, year),
        pre_harvest_conifer=pre_cf, pre_harvest_pine=pre_pine, pre_harvest_closure=pre_closure)
    if alt_gate:
        treed = (np.nan_to_num(v3["treed_coniferous"]) + np.nan_to_num(v3["treed_broadleaf"])) / 100.0
        x.alt_treed = np.clip(treed, 0, 1).astype(np.float32)
        x.alt_closure = sc_closure.astype(np.float32)
        x.alt_height = sc_height.astype(np.float32)
    cy = chm_year[valid & (chm_year > 0)]
    diag = {"map_year": year, "scanfi_v2_epoch": epoch, "scanfi_v2_prev_epoch": epoch - 5,
            "scanfi_v3_year": min(year - 1, 2025), "aci": aci_name,
            "chm_image_years": {"min": int(cy.min()) if cy.size else None, "max": int(cy.max()) if cy.size else None},
            "burn_years_used": sorted({y for y, _ in burns}),
            "stale_canopy_ha": round(float((stale & valid & valid_chm).sum()) * grid.cell ** 2 / 1e4, 1),
            "no_canopy_model_ha": round(float((~valid_chm & valid).sum()) * grid.cell ** 2 / 1e4, 1),
            "harvest_loss_cells": int(((harvest > 0) & valid).sum()),
            "fire_loss_cells": int(((loss > 0) & (harvest == 0) & valid).sum())}
    return x, diag


def write_outputs(key: str, name: str, grid: Grid, cls, rule, pc, stands, cf, ucf, extra: dict) -> dict:
    prov = json.loads(PROV_V2.read_text()) if PROV_V2.exists() else {}
    v1prov = {k: v for k, v in json.loads((C.DATA / "provenance.json").read_text()).items()
              if not k.startswith("cfs_fbp")}
    tags = {
        "TIFFTAG_DOCUMENTNAME": f"FireSim open-data FBP fuel grid v2, {name}",
        "product": "FireSim open-data FBP fuel grid v2: full FBP key + disturbance (no LiDAR, no inventory)",
        "aoi": name, "resolution_m": str(grid.cell), "crs": grid.crs,
        "version": f"{C.PIPELINE_VERSION_V2}+{VERSION_DATE}", "date_built": date.today().isoformat(),
        "generator": f"scripts/fuelgrid build2 (FireSim git {_git_sha()})",
        "sources": "; ".join(sorted({f"{v.get('title', '')} [{v.get('licence', '')}]"
                                     for v in list(v1prov.values()) + list(prov.values()) if v.get("title")})),
        "decision_key": json.dumps(C.RULES_V2), "params": json.dumps(C.PARAMS_V2),
        "seasonal_rule": (f"leaf-on (D-2/M-2) from day-of-year {C.GREENUP_DOY} to {C.LEAFOFF_DOY}; "
                          "leaf-off (D-1/M-1) otherwise"),
        "documentation": "docs/fuel-grid-open-data.md (v2: section 4b)", **{k: str(v) for k, v in extra.items()},
    }
    C.OUT_DIR_V2.mkdir(parents=True, exist_ok=True)
    codes = {**{v: C.CLASS_NAMES_V2[k] for k, v in C.CIFFC_V2.items()},
             "4xx": "M-1, xx = percent conifer", "5xx": "M-2, xx = percent conifer"}
    outs = {}
    for season, arr in (("leafon", cls), ("leafoff", K.leaf_off_v2(cls))):
        p = C.OUT_DIR_V2 / f"{file_stem(key, f'{season}_ciffc', grid)}.tif"
        write_tif(p, K.encode_ciffc_v2(arr, pc), grid, nodata=0, band_names=["fbp_code"],
                  tags={**tags, "code_scheme": "cfs_national (load with code_scheme='cfs_national')",
                        "season": "leaf-on" if season == "leafon" else "leaf-off", "codes": json.dumps(codes)})
        outs[f"{season}_ciffc"] = str(p)
    p = C.OUT_DIR_V2 / f"{file_stem(key, 'rule', grid)}.tif"
    write_tif(p, rule, grid, nodata=0, band_names=["rule"],
              tags={**tags, "content": "decision-key rule that set each cell", "rules": json.dumps(C.RULES_V2)})
    outs["rule"] = str(p)
    p = C.OUT_DIR_V2 / f"{file_stem(key, 'percent_conifer', grid)}.tif"
    pcf = np.where(cls != C.NODATA, np.round(np.clip(cf, 0, 1) * 100), -1).astype(np.int16)
    pcu = np.where(np.isfinite(ucf) & (cls != C.NODATA), np.round(np.clip(np.nan_to_num(ucf), 0, 1) * 100),
                   -1).astype(np.int16)
    write_tif(p, np.stack([pcf, pcu]), grid, nodata=-1, band_names=["percent_conifer_cell", "percent_conifer_unit"],
              tags={**tags, "content": ("band 1: 20 m continuous percent conifer (0-100); band 2: decision-unit "
                                        "(100 m block) percent conifer used to type forest cells; -1 = none")})
    outs["percent_conifer"] = str(p)
    meta = {**tags, "params": C.PARAMS_V2, "decision_key": C.RULES_V2, "files": outs,
            "sources_v1": v1prov, "sources_v2": prov}
    (C.OUT_DIR_V2 / f"{file_stem(key, 'meta', grid)}.json").write_text(json.dumps(meta, indent=2, default=str))
    WORK2.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(WORK2 / f"{key}.npz", cls=cls, rule=rule, pc=pc, stands=stands, conifer_frac=cf,
                        unit_conifer=ucf, valid=cls != C.NODATA)
    return outs


FOREST_RULES = list(range(20, 27)) + list(range(60, 67))


def _majority(ids: np.ndarray, values: np.ndarray, n: int, nvals: int) -> np.ndarray:
    """Most frequent value (0..nvals-1) per id 1..n (row 0 unused)."""
    m = ids > 0
    cnt = np.bincount(ids[m].astype(np.int64) * nvals + values[m].astype(np.int64), minlength=(n + 1) * nvals)
    return cnt.reshape(n + 1, nvals).argmax(1)


def capital_features(grid: Grid) -> tuple[np.ndarray, list[str]]:
    """v1 open features plus a canopy-height texture layer (3 x 3 SD of mean tree height)."""
    from scipy import ndimage as ndi
    try:
        from . import model as M
    except ImportError:  # pragma: no cover
        import model as M  # type: ignore[no-redef]
    F, names = M.features(grid)
    h = np.nan_to_num(F[names.index("chm_height5")])
    mu = ndi.uniform_filter(h, 3)
    sd = np.sqrt(np.maximum(ndi.uniform_filter(h * h, 3) - mu * mu, 0)).astype(np.float32)
    return np.concatenate([F, sd[None]]), names + ["chm_height5_sd3x3"]


def stand_objects(typed: np.ndarray, pid: np.ndarray | None, share: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(typing objects on forest cells, feature objects incl. whole uPLVI polygons).

    uPLVI polygons are the objects where they exist (ids = polygon ids); the remaining forest cells
    are split into one-class objects (``stands.class_objects``) with ids above the largest polygon."""
    base = np.zeros(typed.shape, np.int32) if pid is None else pid.astype(np.int32)
    start = int(base.max()) + 1
    seg = S.class_objects(share, typed & (base == 0), start=start)
    units = np.where(typed, np.where(base > 0, base, seg), 0).astype(np.int32)
    feat_ids = np.where(base > 0, base, seg).astype(np.int32)
    return units, feat_ids


def stand_model_oof(X: np.ndarray, pc: np.ndarray, fold: np.ndarray, keep: np.ndarray) -> dict:
    """Per-fold stand models (trained without the fold) + a full model; out-of-fold predictions for
    every training polygon, and the regressor-only variant for comparison."""
    n_folds = C.PARAMS["n_folds"]
    models, k_oof, pc_oof, reg_only = {}, np.full(len(pc), -1), np.full(len(pc), np.nan), np.full(len(pc), -1)
    for k in range(n_folds):
        tr, te = keep & (fold != k), keep & (fold == k)
        m = S.StandModel(C.PARAMS["seed"] + k).fit(X[tr], pc[tr])
        models[k] = m
        if te.any():
            k_oof[te], pc_oof[te] = m.predict(X[te])
            reg_only[te] = S.cmd_class(np.clip(m.reg.predict(X[te]), 0, 1))
    models["full"] = S.StandModel(C.PARAMS["seed"]).fit(X[keep], pc[keep])
    return {"models": models, "class_oof": k_oof, "pc_oof": pc_oof, "class_oof_regressor_only": reg_only}


def build_capital(key: str, regs: dict, bundle: dict | None = None, uplvi_objects: bool = True,
                  variant: str | None = None) -> tuple[dict, dict]:
    """v2.1 grid for Edmonton / St. Albert. `uplvi_objects=False` (validation variant) types
    Edmonton on open-data objects only, so the score does not borrow the label polygons' geometry;
    a variant is written to VAL_DIR_V2 / "variants" instead of the products."""
    r = regs["capital"]
    grid = aoi_grid(key)
    v1 = np.load(V1_WORK / f"{key}.npz")
    valid = aoi_mask(key, grid)
    x, diag = evidence(r, grid, C.MAP_YEAR_CAPITAL, v1["conifer_frac"], valid, "aci2025.tif")
    _, rule0, _, _ = K.decision_key_v2(x)          # first pass: the 20 m forest extent
    typed = np.isin(rule0, FOREST_RULES)
    F, names = capital_features(grid)
    folds_cell = M_block_folds(grid)
    if key == "edmonton":
        pid, df = S.uplvi_raster(grid)
        npoly = len(df)
        X_poly = S.segment_means(F, pid, npoly)[1:]
        cells = np.bincount(pid.ravel(), minlength=npoly + 1)[1:]
        pc = df.pc.to_numpy() / 100.0
        keep = np.isfinite(pc) & (df.treed_share.to_numpy() >= C.PARAMS_V2["uplvi_min_treed_share"]) \
            & (cells >= C.PARAMS_V2["uplvi_min_cells"]) & np.isfinite(X_poly).any(1)
        fold_poly = _majority(pid, folds_cell, npoly, C.PARAMS["n_folds"])[1:]
        bundle = {**stand_model_oof(X_poly, pc, fold_poly, keep), "pc": pc, "keep": keep, "fold": fold_poly,
                  "all_treed": df.all_treed.to_numpy(), "cells": cells, "n_train": int(keep.sum())}
        ti = [names.index(n) for n in S.TRANSFER]
        bundle["transfer"] = stand_model_oof(X_poly[:, ti], pc, fold_poly, keep)
        if uplvi_objects:
            units, feat_ids = stand_objects(typed, pid, v1["conifer_frac"])
            objects = "uPLVI 2025 polygons + one-class objects (<= 4 ha) elsewhere"
        else:
            units, feat_ids = stand_objects(typed, None, v1["conifer_frac"])
            objects = "one-class objects (<= 4 ha) only (validation variant)"
    else:
        units, feat_ids = stand_objects(typed, None, v1["conifer_frac"])
        objects = "one-class objects (<= 4 ha)"
    n = int(feat_ids.max())
    Xs = S.segment_means(F, feat_ids, n)
    seg_fold = _majority(feat_ids, folds_cell, n, C.PARAMS["n_folds"])
    present = np.unique(units[units > 0])
    ucf_seg = np.full(n + 1, np.nan, np.float32)
    okx = np.isfinite(Xs[present]).any(1)
    present = present[okx]
    if key == "edmonton":  # out of fold: each object predicted by the model that did not see its block
        for k in range(C.PARAMS["n_folds"]):
            sel = present[seg_fold[present] == k]
            if len(sel):
                ucf_seg[sel] = bundle["models"][k].predict(Xs[sel])[1]
        how = "uPLVI-trained stand model, out of fold by 2 km blocks"
    else:
        ucf_seg[present] = bundle["models"]["full"].predict(Xs[present])[1]
        how = "uPLVI-trained stand model (all Edmonton polygons)"
    ucf = np.where(typed, ucf_seg[units], np.nan).astype(np.float32)
    x.units, x.unit_conifer = units, ucf
    cls, rule, stands, pc_out = K.decision_key_v2(x)
    if variant is not None:
        vdir = C.VAL_DIR_V2 / "variants"
        vdir.mkdir(parents=True, exist_ok=True)
        write_tif(vdir / f"{key}_{variant}_leafon_ciffc.tif", K.encode_ciffc_v2(cls, pc_out), grid, nodata=0,
                  band_names=["fbp_code"], tags={"content": f"validation variant {variant}; not a product"})
        return {"areas_ha": K.class_areas(cls, grid.cell)}, bundle
    outs = write_outputs(key, C.AOIS[key].name, grid, cls, rule, pc_out, stands, x.base.conifer_frac, ucf,
                         {"conifer_model": how, "decision_unit": objects, "map_year": C.MAP_YEAR_CAPITAL})
    np.savez_compressed(WORK2 / f"{key}_units.npz", units=units)
    return {"files": outs, "diagnostics": {**diag, "objects": int(len(np.unique(units[units > 0])))},
            "areas_ha": K.class_areas(cls, grid.cell)}, bundle


def M_block_folds(grid: Grid) -> np.ndarray:
    try:
        from . import model as M
    except ImportError:  # pragma: no cover
        import model as M  # type: ignore[no-redef]
    return M.block_folds(grid)


def build_test_area(key: str, regs: dict, bundle: dict | None = None) -> dict:
    t = C.TEST_AREAS[key]
    r = regs[t.region]
    grid = test_area_grid(key)
    valid = np.ones(grid.shape, bool)
    x, diag = evidence(r, grid, t.year, None, valid, f"aci{t.year - 1}.tif", alt_gate=True)
    if diag["chm_image_years"]["max"] is not None and diag["chm_image_years"]["max"] >= t.year:
        raise RuntimeError(f"{key}: canopy imagery from {diag['chm_image_years']['max']} is not pre-fire")
    _, rule0, _, _ = K.decision_key_v2(x)
    typed = np.isin(rule0, FOREST_RULES)
    units, _ = stand_objects(typed, None, x.base.conifer_frac)
    x.units = units
    how = "SCANFI v2 species composition (tamarack as deciduous), object mean"
    if bundle is not None:  # transfer stand model (SCANFI + canopy features, trained on Edmonton uPLVI)
        epoch = max(e for e in C.SCANFI_V2_EPOCHS if e < t.year)
        sc = _scanfi_epoch(r, epoch, grid)
        v3 = _bands(r, f"scanfi_v3_{min(t.year - 1, 2025)}.tif", grid)
        chm = read_tif(r.dir / "meta_chm.tif", grid, region=r.grid)
        Ft = S.transfer_features(sc, v3, chm)
        n = int(units.max())
        Xs = S.segment_means(Ft, units, n)
        present = np.unique(units[units > 0])
        present = present[np.isfinite(Xs[present]).any(1)]
        ucf_seg = np.full(n + 1, np.nan, np.float32)
        ucf_seg[present] = bundle["transfer"]["models"]["full"].predict(Xs[present])[1]
        mode = C.PARAMS_V2["north_mode"]
        if mode != "transfer":  # guard the transfer call with the object's own SCANFI share [H]
            share = S.segment_means(x.base.conifer_frac[None], units, n)[:, 0]
            k = S.cmd_class(ucf_seg)
            demote = (k == 2) & (share < 0.5)
            if mode == "transfer_guard_both":
                demote |= (k == 0) & (share > 0.5)
            mpc = np.clip(np.nan_to_num(share), C.PARAMS_V2["conifer_d"] + 0.05, C.PARAMS_V2["conifer_c"] - 0.05)
            ucf_seg = np.where(demote, mpc, ucf_seg).astype(np.float32)
        x.unit_conifer = np.where(typed, ucf_seg[units], np.nan).astype(np.float32)
        how = "transfer stand model (SCANFI + canopy features) trained on Edmonton uPLVI, object mean features"
    cls, rule, stands, pc = K.decision_key_v2(x)
    typed2 = np.isin(rule, FOREST_RULES)
    ucf = x.unit_conifer if x.unit_conifer is not None else K.unit_mean(x.base.conifer_frac, typed, units)
    ucf = np.where(typed2, ucf, np.nan).astype(np.float32)
    outs = write_outputs(key, f"fire {t.fire_id} test area (pre-fire, map year {t.year})", grid, cls, rule, pc,
                         stands, x.base.conifer_frac, ucf,
                         {"conifer_model": how,
                          "decision_unit": "one-class objects (<= 4 ha)", "map_year": t.year, "fire_id": t.fire_id})
    np.savez_compressed(WORK2 / f"{key}_units.npz", units=units)
    return {"files": outs, "diagnostics": {**diag, "objects": int(len(np.unique(units[units > 0])))},
            "areas_ha": K.class_areas(cls, grid.cell)}


def stand_scores(bundle: dict) -> dict:
    """Out-of-fold stand scores against uPLVI (all training polygons, and the all-treed pure set)."""
    keep, pc, k = bundle["keep"], bundle["pc"], bundle["class_oof"]
    ok = keep & (k >= 0)
    y = S.cmd_class(pc[ok])
    cm = np.zeros((3, 3), int)
    np.add.at(cm, (y, k[ok]), 1)
    a = R.agreement(cm)
    pure = ok & bundle["all_treed"]
    reg = bundle["class_oof_regressor_only"]
    return {"n_train_polygons": bundle["n_train"],
            "cmd_all_polygons": {"oa": round(a["oa"], 3), "kappa": round(a["kappa"], 3),
                                 "confusion_rows_true_D_M_C": cm.tolist()},
            "pure_all_treed_classifier": S.balanced_accuracy(pc[pure], k[pure]),
            "pure_all_treed_regressor_only": S.balanced_accuracy(pc[pure], reg[pure]),
            "pc_mae_oof": round(float(np.nanmean(np.abs(bundle["pc_oof"][ok] - pc[ok]))), 3)}


def build_all_v2() -> dict:
    regs = regions()
    summary = {}
    bundle = None
    for k in ("edmonton", "st_albert"):  # Edmonton first: its stand model maps St. Albert
        summary[k], bundle = build_capital(k, regs, bundle)
    summary["edmonton_stand_model_oof"] = stand_scores(bundle)
    summary["edmonton_transfer_model_oof"] = stand_scores({**bundle, **bundle["transfer"]})
    for k in C.TEST_AREAS:
        summary[k] = build_test_area(k, regs, None if C.PARAMS_V2["north_mode"] == "scanfi" else bundle)
        log.info("%s: %s", k, summary[k]["areas_ha"])
    C.VAL_DIR_V2.mkdir(parents=True, exist_ok=True)
    (C.VAL_DIR_V2 / "build_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary
