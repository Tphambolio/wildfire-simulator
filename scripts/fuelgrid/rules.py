"""Decision key for the open-data FBP fuel grid (pure numpy / scipy; unit-tested).

All inputs are 2-D arrays on the same 20 m grid. Fractions are 0-1. The key is ordered: the
first rule that matches a cell sets its class, and the rule number is kept per cell so every
class can be traced to the evidence that produced it (``config.RULES``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

try:  # package import (scripts.fuelgrid.rules) or script import (rules)
    from . import config as C
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]


def mean3x3(a: np.ndarray) -> np.ndarray:
    """3 x 3 moving mean (edges use the nearest value)."""
    return ndi.uniform_filter(a.astype(np.float32), size=3, mode="nearest")


def built_mask(bld_frac: np.ndarray, wc_built: np.ndarray, p: dict = C.PARAMS) -> np.ndarray:
    """Cells over buildings or hardscape: own footprint share, 60 m footprint share, or WorldCover."""
    return ((bld_frac >= p["built_cell_frac"])
            | (mean3x3(bld_frac) >= p["built_3x3_frac"])
            | (wc_built >= p["wc_built_frac"]))


def stand_mask(cover: np.ndarray, built: np.ndarray, valid: np.ndarray | None = None,
               p: dict = C.PARAMS, cell_m: float = C.CELL) -> np.ndarray:
    """Contiguous treed stands: 3x3 mean canopy >= stand_cover, not built, patch >= stand_min_ha.

    Canopy over buildings or hardscape never forms or joins a stand, so street and yard trees
    stay non-fuel while ravine, riparian and park woodlands are typed by their vegetation
    whether or not an inventory polygon covers them. Patches are 8-connected.
    """
    cand = (mean3x3(cover) >= p["stand_cover"]) & ~built
    if valid is not None:
        cand &= valid
    lab, n = ndi.label(cand, structure=np.ones((3, 3), bool))
    if n == 0:
        return np.zeros_like(cand)
    sizes = np.bincount(lab.ravel())
    min_cells = int(np.ceil(p["stand_min_ha"] * 1e4 / (cell_m * cell_m) - 1e-9))
    keep = sizes >= min_cells
    keep[0] = False
    return keep[lab]


def forest_type(conifer_frac: np.ndarray, p: dict = C.PARAMS) -> np.ndarray:
    """C-2 / M-2 / D-2 from conifer fraction (>= conifer_c C-2, <= conifer_d D-2, else M-2)."""
    out = np.full(conifer_frac.shape, C.M2, np.uint8)
    out[conifer_frac >= p["conifer_c"]] = C.C2
    out[conifer_frac <= p["conifer_d"]] = C.D2
    return out


@dataclass
class KeyInputs:
    """Per-cell inputs of the decision key (all on one grid)."""
    valid: np.ndarray          # bool: inside the AOI
    cover: np.ndarray          # canopy cover fraction (CHM >= tree height)
    conifer_frac: np.ndarray   # predicted conifer fraction of the canopy (0-1)
    bld_frac: np.ndarray       # building footprint fraction of the cell
    wc: dict[int, np.ndarray]  # WorldCover class -> fraction of the cell
    aci: np.ndarray            # AAFC ACI class code
    osm: np.ndarray            # OSM category (config.OSM_*)


def decision_key(x: KeyInputs, p: dict = C.PARAMS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (class, rule, stand) arrays. Class values are config.C2 ... config.WATER."""
    shape = x.valid.shape
    cls = np.full(shape, C.NODATA, np.uint8)
    rule = np.zeros(shape, np.uint8)
    todo = x.valid.copy()

    def assign(mask: np.ndarray, value, rule_id: int) -> None:
        m = mask & todo
        if np.isscalar(value):
            cls[m] = value
        else:
            cls[m] = value[m]
        rule[m] = rule_id
        todo[m] = False

    zero = np.zeros(shape, np.float32)
    wc = {k: x.wc.get(k, zero) for k in C.WC_BANDS}
    built = built_mask(x.bld_frac, wc[50], p)
    stands = stand_mask(x.cover, built, x.valid, p)

    # 1 water
    assign(wc[80] >= p["water_frac"], C.WATER, 1)
    # 2-3 treed stands (contiguous canopy, not over buildings / hardscape)
    assign(stands & (x.cover >= p["treed_cell_cover"]), forest_type(x.conifer_frac, p), 2)
    assign(stands, C.O1B, 3)
    # 4 mapped natural open vegetation (OSM natural=scrub/heath/grassland/wetland, meadow)
    assign((x.osm == C.OSM_NATURAL_OPEN) & ~built, C.O1B, 4)
    # 5 built-up or maintained urban land (lawns, parks, sports fields, yards, hardscape)
    urban = (built | (x.osm == C.OSM_URBAN) | (x.osm == C.OSM_MAINTAINED)
             | np.isin(x.aci, list(C.ACI_URBAN)))
    assign(urban, C.NF, 5)
    # 6 annual crops and pasture / forage
    assign(np.isin(x.aci, list(C.ACI_CROP | C.ACI_PASTURE)) | (x.osm == C.OSM_FARMLAND), C.O1A, 6)
    # 7 natural grass, shrub, wetland, or treed land outside stands (small woodlots, open woods)
    natural = (np.isin(x.aci, list(C.ACI_NATURAL_OPEN | C.ACI_FOREST))
               | ((wc[20] + wc[30] + wc[90] + wc[100] + wc[10]) >= 0.5)
               | (x.osm == C.OSM_WOOD))
    assign(natural, C.O1B, 7)
    # 8 everything else (bare, unclassified) is non-fuel
    assign(np.ones(shape, bool), C.NF, 8)
    return cls, rule, stands


