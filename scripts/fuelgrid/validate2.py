"""Validation of the v2 grids: change from v1 (Edmonton, St. Albert), comparison with the CFS
national layers (2026 30 m; 2024 100 m) and with the Alberta Vegetation Inventory (AVI Crown) in
the northern test areas, and a FireSim engine check of every new code.

Agreement with another map is not accuracy: none of the reference layers is field-checked here,
and the CFS 2026 layer includes the test fires themselves (burned after the pre-fire map date),
so test-fire perimeters and later burns are excluded from the CFS 2026 comparison.
"""

from __future__ import annotations

import json
import logging

import numpy as np
from rasterio.features import rasterize

try:
    from . import config as C
    from . import key2 as K
    from . import rules as R
    from .build2 import FOREST_RULES, V1_WORK, WORK2, edmonton_unit_data, file_stem, remap_units
    from .grid import read_tif
    from .sources import aoi_grid
    from .sources2 import burn_years, regions, test_area_grid
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    import key2 as K  # type: ignore[no-redef]
    import rules as R  # type: ignore[no-redef]
    from build2 import FOREST_RULES, V1_WORK, WORK2, edmonton_unit_data, file_stem, remap_units  # type: ignore[no-redef]
    from grid import read_tif  # type: ignore[no-redef]
    from sources import aoi_grid  # type: ignore[no-redef]
    from sources2 import burn_years, regions, test_area_grid  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.validate2")
HA = C.CELL * C.CELL / 1e4

# Fine labels shared by all maps (CFS C-5 and C-6 kept separate; they do not occur in our key)
FINE = ["C-1", "C-2", "C-3", "C-4", "C-5", "C-7", "D", "M", "O-1a", "O-1b", "S", "NF", "Water"]
GROUPS = ["Spruce (C-1/C-2)", "Pine (C-3/C-4)", "Other conifer (C-5/C-7)", "Deciduous", "Mixedwood",
          "Open (O-1)", "Slash", "Non-fuel", "Water"]
FINE_TO_GROUP = {"C-1": GROUPS[0], "C-2": GROUPS[0], "C-3": GROUPS[1], "C-4": GROUPS[1], "C-5": GROUPS[2],
                 "C-7": GROUPS[2], "D": GROUPS[3], "M": GROUPS[4], "O-1a": GROUPS[5], "O-1b": GROUPS[5],
                 "S": GROUPS[6], "NF": GROUPS[7], "Water": GROUPS[8]}
OURS_FINE = {C.C1: "C-1", C.C2: "C-2", C.C3: "C-3", C.C4: "C-4", C.C7: "C-7", C.D1: "D", C.D2: "D",
             C.M1: "M", C.M2: "M", C.O1A: "O-1a", C.O1B: "O-1b", C.S1: "S", C.S2: "S", C.NF: "NF",
             C.WATER: "Water"}


def cfs_fine(code: np.ndarray) -> np.ndarray:
    """CIFFC codes (CFS 2024 / 2026 national layers) -> fine labels; '' = no data."""
    out = np.full(code.shape, "", object)
    for k, lab in ((1, "C-1"), (2, "C-2"), (3, "C-3"), (4, "C-4"), (5, "C-5"), (6, "C-5"), (7, "C-7"),
                   (31, "O-1a"), (32, "O-1b"), (102, "Water")):
        out[code == k] = lab
    out[np.isin(code, [11, 12, 13])] = "D"
    out[np.isin(code, [40, 50, 60, 70, 80, 90]) | ((code >= 400) & (code < 1000))] = "M"
    out[np.isin(code, [21, 22, 23])] = "S"
    out[((code >= 100) & (code < 200) & (code != 102)) | (code == 1000)] = "NF"
    return out


def mapcodes(a: np.ndarray, table: dict) -> np.ndarray:
    out = np.full(a.shape, "", object)
    for k, v in table.items():
        out[a == k] = v
    return out


