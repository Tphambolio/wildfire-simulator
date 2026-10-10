"""Build the open-data FBP fuel grids (leaf-on and leaf-off) for each AOI.

Edmonton uses out-of-fold conifer fractions (spatial-block CV), so every Edmonton number in the
validation is a held-out number. St. Albert uses a model fitted on all Edmonton labels.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import date

import numpy as np

try:
    from . import config as C
    from . import labels as L
    from . import model as M
    from . import rules as R
    from .grid import Grid, read_tif, write_tif
    from .sources import aoi_grid, aoi_mask
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    import labels as L  # type: ignore[no-redef]
    import model as M  # type: ignore[no-redef]
    import rules as R  # type: ignore[no-redef]
    from grid import Grid, read_tif, write_tif  # type: ignore[no-redef]
    from sources import aoi_grid, aoi_mask  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.build")

VERSION_DATE = date.today().strftime("%Y%m%d")
WORK = C.VAL_DIR / "work"


def key_inputs(key: str, grid: Grid, conifer_frac: np.ndarray) -> R.KeyInputs:
    wc = read_tif(C.REGION_DIR / "worldcover_frac.tif", grid).astype(np.float32) / 100.0
    chm = read_tif(C.REGION_DIR / "meta_chm.tif", grid)
    cover = np.clip(np.where(chm[0] < 0, 0, chm[0]), 0, 1)
    return R.KeyInputs(
        valid=aoi_mask(key, grid),
        cover=cover,
        conifer_frac=conifer_frac,
        bld_frac=read_tif(C.REGION_DIR / "buildings_frac.tif", grid),
        wc={k: wc[i] for i, k in enumerate(C.WC_BANDS)},
        aci=read_tif(C.REGION_DIR / "aci2025.tif", grid),
        osm=read_tif(C.REGION_DIR / "osm_landuse.tif", grid),
    )


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(C.REPO), "rev-parse", "--short", "HEAD"],
                                       text=True).strip()
    except Exception:  # pragma: no cover
        return "unknown"


def file_stem(key: str, kind: str) -> str:
    return f"{key}_{kind}_20m_3776_v{VERSION_DATE}"


def write_outputs(key: str, grid: Grid, cls: np.ndarray, rule: np.ndarray, conifer_frac: np.ndarray,
                  stands: np.ndarray, extra_tags: dict) -> dict:
    sources = {k: v for k, v in json.loads((C.DATA / "provenance.json").read_text()).items()
               if not k.startswith("cfs_fbp")}  # the CFS layer is a comparison, not an input
    base_tags = {
        "TIFFTAG_DOCUMENTNAME": f"FireSim open-data FBP fuel grid, {C.AOIS[key].name}",
        "product": "FireSim open-data FBP fuel grid (no LiDAR, no municipal inventory)",
        "aoi": C.AOIS[key].name,
        "resolution_m": str(C.CELL),
        "crs": C.CRS,
        "version": f"{C.PIPELINE_VERSION}+{VERSION_DATE}",
        "date_built": date.today().isoformat(),
        "generator": f"scripts/fuelgrid (FireSim git {_git_sha()})",
        "sources": "; ".join(f"{k}: {v.get('title', '')} [{v.get('licence', '')}]"
                             for k, v in sorted(sources.items())),
        "decision_key": json.dumps(C.RULES),
        "params": json.dumps(C.PARAMS),
        "seasonal_rule": (f"leaf-on (D-2/M-2) from day-of-year {C.GREENUP_DOY} (green-up) to "
                          f"{C.LEAFOFF_DOY} (leaf fall); leaf-off (D-1/M-1) otherwise"),
        "documentation": "docs/fuel-grid-open-data.md",
        **extra_tags,
    }
    leafon = cls
    leafoff = R.leaf_off(cls)
    outs = {}
    p = C.OUT_DIR / f"{file_stem(key, 'fbp_opendata_leafon')}.tif"
    write_tif(p, R.encode_canopy_lidar(leafon), grid, nodata=0, band_names=["fbp_code"],
              tags={**base_tags, "code_scheme": "canopy_lidar", "season": "leaf-on",
                    "codes": json.dumps({v: C.CLASS_NAMES[k] for k, v in C.CANOPY_LIDAR.items()})})
    outs["leafon_canopy_lidar"] = p
    ciffc_codes = {**{v: C.CLASS_NAMES[k] for k, v in C.CIFFC.items()},
                   "4xx": "M-1, xx = percent conifer", "5xx": "M-2, xx = percent conifer"}
    for season, arr in (("leafon", leafon), ("leafoff", leafoff)):
        p = C.OUT_DIR / f"{file_stem(key, f'fbp_opendata_{season}_ciffc')}.tif"
        write_tif(p, R.encode_ciffc(arr, conifer_frac), grid, nodata=0, band_names=["fbp_code"],
                  tags={**base_tags, "code_scheme": "cfs_national (load with code_scheme='cfs_national')",
                        "season": "leaf-on" if season == "leafon" else "leaf-off", "codes": json.dumps(ciffc_codes)})
        outs[f"{season}_ciffc"] = p
    treed = np.isin(cls, [C.C2, C.D2, C.M2])
    pc = np.where(treed, np.round(np.clip(conifer_frac, 0, 1) * 100), -1).astype(np.int16)
    p = C.OUT_DIR / f"{file_stem(key, 'percent_conifer')}.tif"
    write_tif(p, pc, grid, nodata=-1, band_names=["percent_conifer"],
              tags={**base_tags, "content": "predicted percent conifer of the canopy (0-100) in C-2/D-2/M-2 cells"})
    outs["percent_conifer"] = p
    p = C.OUT_DIR / f"{file_stem(key, 'fbp_opendata_rule')}.tif"
    write_tif(p, rule, grid, nodata=0, band_names=["rule"],
              tags={**base_tags, "content": "decision-key rule that set each cell", "rules": json.dumps(C.RULES)})
    outs["rule"] = p
    meta = {k: (v if isinstance(v, str) else json.loads(v) if k in ("params", "decision_key") else v)
            for k, v in base_tags.items()}
    meta["files"] = {k: str(v) for k, v in outs.items()}
    meta["sources"] = sources
    (C.OUT_DIR / f"{file_stem(key, 'fbp_opendata')}.json").write_text(json.dumps(meta, indent=2, default=str))
    WORK.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(WORK / f"{key}.npz", cls=cls, rule=rule, conifer_frac=conifer_frac, stands=stands,
                        valid=cls != C.NODATA)
    return {k: str(v) for k, v in outs.items()}


def edmonton_domain(grid: Grid, lab: dict, cover: np.ndarray, built: np.ndarray) -> np.ndarray:
    """Training domain: reference forest cells or open-data stand candidates (natural stands)."""
    ref = read_ref(grid)
    stands = R.stand_mask(cover, built)
    return np.isin(ref, [2, 12, 14]) | stands


def read_ref(grid: Grid) -> np.ndarray:
    import rasterio
    with rasterio.open(C.REFERENCE_GRID) as src:
        g = Grid(src.transform.c, src.transform.f, src.width, src.height)
        if (g.x0, g.y0, g.shape) != (grid.x0, grid.y0, grid.shape):
            raise ValueError("Edmonton AOI grid does not match the City LiDAR grid")
        return src.read(1)


def build_all() -> dict:
    C.OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {}
    # ── Edmonton: out-of-fold conifer fraction ──
    eg = aoi_grid("edmonton")
    X, names = M.features(eg)
    lab = L.load_labels()
    probe = key_inputs("edmonton", eg, np.zeros(eg.shape, np.float32))
    built = R.built_mask(probe.bld_frac, probe.wc[50])
    domain = edmonton_domain(eg, lab, probe.cover, built)
    train = M.train_mask(lab, domain)
    y = np.nan_to_num(lab["conifer_frac"]).astype(np.float32)
    log.info("Edmonton training cells: %d", int(train.sum()))
    oof, oof_raw, per_fold, imp = M.cv_predict(eg, X, y, train)
    full = M.fit_full(X, y, train)
    insample = M.predict(full, X)
    rule_cf = M.scanfi_rule_conifer(eg)
    pooled = M.regression_metrics(y[train], oof[train])
    pooled_rule = M.regression_metrics(y[train], rule_cf[train])
    pooled_in = M.regression_metrics(y[train], insample[train])
    pooled_raw = M.regression_metrics(y[train], oof_raw[train])
    model_report = {
        "n_train_cells": int(train.sum()), "features": names,
        "importance_gain": dict(sorted(zip(names, imp.tolist()), key=lambda t: -t[1])),
        "cv_per_fold": per_fold, "cv_pooled": pooled, "cv_pooled_unmapped": pooled_raw, "scanfi_rule": pooled_rule, "in_sample": pooled_in,
        "block_m": C.PARAMS["block_m"], "n_folds": C.PARAMS["n_folds"],
    }
    WORK.mkdir(parents=True, exist_ok=True)
    (C.VAL_DIR / "conifer_model.json").write_text(json.dumps(model_report, indent=2))
    np.savez_compressed(WORK / "edmonton_conifer_variants.npz", oof=oof, oof_raw=oof_raw, insample=insample, scanfi_rule=rule_cf,
                        folds=M.block_folds(eg), train=train, label=y)
    x = key_inputs("edmonton", eg, oof)
    cls, rule, stands = R.decision_key(x)
    summary["edmonton"] = write_outputs("edmonton", eg, cls, rule, oof, stands,
                                        {"conifer_model": "LightGBM, out-of-fold (2 km spatial blocks, 5 folds)"})
    # variants for validation only (not products)
    for name, cf in (("insample", insample), ("scanfi_rule", rule_cf)):
        c2, r2, s2 = R.decision_key(key_inputs("edmonton", eg, cf))
        np.savez_compressed(WORK / f"edmonton_{name}.npz", cls=c2, rule=r2, conifer_frac=cf, stands=s2)
    # ── St. Albert: model fitted on all Edmonton labels ──
    sg = aoi_grid("st_albert")
    Xs, _ = M.features(sg)
    cf_s = M.predict(full, Xs)
    xs = key_inputs("st_albert", sg, cf_s)
    cls_s, rule_s, stands_s = R.decision_key(xs)
    summary["st_albert"] = write_outputs("st_albert", sg, cls_s, rule_s, cf_s, stands_s,
                                         {"conifer_model": "LightGBM fitted on all Edmonton LiDAR labels"})
    (C.VAL_DIR / "build_summary.json").write_text(json.dumps(summary, indent=2))
    return summary