def leaf_off(cls: np.ndarray) -> np.ndarray:
    """Leafless variant: D-2 -> D-1 and M-2 -> M-1 (same percent conifer); others unchanged."""
    out = cls.copy()
    out[cls == C.D2] = C.D1
    out[cls == C.M2] = C.M1
    return out


def season_for_doy(doy: int, greenup_doy: int = C.GREENUP_DOY, leafoff_doy: int = C.LEAFOFF_DOY) -> str:
    """'leafon' from green-up (inclusive) to leaf fall (exclusive), else 'leafoff'."""
    return "leafon" if greenup_doy <= doy < leafoff_doy else "leafoff"


def percent_conifer(conifer_frac: np.ndarray, step: int = 5) -> np.ndarray:
    """Percent conifer rounded to `step`, clipped to 5-95 (CIFFC mixedwood codes allow 5-95)."""
    pc = np.round(np.clip(conifer_frac, 0, 1) * 100 / step) * step
    return np.clip(pc, 5, 95).astype(np.int16)


def encode_canopy_lidar(cls: np.ndarray) -> np.ndarray:
    """FireSim `canopy_lidar` codes (leaf-on only: the scheme has no D-1 / M-1)."""
    if np.isin(cls, [C.D1, C.M1]).any():
        raise ValueError("canopy_lidar scheme has no leafless codes; encode the leaf-on grid")
    out = np.zeros(cls.shape, np.int16)
    for k, v in C.CANOPY_LIDAR.items():
        out[cls == k] = v
    return out


def encode_ciffc(cls: np.ndarray, conifer_frac: np.ndarray) -> np.ndarray:
    """FireSim `cfs_national` (CIFFC) codes; M-1 = 4xx, M-2 = 5xx with xx = percent conifer."""
    out = np.zeros(cls.shape, np.int16)
    for k, v in C.CIFFC.items():
        out[cls == k] = v
    pc = percent_conifer(conifer_frac)
    out[cls == C.M1] = C.CIFFC_M1_BASE + pc[cls == C.M1]
    out[cls == C.M2] = C.CIFFC_M2_BASE + pc[cls == C.M2]
    return out


# ── Agreement statistics (used by validation; tested on small arrays) ─────────
def confusion(ref: np.ndarray, pred: np.ndarray, labels: list) -> np.ndarray:
    """Confusion matrix (rows = reference, cols = prediction) over the given labels."""
    idx = {lab: i for i, lab in enumerate(labels)}
    n = len(labels)
    r, q = _index(np.asarray(ref), idx), _index(np.asarray(pred), idx)
    ok = (r >= 0) & (q >= 0)
    return np.bincount(r[ok] * n + q[ok], minlength=n * n).reshape(n, n)


def _index(a: np.ndarray, idx: dict) -> np.ndarray:
    out = np.full(a.shape, -1, np.int64)
    for k, i in idx.items():
        out[a == k] = i
    return out


def agreement(cm: np.ndarray) -> dict:
    """Overall agreement, Cohen's kappa, per-class precision (user's) and recall (producer's)."""
    cm = cm.astype(float)
    tot = cm.sum()
    if tot == 0:
        return {"oa": float("nan"), "kappa": float("nan"), "precision": [], "recall": [], "n": 0}
    po = np.trace(cm) / tot
    pe = (cm.sum(0) * cm.sum(1)).sum() / tot ** 2
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    with np.errstate(invalid="ignore", divide="ignore"):
        precision = np.diag(cm) / cm.sum(0)
        recall = np.diag(cm) / cm.sum(1)
    return {"oa": float(po), "kappa": float(kappa), "precision": precision.tolist(),
            "recall": recall.tolist(), "n": int(tot)}