def to_group(fine: np.ndarray) -> np.ndarray:
    out = np.full(fine.shape, "", object)
    for k, v in FINE_TO_GROUP.items():
        out[fine == k] = v
    return out


def compare(ref: np.ndarray, pred: np.ndarray, labels: list[str], mask: np.ndarray | None = None) -> dict:
    m = (ref != "") & (pred != "")
    if mask is not None:
        m &= mask
    idx = {lab: i for i, lab in enumerate(labels)}
    r = np.array([idx.get(v, -1) for v in ref[m]], np.int64)
    p = np.array([idx.get(v, -1) for v in pred[m]], np.int64)
    ok = (r >= 0) & (p >= 0)
    n = len(labels)
    cm = np.bincount(r[ok] * n + p[ok], minlength=n * n).reshape(n, n)
    a = R.agreement(cm)
    keep = [i for i in range(n) if cm[i].sum() or cm[:, i].sum()]
    return {"labels": [labels[i] for i in keep], "oa": round(a["oa"], 3), "kappa": round(a["kappa"], 3),
            "n_cells": a["n"], "area_ha": round(a["n"] * HA, 1),
            "confusion_ha": (cm[np.ix_(keep, keep)] * HA).round(1).tolist(),
            "area_ref_ha": {labels[i]: round(float(cm[i].sum() * HA), 1) for i in keep},
            "area_pred_ha": {labels[i]: round(float(cm[:, i].sum() * HA), 1) for i in keep},
            "recall": {labels[i]: (round(float(cm[i, i] / cm[i].sum()), 3) if cm[i].sum() else None) for i in keep},
            "precision": {labels[i]: (round(float(cm[i, i] / cm[:, i].sum()), 3) if cm[:, i].sum() else None)
                          for i in keep}}


def areas(fine: np.ndarray, labels: list[str], mask: np.ndarray) -> dict:
    return {lab: round(float(((fine == lab) & mask).sum() * HA), 1) for lab in labels if ((fine == lab) & mask).any()}


# ── Edmonton / St. Albert: what v2 changed relative to v1 ────────────────────
def capital_change(key: str) -> dict:
    v1 = np.load(V1_WORK / f"{key}.npz")
    v2 = np.load(WORK2 / f"{key}.npz")
    c1, c2, rule = v1["cls"], v2["cls"], v2["rule"]
    valid = c1 != C.NODATA
    changed = valid & (c1 != c2)
    trans = {}
    for a, b in sorted(set(zip(c1[changed].tolist(), c2[changed].tolist()))):
        m = changed & (c1 == a) & (c2 == b)
        trans[f"{C.CLASS_NAMES_V2[a]} -> {C.CLASS_NAMES_V2[b]}"] = {
            "ha": round(float(m.sum() * HA), 1),
            "rules": {C.RULES_V2[int(r)]: round(float(((rule == r) & m).sum() * HA), 1) for r in np.unique(rule[m])}}
    pc_changed = valid & (c1 == c2) & np.isin(c2, [C.M2]) & (v2["pc"] != R.percent_conifer(v2["conifer_frac"]))
    return {"cells": int(valid.sum()), "area_ha": round(float(valid.sum() * HA), 1),
            "changed_ha": round(float(changed.sum() * HA), 1),
            "changed_pct": round(100 * float(changed.sum()) / max(1, int(valid.sum())), 2),
            "transitions": trans, "areas_v1_ha": K.class_areas(c1, C.CELL), "areas_v2_ha": K.class_areas(c2, C.CELL),
            "m2_percent_conifer_changed_ha": round(float(pc_changed.sum() * HA), 1),
            "rule_areas_ha": {C.RULES_V2[int(r)]: round(float(((rule == r) & valid).sum() * HA), 1)
                              for r in np.unique(rule[valid])}}


