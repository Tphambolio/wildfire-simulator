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
    from .grid import Grid, band_names, read_tif, write_tif
    from .sources import aoi_grid, aoi_mask
    from .sources2 import PROV_V2, Region, burn_years, regions, test_area_grid
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    import key2 as K  # type: ignore[no-redef]
    import rules as R  # type: ignore[no-redef]
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


def _per_unit(a: np.ndarray, mask: np.ndarray, ids: np.ndarray, n: int, weights=None) -> tuple[np.ndarray, np.ndarray]:
    """(sum of weights, weighted mean) of `a` over the mask cells of each unit."""
    w = np.ones(a.shape) if weights is None else weights
    w = np.where(mask & np.isfinite(a), w, 0.0)
    sw = np.bincount(ids.ravel(), weights=w.ravel(), minlength=n)
    sa = np.bincount(ids.ravel(), weights=(np.nan_to_num(a) * w).ravel(), minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return sw, np.where(sw > 0, sa / np.maximum(sw, 1e-12), np.nan)


def edmonton_unit_data(grid: Grid, typed: np.ndarray, cells: int) -> dict:
    """Unit-scale prediction (mean of the out-of-fold cell conifer fraction), crown-weighted label
    share, crown count and spatial fold of every decision unit in Edmonton (City labels; local only)."""
    try:
        from . import labels as L
    except ImportError:  # pragma: no cover
        import labels as L  # type: ignore[no-redef]
    lab = L.load_labels()
    var = np.load(V1_WORK / "edmonton_conifer_variants.npz")
    ids = K.unit_ids(grid.shape, cells)
    n = int(ids.max()) + 1
    cnt = np.nan_to_num(lab["cnt"]).astype(np.float64)
    lf = lab["conifer_frac"]
    labelled = typed & (cnt > 0) & np.isfinite(lf)
    _, pred = _per_unit(var["oof"], typed, ids, n)
    crowns, share = _per_unit(lf, labelled, ids, n, weights=cnt)
    fold = np.full(n, -1)
    fold[ids[typed]] = var["folds"][typed]
    return {"ids": ids, "n": n, "pred": pred, "label": share, "crowns": crowns, "fold": fold}


def remap_units(u: dict, p: dict = C.PARAMS_V2) -> tuple[np.ndarray, dict]:
    """Out-of-fold quantile re-map at unit scale: for each spatial fold, the map from unit-mean
    prediction to unit label share is fitted on the units of the other folds (>= unit_min_crowns
    crowns) and applied to the fold's units. Returns (re-mapped unit values, full-data table)."""
    out = np.full(u["n"], np.nan)
    train = (u["crowns"] >= p["unit_min_crowns"]) & np.isfinite(u["pred"]) & np.isfinite(u["label"])
    for k in range(C.PARAMS["n_folds"]):
        tr = train & (u["fold"] != k) & (u["fold"] >= 0)
        xp, yp = K.quantile_map(u["pred"][tr], u["label"][tr])
        te = (u["fold"] == k) & np.isfinite(u["pred"])
        out[te] = np.interp(u["pred"][te], xp, yp)
    xp, yp = K.quantile_map(u["pred"][train], u["label"][train])
    return out, {"xp": xp, "yp": yp, "n_train_units": int(train.sum())}


def build_capital(key: str, regs: dict, table: dict | None = None) -> tuple[dict, dict | None]:
    r = regs["capital"]
    grid = aoi_grid(key)
    v1 = np.load(V1_WORK / f"{key}.npz")
    valid = aoi_mask(key, grid)
    x, diag = evidence(r, grid, C.MAP_YEAR_CAPITAL, v1["conifer_frac"], valid, "aci2025.tif")
    _, rule0, _, _ = K.decision_key_v2(x)          # first pass: the 20 m forest extent
    typed = np.isin(rule0, FOREST_RULES)
    cells = int(C.PARAMS_V2["unit_cells"])
    ids = K.unit_ids(grid.shape, cells)
    if key == "edmonton":
        u = edmonton_unit_data(grid, typed, cells)
        mapped, table = remap_units(u)
        how = "unit mean of the out-of-fold cell prediction, re-mapped out-of-fold at unit scale"
    else:
        n = int(ids.max()) + 1
        _, pred = _per_unit(v1["conifer_frac"], typed, ids, n)
        mapped = np.interp(pred, table["xp"], table["yp"])
        how = "unit mean of the cell prediction, re-mapped with the Edmonton unit-scale table"
    ucf = np.where(typed, mapped[ids], np.nan).astype(np.float32)
    x.unit_conifer = ucf
    cls, rule, stands, pc = K.decision_key_v2(x)
    outs = write_outputs(key, C.AOIS[key].name, grid, cls, rule, pc, stands, x.base.conifer_frac, ucf,
                         {"conifer_model": "v1 LightGBM conifer fraction (Edmonton out-of-fold); " + how,
                          "decision_unit": f"{cells * grid.cell:.0f} m blocks", "map_year": C.MAP_YEAR_CAPITAL})
    return {"files": outs, "diagnostics": diag, "areas_ha": K.class_areas(cls, grid.cell)}, table


def build_test_area(key: str, regs: dict) -> dict:
    t = C.TEST_AREAS[key]
    r = regs[t.region]
    grid = test_area_grid(key)
    valid = np.ones(grid.shape, bool)
    x, diag = evidence(r, grid, t.year, None, valid, f"aci{t.year - 1}.tif", alt_gate=True)
    if diag["chm_image_years"]["max"] is not None and diag["chm_image_years"]["max"] >= t.year:
        raise RuntimeError(f"{key}: canopy imagery from {diag['chm_image_years']['max']} is not pre-fire")
    cls, rule, stands, pc = K.decision_key_v2(x)
    typed = np.isin(rule, FOREST_RULES)
    ucf = np.where(typed, K.unit_mean(x.base.conifer_frac, typed, K.unit_ids(grid.shape, int(C.PARAMS_V2["unit_cells"]))),
                   np.nan).astype(np.float32)
    outs = write_outputs(key, f"fire {t.fire_id} test area (pre-fire, map year {t.year})", grid, cls, rule, pc,
                         stands, x.base.conifer_frac, ucf,
                         {"conifer_model": "SCANFI v2 species composition (tamarack as deciduous)",
                          "map_year": t.year, "fire_id": t.fire_id})
    return {"files": outs, "diagnostics": diag, "areas_ha": K.class_areas(cls, grid.cell)}


def build_all_v2() -> dict:
    regs = regions()
    summary = {}
    table = None
    for k in ("edmonton", "st_albert"):  # Edmonton first: its unit-scale table maps St. Albert
        summary[k], t = build_capital(k, regs, table)
        table = table or t
    for k in C.TEST_AREAS:
        summary[k] = build_test_area(k, regs)
        log.info("%s: %s", k, summary[k]["areas_ha"])
    C.VAL_DIR_V2.mkdir(parents=True, exist_ok=True)
    (C.VAL_DIR_V2 / "build_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary
