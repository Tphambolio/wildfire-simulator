"""Edmonton-only training / validation labels from the City LiDAR crown tables.

Reads the City crown tables (``FUELGRID_LIDAR_DIR``, read-only) and aggregates them to the 20 m
Edmonton grid: crown count, crown area, conifer crown count and conifer crown area (conifer /
deciduous label from the City's v2 Random Forest; crowns without a v2 label count as deciduous,
exactly as the City fuel grid did). The output stays in ``FUELGRID_DATA/labels`` (outside the
repository); only aggregate statistics derived from it are reported.
"""

from __future__ import annotations

import logging

import numpy as np

try:
    from . import config as C
    from .grid import Grid
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    from grid import Grid  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.labels")

LABELS = C.LABEL_DIR / "edmonton_crown_cells.npz"

# Crown-area sum -> canopy-area factor (overlapping watershed crowns; calibrated in the 2026-10-07
# grid audit against the City canopy-cover analysis: 11,956 ha canopy / 21,053 ha crown area).
CROWN_AREA_TO_CANOPY = 11956 / 21053.0


def build_labels(force: bool = False) -> None:
    if LABELS.exists() and not force:
        return
    import pandas as pd
    trees = C.LIDAR_DIR / "individual_trees.csv"
    v2 = C.LIDAR_DIR / "trees_classified_v2.csv"
    if not trees.exists():
        raise FileNotFoundError(f"City crown table not found: {trees} (set FUELGRID_LIDAR_DIR)")
    log.info("reading crowns ...")
    df = pd.read_csv(trees, usecols=["tile", "tree_id", "centroid_x", "centroid_y", "crown_area_m2", "veg_type"])
    lab = pd.read_csv(v2, usecols=["tile", "tree_id", "leaf_type_v2"])
    df = df.merge(lab, on=["tile", "tree_id"], how="left")
    g = Grid.from_bounds(C.EDMONTON_BOUNDS)
    col = np.floor((df.centroid_x.to_numpy() - g.x0) / g.cell).astype(np.int64)
    row = np.floor((g.y0 - df.centroid_y.to_numpy()) / g.cell).astype(np.int64)
    ok = (col >= 0) & (col < g.width) & (row >= 0) & (row < g.height)
    df, col, row = df[ok], col[ok], row[ok]
    idx = row * g.width + col
    n = g.width * g.height
    area = df.crown_area_m2.to_numpy(np.float64)
    con = (df.leaf_type_v2 == "conifer").to_numpy()
    nolab = df.leaf_type_v2.isna().to_numpy()
    out = {
        "cnt": np.bincount(idx, minlength=n),
        "area": np.bincount(idx, weights=area, minlength=n),
        "con": np.bincount(idx[con], minlength=n),
        "con_area": np.bincount(idx[con], weights=area[con], minlength=n),
        "nolabel": np.bincount(idx[nolab], minlength=n),
    }
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(LABELS, **{k: v.reshape(g.shape).astype(np.float32) for k, v in out.items()},
                        x0=g.x0, y0=g.y0)
    log.info("labels: %d crowns on %d cells", len(df), int((out["cnt"] > 0).sum()))


def load_labels() -> dict[str, np.ndarray]:
    d = np.load(LABELS)
    lab = {k: d[k] for k in ("cnt", "area", "con", "con_area", "nolabel")}
    with np.errstate(invalid="ignore", divide="ignore"):
        # Same definition as the City fuel grid: conifer crowns / all crowns (count based)
        lab["conifer_frac"] = np.where(lab["cnt"] > 0, lab["con"] / lab["cnt"], np.nan)
        lab["conifer_frac_area"] = np.where(lab["area"] > 0, lab["con_area"] / lab["area"], np.nan)
    lab["cover"] = np.minimum(lab["area"] * CROWN_AREA_TO_CANOPY / (C.CELL * C.CELL), 1.0)
    return lab