def capital_vs_cfs(key: str) -> dict:
    r = regions()["capital"]
    grid = aoi_grid(key)
    v2 = np.load(WORK2 / f"{key}.npz")
    ours = mapcodes(v2["cls"], OURS_FINE)
    cfs = cfs_fine(read_tif(r.dir / "cfs_fbp_2026.tif", grid, region=r.grid))
    valid = v2["cls"] != C.NODATA
    return {"fine": compare(cfs, ours, FINE, valid), "groups": compare(to_group(cfs), to_group(ours), GROUPS, valid),
            "areas_ours_ha": areas(ours, FINE, valid), "areas_cfs2026_ha": areas(cfs, FINE, valid)}


# Label ceiling (split-half agreement of the City crown labels), measured in the conifer-mapping
# report 2026-10-10 §2.1 (~/dev/wildfire/reports/Conifer vs deciduous mapping ... 2026-10-10.md)
LABEL_CEILING = {"20 m": 0.33, "100 m": 0.72}


def _cmd(v: np.ndarray, p: dict = C.PARAMS_V2) -> np.ndarray:
    out = np.full(v.shape, "", object)
    out[v >= p["conifer_c"]] = "C"
    out[v <= p["conifer_d"]] = "D"
    out[(v > p["conifer_d"]) & (v < p["conifer_c"])] = "M"
    out[~np.isfinite(v)] = ""
    return out


def edmonton_unit_scores() -> dict:
    """C/M/D agreement of the conifer fraction at 20 m and at the decision unit (all out-of-fold),
    against the City LiDAR crown labels (a classifier's output, not field truth)."""
    grid = aoi_grid("edmonton")
    v2 = np.load(WORK2 / "edmonton.npz")
    typed = np.isin(v2["rule"], FOREST_RULES)
    out = {"label_ceiling_split_half_kappa": LABEL_CEILING, "forest_cells_ha": round(float(typed.sum() * HA), 1)}
    var = np.load(V1_WORK / "edmonton_conifer_variants.npz")
    cell = typed & var["train"]
    out["cell_20m"] = {"all_folds": compare(_cmd(var["label"][cell]), _cmd(var["oof"][cell]), ["C", "M", "D"]),
                       "mae": round(float(np.abs(var["label"][cell] - var["oof"][cell]).mean()), 3)}
    for cells in (5, 10):
        u = edmonton_unit_data(grid, typed, cells)
        mapped, table = remap_units(u)
        ok = (u["crowns"] >= C.PARAMS_V2["unit_min_crowns"]) & np.isfinite(u["label"]) & np.isfinite(mapped)
        res = {"units": int(ok.sum()), "train_units_full_table": table["n_train_units"],
               "remapped": compare(_cmd(u["label"][ok]), _cmd(mapped[ok]), ["C", "M", "D"]),
               "unit_mean_not_remapped": compare(_cmd(u["label"][ok]), _cmd(u["pred"][ok]), ["C", "M", "D"]),
               "pc_mae": round(float(np.abs(u["label"][ok] - mapped[ok]).mean()), 3),
               "per_fold_kappa": []}
        for k in range(C.PARAMS["n_folds"]):
            f = ok & (u["fold"] == k)
            res["per_fold_kappa"].append(compare(_cmd(u["label"][f]), _cmd(mapped[f]), ["C", "M", "D"])["kappa"])
        lab = _cmd(u["label"][ok])
        res["label_share_of_unit_area_pct"] = {c: round(100 * float((lab == c).mean()), 1) for c in "CMD"}
        out[f"unit_{cells * 20}m"] = res
    return out


# ── northern test areas ──────────────────────────────────────────────────────
AVI_CONIFER = {"Sw", "Se", "Sb", "Fb", "Fa", "Fd", "P", "Pl", "Pj", "Pf", "Pw", "Pa"}
AVI_PINE = {"P", "Pl", "Pj"}


