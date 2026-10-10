"""Full FBP decision key (v2) for the open-data fuel grid: pure numpy, unit-tested.

v2 = the v1 land-cover gate (``rules.decision_key``) + four additions:

1. **Forest typing** inside treed stands (v1 rule 2 split): C-1, C-2, C-3, C-4, C-7, D-1/D-2,
   M-1/M-2 from conifer fraction, species shares, height and density.
2. **Open conifer** outside stands (sparse or short conifer, treed peatland): C-1 or M-1/M-2 25 %.
3. **Herbaceous wetland / peatland** relabelled (provenance only; still O-1b).
4. **Disturbance**: years since fire (CFS 2026 Table 3) and years since harvest (slash S-1/S-2,
   Perrakis et al. 2015), the more recent disturbance winning.

Every threshold lives in ``config.PARAMS_V2``; sources and FireSim choices ([H]) are listed in
``docs/fuel-grid-open-data.md`` §4b. The key is ordered, and each cell keeps the number of the
rule that set it (``config.RULES_V2``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:  # package import (scripts.fuelgrid.key2) or script import (key2)
    from . import config as C
    from . import rules as R
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    import rules as R  # type: ignore[no-redef]

P2 = C.PARAMS_V2


@dataclass
class Species:
    """Shares of the cell's tree composition (0-1; each species' crown closure / total)."""
    pine: np.ndarray           # jack + lodgepole pine
    black_spruce: np.ndarray
    douglas_fir: np.ndarray
    ponderosa: np.ndarray


def species_from_scanfi(sps: dict[str, np.ndarray]) -> tuple[np.ndarray, Species, np.ndarray]:
    """(conifer fraction, Species, pine share of conifer) from SCANFI v2 species crown closure.

    Tamarack counts as deciduous (CFS 2018 decision rules p.1: "tamarack as deciduous"); its
    needles are shed, and in the FBP sense it behaves as a leafless overstorey. Cells with no
    species crown closure get conifer fraction 0 and zero shares.
    """
    g = {k: np.nan_to_num(np.asarray(v, np.float32)) for k, v in sps.items()}
    con_keys = ("blackSpruce", "otherConiferous", "balsamFir", "jackPine", "lodgepolePine",
                "whiteRedPine", "douglasFir", "ponderosaPine")
    conifer = sum(g[k] for k in con_keys)
    total = conifer + g["broadleaf"] + g["tamarack"]
    with np.errstate(invalid="ignore", divide="ignore"):
        inv = np.where(total > 0, 1.0 / total, 0.0)
        pine = g["jackPine"] + g["lodgepolePine"]
        sp = Species(pine=pine * inv, black_spruce=g["blackSpruce"] * inv,
                     douglas_fir=g["douglasFir"] * inv, ponderosa=g["ponderosaPine"] * inv)
        pine_of_conifer = np.where(conifer > 0, pine / np.where(conifer > 0, conifer, 1), 0.0)
    return (conifer * inv).astype(np.float32), sp, pine_of_conifer.astype(np.float32)


def forest_type_v2(conifer_frac: np.ndarray, sp: Species, height: np.ndarray, cover2: np.ndarray,
                   closure: np.ndarray, lichen: np.ndarray, p: dict = P2) -> tuple[np.ndarray, np.ndarray]:
    """(class, rule) for treed-stand cells.

    Order (first match): deciduous (<= 25 % conifer) -> D-2; mixedwood -> M-2; conifer (>= 75 %):
    C-7 (ponderosa pine / open Douglas-fir), then pine (>= 25 % of the cell) -> C-4 if shorter than
    12 m and dense (2 m+ cover >= 60 %) else C-3, then black spruce >= 75 % with lichen >= 1 % and
    closure < 60 % -> C-1, else C-2.
    """
    shape = conifer_frac.shape
    cls = np.full(shape, C.M2, np.uint8)
    rule = np.full(shape, 26, np.uint8)
    decid = conifer_frac <= p["conifer_d"]
    con = conifer_frac >= p["conifer_c"]
    c7 = con & ((sp.ponderosa >= p["c7_ponderosa"])
                | ((sp.ponderosa >= p["c7_ponderosa_mixed"]) & (sp.douglas_fir >= p["c7_douglas_fir"]))
                | ((sp.douglas_fir >= p["c7_df_open"]) & (closure <= p["c7_df_open_closure"])))
    pine = con & ~c7 & (sp.pine >= p["pine_share"])
    c4 = pine & (height < p["c4_height_m"]) & (cover2 >= p["c4_cover"])
    c3 = pine & ~c4
    spruce = con & ~c7 & ~pine
    c1 = spruce & (sp.black_spruce >= p["c1_black_spruce"]) & (lichen >= p["c1_lichen"]) \
        & (closure < p["c1_closure"])
    c2 = spruce & ~c1
    for m, k, r in ((decid, C.D2, 25), (c2, C.C2, 20), (c1, C.C1, 21), (c4, C.C4, 22), (c3, C.C3, 23),
                    (c7, C.C7, 24)):
        cls[m] = k
        rule[m] = r
    return cls, rule


@dataclass
class KeyInputsV2:
    """v1 inputs plus the v2 evidence (all on one grid)."""
    base: R.KeyInputs
    species: Species
    height: np.ndarray               # stand height, m (mean height of the 1 m pixels >= 5 m)
    cover2: np.ndarray               # cover of vegetation >= 2 m (0-1)
    closure: np.ndarray              # crown closure for the C-1 / C-7 rules (0-1)
    lichen: np.ndarray               # SCANFI v3 non-treed lichen fraction (0-1)
    treed_con: np.ndarray            # SCANFI v3 treed-coniferous fraction (0-1)
    treed_broad: np.ndarray          # SCANFI v3 treed-broadleaf fraction (0-1)
    years_since_fire: np.ndarray     # map year - most recent burn year before it; -1 = none
    years_since_harvest: np.ndarray  # map year - most recent non-fire forest loss year; -1 = none
    pre_harvest_conifer: np.ndarray  # conifer fraction before the harvest (0-1)
    pre_harvest_pine: np.ndarray     # pine share of the conifer before the harvest (0-1)
    pre_harvest_closure: np.ndarray  # crown closure before the harvest (0-1); blocks must have been treed
    # Optional second treed gate (test areas): SCANFI v3 treed fraction, closure and height (0-1, m)
    alt_treed: np.ndarray | None = None
    alt_closure: np.ndarray | None = None
    alt_height: np.ndarray | None = None
    # Optional unit conifer fraction (per cell, constant within a unit), re-mapped at unit scale
    unit_conifer: np.ndarray | None = None


def unit_ids(shape: tuple[int, int], cells: int) -> np.ndarray:
    """Decision-unit id per cell: square blocks of `cells` x `cells` anchored at the grid origin."""
    r, c = np.indices(shape)
    return (r // cells) * ((shape[1] + cells - 1) // cells) + c // cells


def unit_mean(a: np.ndarray, mask: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Mean of `a` over the `mask` cells of each unit, broadcast back to every cell (NaN if none)."""
    n = int(ids.max()) + 1
    w = np.bincount(ids[mask], minlength=n).astype(np.float64)
    s = np.bincount(ids[mask], weights=np.asarray(a, np.float64)[mask], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(w > 0, s / np.maximum(w, 1), np.nan)
    return mean[ids].astype(np.float32)


def quantile_map(x_train: np.ndarray, y_train: np.ndarray, n: int = 1001) -> tuple[np.ndarray, np.ndarray]:
    """Quantile-mapping table (xp, yp) from training predictions to training labels."""
    q = np.linspace(0, 1, n)
    return np.quantile(x_train, q), np.quantile(y_train, q)


def decision_key_v2(x: KeyInputsV2, p: dict = P2, p1: dict = C.PARAMS
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (class, rule, stand, percent_conifer). Leaf-on classes; see ``leaf_off_v2``.

    percent_conifer is the mixedwood modifier (5-95) for M-1/M-2 cells and 0 elsewhere.
    """
    cls, rule, stands = R.decision_key(x.base, p1)
    cf = x.base.conifer_frac
    pc = np.zeros(cls.shape, np.int16)

    # 1 forest extent at 20 m: treed stands (v1 rule 2) and, in the test areas, SCANFI-treed
    # stands where the canopy model under-reads (rules 60-66; same stand rule on SCANFI v3 treed
    # cover, typed with SCANFI closure / height) [H]
    st = rule == 2
    alt = np.zeros(cls.shape, bool)
    if x.alt_treed is not None:
        zero0 = np.zeros(cls.shape, np.float32)
        built0 = R.built_mask(x.base.bld_frac, x.base.wc.get(50, zero0), p1)
        alt = R.stand_mask(x.alt_treed, built0, x.base.valid, p1) & np.isin(rule, [4, 7])
    typed = st | alt
    height = x.height if x.alt_height is None else np.where(alt, x.alt_height, x.height)
    cover2 = x.cover2 if x.alt_closure is None else np.where(alt, x.alt_closure, x.cover2)
    closure = x.closure if x.alt_closure is None else np.where(alt, x.alt_closure, x.closure)

    # 2 type at the decision unit (stand-level typing): every evidence layer is averaged over the
    # forest cells of each unit (100 m block by default) and the forest key is applied to the
    # means; the 20 m fuel extent is kept. The unit conifer fraction may be supplied already
    # re-mapped at unit scale (Edmonton / St. Albert) via x.unit_conifer.
    ids = unit_ids(cls.shape, int(p["unit_cells"]))
    um = lambda a: unit_mean(a, typed, ids)  # noqa: E731
    ucf = um(cf) if x.unit_conifer is None else x.unit_conifer
    usp = Species(pine=um(x.species.pine), black_spruce=um(x.species.black_spruce),
                  douglas_fir=um(x.species.douglas_fir), ponderosa=um(x.species.ponderosa))
    fcls, frule = forest_type_v2(np.nan_to_num(ucf), usp, um(height), um(cover2), um(closure), um(x.lichen), p)
    cls[typed] = fcls[typed]
    rule[st] = frule[st]
    rule[alt] = frule[alt] + 40
    pc_unit = R.percent_conifer(np.nan_to_num(ucf))
    m = typed & (cls == C.M2)
    pc[m] = pc_unit[m]

    # 2 open conifer outside stands: sparse / short conifer and treed peatland (v1 rules 4, 7)
    zero = np.zeros(cls.shape, np.float32)
    wc = {k: x.base.wc.get(k, zero) for k in C.WC_BANDS}
    built = R.built_mask(x.base.bld_frac, wc[50], p1)
    cand = np.isin(rule, [4, 7]) & ~built
    oc = cand & (x.cover2 >= p["open_conifer_cover2"]) & (x.treed_con >= p["open_conifer_treed"]) \
        & (x.treed_con >= p["open_conifer_ratio"] * x.treed_broad)
    oc1 = oc & (x.species.black_spruce >= p["c1_black_spruce"]) & (x.lichen >= p["c1_lichen"])
    cls[oc1], rule[oc1] = C.C1, 30
    ocm = oc & ~oc1
    cls[ocm], rule[ocm] = C.M2, 31
    pc[ocm] = p["open_conifer_pc"]

    # 3 herbaceous wetland / peatland: provenance relabel only (class stays O-1b)
    wet = (rule == 7) & (((wc[90] + wc[100]) >= p["wetland_frac"]) | np.isin(x.base.aci, [80, 85]))
    rule[wet] = 32

    # 4 disturbance: the more recent of fire and harvest wins
    ysf = np.asarray(x.years_since_fire)
    ysh = np.asarray(x.years_since_harvest)
    has_f = ysf >= 1
    has_h = ysh >= 1
    fire = has_f & (~has_h | (ysf <= ysh))
    harv = has_h & (~has_f | (ysh < ysf))
    forest = np.isin(cls, C.FOREST_V2)
    # fire (CFS 2026 Table 3, p.17): O-1, non-fuel and water are kept
    f = fire & forest
    for lo, hi, k, r in ((1, p["fire_nf_years"], C.NF, 40),
                         (p["fire_nf_years"] + 1, p["fire_d_years"], C.D2, 41),
                         (p["fire_d_years"] + 1, p["fire_m_years"], C.M2, 42)):
        mm = f & (ysf >= lo) & (ysf <= hi)
        cls[mm], rule[mm] = k, r
        pc[mm] = p["fire_m_pc"] if k == C.M2 else 0
    # harvest: slash 1-5 years (Perrakis et al. 2015 §5.4.3), open 6-24 years where not treed again
    h_ok = harv & (forest | np.isin(rule, [3, 7, 8])) & (x.pre_harvest_closure >= p["harvest_pre_closure"])
    slash = h_ok & (ysh <= p["slash_years"])
    dec = slash & (x.pre_harvest_conifer <= p["conifer_d"])
    s1 = slash & ~dec & (x.pre_harvest_pine >= p["slash_pine_share"])
    s2 = slash & ~dec & ~s1
    for mm, k, r in ((dec, C.D2, 52), (s1, C.S1, 50), (s2, C.S2, 51)):
        cls[mm], rule[mm] = k, r
        pc[mm] = 0
    reopen = h_ok & (ysh > p["slash_years"]) & (ysh <= p["harvest_open_years"]) & np.isin(rule, [3, 7, 8])
    cls[reopen], rule[reopen] = C.O1B, 53
    pc[~np.isin(cls, [C.M1, C.M2])] = 0
    return cls, rule, stands, pc


def leaf_off_v2(cls: np.ndarray) -> np.ndarray:
    """Leafless variant: D-2 -> D-1 and M-2 -> M-1 (same percent conifer); others unchanged."""
    return R.leaf_off(cls)


def encode_ciffc_v2(cls: np.ndarray, pc: np.ndarray) -> np.ndarray:
    """CIFFC codes (FireSim ``cfs_national``): C-1..C-7 = 1..7, D-1 11, D-2 12, S-1 21, S-2 22,
    O-1a 31, O-1b 32, M-1 4xx / M-2 5xx with xx = percent conifer (5-95), non-fuel 101, water 102."""
    out = np.zeros(cls.shape, np.int16)
    for k, v in C.CIFFC_V2.items():
        out[cls == k] = v
    pcc = np.clip(np.round(np.asarray(pc) / 5) * 5, 5, 95).astype(np.int16)
    out[cls == C.M1] = C.CIFFC_M1_BASE + pcc[cls == C.M1]
    out[cls == C.M2] = C.CIFFC_M2_BASE + pcc[cls == C.M2]
    return out


def stale_canopy(chm_year: np.ndarray, dist_year: np.ndarray) -> np.ndarray:
    """Cells whose canopy-height imagery pre-dates a later disturbance (fire or forest loss).

    The Meta canopy model mixes imagery from 2010-2020; a stand cut or burned after its image
    date still looks treed. Where this returns True the build replaces the canopy model's cover
    and height with SCANFI's closure and height from an epoch after the disturbance [H].
    """
    return (dist_year > 0) & (chm_year > 0) & (dist_year >= chm_year)


def years_since(event_year: np.ndarray, map_year: int) -> np.ndarray:
    """map_year - event_year for events strictly before map_year; -1 otherwise (no event = 0)."""
    ev = np.asarray(event_year).astype(np.int32)
    out = np.where((ev > 0) & (ev < map_year), map_year - ev, -1)
    return out.astype(np.int16)


def harvest_year(loss_year: np.ndarray, fire_years: list[tuple[int, np.ndarray]], window: int = P2["fire_harvest_window"]
                 ) -> np.ndarray:
    """Forest loss not explained by fire: loss years (calendar) minus those inside a burn of year y
    with |loss - y| <= window. ``fire_years`` is a list of (year, mask) burns. [H]"""
    ly = np.asarray(loss_year).astype(np.int32).copy()
    for y, mask in fire_years:
        ly[(ly > 0) & mask & (np.abs(ly - y) <= window)] = 0
    return ly


def class_areas(cls: np.ndarray, cell_m: float, names: dict = C.CLASS_NAMES_V2) -> dict[str, float]:
    """Area (ha) per class name, classes present only (no-data excluded)."""
    ha = cell_m * cell_m / 1e4
    vals, cnt = np.unique(cls[cls != C.NODATA], return_counts=True)
    return {names[int(v)]: round(float(c) * ha, 1) for v, c in zip(vals, cnt)}