def avi_groups(grid, photo_after: int | None = None) -> tuple[np.ndarray, dict]:
    """AVI Crown polygons -> comparison groups on the grid (crosswalk [H], docs §6b).

    Forest polygons: conifer share from SP1-SP5 percentages (tenths; larch counted deciduous, as
    tamarack in CFS 2018); >= 75 % conifer -> pine (leading conifer P/Pl/Pj) or spruce/fir
    (Fd-leading -> other conifer); <= 25 % -> deciduous; else mixedwood. Non-forest: NAT_NON water
    (NW*) -> water, other NAT_NON / ANTH_NON -> non-fuel, NFL shrub/herb/bryoid and ANTH_VEG -> open.
    Polygons with a burn or clear-cut modifier are dropped.
    """
    import pyogrio
    cols = ["SP1", "SP1_PER", "SP2", "SP2_PER", "SP3", "SP3_PER", "SP4", "SP4_PER", "SP5", "SP5_PER",
            "NFL", "NAT_NON", "ANTH_VEG", "ANTH_NON", "MOD1", "MOD2", "PHOTO_YR", "DENSITY", "HEIGHT"]
    df = pyogrio.read_dataframe(C.AVI_GDB, layer="AVI_Crown", bbox=grid.bounds, columns=cols)
    df = df.to_crs(grid.crs)
    lab = np.full(len(df), "", object)
    con = np.zeros(len(df))
    pine = np.zeros(len(df))
    for i in range(1, 6):
        sp, per = df[f"SP{i}"].fillna("").astype(str), df[f"SP{i}_PER"].fillna(0).astype(float)
        con += np.where(sp.isin(AVI_CONIFER), per, 0)
        pine += np.where(sp.isin(AVI_PINE), per, 0)
    tot = sum(df[f"SP{i}_PER"].fillna(0).astype(float) for i in range(1, 6)).to_numpy()
    forest = (df.SP1.fillna("") != "").to_numpy() & (tot > 0)
    share = np.where(tot > 0, con / np.maximum(tot, 1), 0)
    lead_pine = df.SP1.fillna("").isin(AVI_PINE).to_numpy() | (pine >= 0.5 * np.maximum(con, 1e-9))
    lead_fd = (df.SP1.fillna("") == "Fd").to_numpy()
    lab[forest & (share >= 0.75) & lead_fd] = GROUPS[2]
    lab[forest & (share >= 0.75) & ~lead_fd & lead_pine] = GROUPS[1]
    lab[forest & (share >= 0.75) & ~lead_fd & ~lead_pine] = GROUPS[0]
    lab[forest & (share <= 0.25)] = GROUPS[3]
    lab[forest & (share > 0.25) & (share < 0.75)] = GROUPS[4]
    nat = df.NAT_NON.fillna("").astype(str).to_numpy()
    nonf = ~forest
    lab[nonf & (np.char.startswith(nat.astype(str), "NW"))] = GROUPS[8]
    lab[nonf & (nat != "") & ~np.char.startswith(nat.astype(str), "NW")] = GROUPS[7]
    lab[nonf & (df.ANTH_NON.fillna("") != "").to_numpy()] = GROUPS[7]
    lab[nonf & ((df.NFL.fillna("") != "") | (df.ANTH_VEG.fillna("") != "")).to_numpy() & (lab == "")] = GROUPS[5]
    disturbed = df.MOD1.fillna("").isin(["BU", "CC"]).to_numpy() | df.MOD2.fillna("").isin(["BU", "CC"]).to_numpy()
    lab[disturbed] = ""
    codes = {g: i + 1 for i, g in enumerate(GROUPS)}
    shapes = [(geom, codes[g]) for geom, g in zip(df.geometry, lab) if g and geom is not None]
    ras = rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8") if shapes \
        else np.zeros(grid.shape, np.uint8)
    out = np.full(grid.shape, "", object)
    for g, i in codes.items():
        out[ras == i] = g
    photo = df.PHOTO_YR.fillna(0).astype(int)
    info = {"polygons": len(df), "photo_years": photo.value_counts().sort_index().to_dict(),
            "dropped_disturbed": int(disturbed.sum())}
    return out, info


def test_area(key: str) -> dict:
    t = C.TEST_AREAS[key]
    r = regions()[t.region]
    grid = test_area_grid(key)
    v2 = np.load(WORK2 / f"{key}.npz")
    cls, rule = v2["cls"], v2["rule"]
    ours = mapcodes(cls, OURS_FINE)
    valid = cls != C.NODATA
    burns = burn_years(r, grid)
    later = np.zeros(grid.shape, bool)
    for y, m in burns:
        if y >= t.year:
            later |= m
    fire = np.zeros(grid.shape, bool)
    for y, m in burns:
        if y == t.year:
            fire |= m
    loss = read_tif(r.dir / "hansen_lossyear.tif", grid, region=r.grid)
    out = {"fire_id": t.fire_id, "map_year": t.year, "grid": [grid.height, grid.width], "crs": grid.crs,
           "area_ha": round(float(valid.sum() * HA), 1),
           "fire_perimeter_ha": round(float(fire.sum() * HA), 1),
           "areas_ha": K.class_areas(cls, C.CELL),
           "areas_leafoff_ha": K.class_areas(K.leaf_off_v2(cls), C.CELL),
           "areas_in_fire_ha": K.class_areas(np.where(fire, cls, C.NODATA).astype(np.uint8), C.CELL),
           "rule_areas_ha": {C.RULES_V2[int(q)]: round(float(((rule == q) & valid).sum() * HA), 1)
                             for q in np.unique(rule[valid])},
           "m_percent_conifer_ha": {int(p): round(float(((v2["pc"] == p) & np.isin(cls, [C.M2])).sum() * HA), 1)
                                    for p in np.unique(v2["pc"][np.isin(cls, [C.M2])])}}
    for name in ("cfs_fbp_2026", "cfs_fbp_2024"):
        path = r.dir / f"{name}.tif"
        if not path.exists():
            continue
        cfs = cfs_fine(read_tif(path, grid, region=r.grid))
        # CFS 2026 includes the test fire and later burns; CFS 2024 (100 m, May 2024) predates 2024 fires
        # CFS 2026 includes burns to 2025, CFS 2024 (May 2024) burns to 2023: exclude every burn of the
        # map year or later that the layer already contains (the test fire itself and later fires)
        last_burn_in_layer = 2025 if name == "cfs_fbp_2026" else 2023
        excl = np.zeros(grid.shape, bool)
        for y, m in burns:
            if t.year <= y <= last_burn_in_layer:
                excl |= m
        pre_fire_layer = last_burn_in_layer < t.year
        mask = valid & ~excl
        both_con = mask & np.isin(ours, ["C-1", "C-2", "C-3", "C-4", "C-7"]) & \
            np.isin(cfs, ["C-1", "C-2", "C-3", "C-4", "C-5", "C-7"])
        out[name] = {
            "excluded_burns_ge_map_year_ha": round(float((valid & excl).sum() * HA), 1),
            "fine": compare(cfs, ours, FINE, mask),
            "groups": compare(to_group(cfs), to_group(ours), GROUPS, mask),
            "pine_vs_spruce_where_both_conifer": compare(to_group(cfs), to_group(ours), GROUPS[:3], both_con),
            "areas_cfs_ha": areas(cfs, FINE, mask), "areas_ours_ha": areas(ours, FINE, mask),
            "pre_fire_layer": pre_fire_layer,
            "in_fire": (compare(to_group(cfs), to_group(ours), GROUPS, valid & fire) if pre_fire_layer else None)}
    if C.AVI_GDB.exists():
        avi, info = avi_groups(grid)
        stable = valid & ~later & (loss == 0)
        for y, m in burns:  # drop everything burned since 1990 (after most AVI photo years)
            if y >= 1990:
                stable &= ~m
        g_ours = to_group(ours)
        res = {"info": info, "stable_ha": round(float((stable & (avi != "")).sum() * HA), 1),
               "ours_vs_avi": compare(avi, g_ours, GROUPS, stable)}
        for name in ("cfs_fbp_2026", "cfs_fbp_2024"):
            path = r.dir / f"{name}.tif"
            if path.exists():
                res[f"{name}_vs_avi"] = compare(avi, to_group(cfs_fine(read_tif(path, grid, region=r.grid))),
                                                GROUPS, stable)
        out["avi"] = res
    return out


# ── FireSim engine check ─────────────────────────────────────────────────────
def firesim_check_v2() -> dict:
    """Load each v2 grid with code_scheme='cfs_national' and run 1 h of spread from the interior of
    the largest patch of every class present (needs PYTHONPATH=engine/src)."""
    from pyproj import Transformer
    from scipy import ndimage as ndi

    from firesim.data.fuel_loader import load_fuel_grid
    from firesim.spread.simulator import Simulator
    from firesim.types import SimulationConfig, WeatherInput

    res = {}
    keys = [(k, aoi_grid(k)) for k in C.AOIS] + [(k, test_area_grid(k)) for k in C.TEST_AREAS]
    for key, grid in keys:
        v2 = np.load(WORK2 / f"{key}.npz")
        tr = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
        for season, doy in (("leafon", 200), ("leafoff", 120)):
            cls = v2["cls"] if season == "leafon" else K.leaf_off_v2(v2["cls"])
            path = C.OUT_DIR_V2 / f"{file_stem(key, f'{season}_ciffc', grid)}.tif"
            fg = load_fuel_grid(str(path), target_resolution_m=C.CELL, code_scheme="cfs_national")
            present = {}
            for k in np.unique(cls):
                if k in (C.NODATA, C.NF, C.WATER):
                    continue
                m = ndi.binary_erosion(cls == k, iterations=2)
                if not m.any():
                    m = cls == k
                lab, _ = ndi.label(m)
                sizes = np.bincount(lab.ravel())
                sizes[0] = 0
                rr, cc = np.nonzero(lab == sizes.argmax())
                i = np.argmin((rr - rr.mean()) ** 2 + (cc - cc.mean()) ** 2)
                x = grid.x0 + (cc[i] + 0.5) * grid.cell
                y = grid.y0 - (rr[i] + 0.5) * grid.cell
                lng, lat = tr.transform(x, y)
                fuel = fg.get_fuel_at(lat, lng)
                cfg = SimulationConfig(ignition_lat=lat, ignition_lng=lng,
                                       weather=WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0),
                                       duration_hours=1.0, snapshot_interval_minutes=60.0,
                                       ffmc=92.0, dmc=40.0, dc=300.0, grass_cure=90.0, day_of_year=doy)
                last = list(Simulator(cfg, fuel_grid=fg).run())[-1]
                present[C.CLASS_NAMES_V2[int(k)]] = {
                    "fuel_at_ignition": getattr(fuel, "value", None), "area_ha_1h": round(last.area_ha, 2),
                    "head_ros_m_min": round(float(last.head_ros_m_min or 0), 2)}
            res[f"{key}/{season}"] = {"file": path.name, "grid_cells": [fg.rows, fg.cols], "classes": present}
            log.info("%s %s: %s", key, season, present)
    C.VAL_DIR_V2.mkdir(parents=True, exist_ok=True)
    (C.VAL_DIR_V2 / "firesim_check_v2.json").write_text(json.dumps(res, indent=2))
    return res


def validate_all_v2() -> dict:
    out = {"capital": {k: {"change_from_v1": capital_change(k), "vs_cfs_2026": capital_vs_cfs(k)} for k in C.AOIS},
           "edmonton_unit_scores": edmonton_unit_scores(),
           "test_areas": {k: test_area(k) for k in C.TEST_AREAS}}
    C.VAL_DIR_V2.mkdir(parents=True, exist_ok=True)
    (C.VAL_DIR_V2 / "validation_v2.json").write_text(json.dumps(out, indent=2, default=str))
    return out
