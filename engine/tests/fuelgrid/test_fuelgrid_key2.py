"""Full FBP decision key (v2) of the open-data fuel grid (scripts/fuelgrid/key2.py).

Synthetic rasters only; no downloads. One test (at least) per new rule, plus the property that
v2 reproduces v1 where no new evidence applies, and that every v2 code means, in the engine's
``cfs_national`` scheme, the fuel type the key intended.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.fuelgrid import config as C  # noqa: E402
from scripts.fuelgrid import key2 as K  # noqa: E402
from scripts.fuelgrid import rules as R  # noqa: E402

from firesim.data.fuel_loader import CODE_SCHEMES, cfs_national_modifier  # noqa: E402
from firesim.fbp.constants import FUEL_TYPES, FuelType  # noqa: E402

N = 30
Z = np.zeros((N, N), np.float32)


def _species(pine=0.0, bs=0.0, df=0.0, pp=0.0) -> K.Species:
    f = lambda v: np.full((N, N), v, np.float32)  # noqa: E731
    return K.Species(pine=f(pine), black_spruce=f(bs), douglas_fir=f(df), ponderosa=f(pp))


def _inputs(cover=None, conifer=0.9, species=None, **kw) -> K.KeyInputsV2:
    cov = np.zeros((N, N), np.float32)
    if cover is None:
        cov[5:15, 5:15] = 0.9   # one 4 ha stand
    else:
        cov = cover
    base = R.KeyInputs(valid=np.ones((N, N), bool), cover=cov, conifer_frac=np.full((N, N), conifer, np.float32),
                       bld_frac=Z.copy(), wc={k: Z.copy() for k in C.WC_BANDS}, aci=np.zeros((N, N), np.uint8),
                       osm=np.zeros((N, N), np.uint8))
    args = dict(base=base, species=species or _species(), height=np.full((N, N), 15.0, np.float32),
                cover2=cov.copy(), closure=R.mean3x3(cov), lichen=Z.copy(), treed_con=Z.copy(),
                treed_broad=Z.copy(), years_since_fire=np.full((N, N), -1, np.int16),
                years_since_harvest=np.full((N, N), -1, np.int16), pre_harvest_conifer=np.full((N, N), 0.9, np.float32),
                pre_harvest_pine=Z.copy(), pre_harvest_closure=np.full((N, N), 0.8, np.float32))
    args.update(kw)
    return K.KeyInputsV2(**args)


def _at(x: K.KeyInputsV2, r=10, c=10):
    cls, rule, _, pc = K.decision_key_v2(x)
    return int(cls[r, c]), int(rule[r, c]), int(pc[r, c])


# ── species composition ──────────────────────────────────────────────────────
def test_tamarack_counts_as_deciduous_and_shares_sum_over_all_species():
    sps = {k: np.zeros((1, 1), np.float32) for k in
           ("broadleaf", "blackSpruce", "otherConiferous", "balsamFir", "jackPine", "lodgepolePine",
            "tamarack", "whiteRedPine", "douglasFir", "ponderosaPine")}
    sps["blackSpruce"][:] = 40
    sps["tamarack"][:] = 40
    sps["jackPine"][:] = 20
    cf, sp, pine_of_con = K.species_from_scanfi(sps)
    assert cf[0, 0] == pytest.approx(0.6)          # tamarack not counted as conifer (CFS 2018)
    assert sp.black_spruce[0, 0] == pytest.approx(0.4)
    assert sp.pine[0, 0] == pytest.approx(0.2)
    assert pine_of_con[0, 0] == pytest.approx(20 / 60)


def test_cells_without_species_crown_closure_are_zero_not_nan():
    sps = {k: np.zeros((2, 2), np.float32) for k in
           ("broadleaf", "blackSpruce", "otherConiferous", "balsamFir", "jackPine", "lodgepolePine",
            "tamarack", "whiteRedPine", "douglasFir", "ponderosaPine")}
    sps["broadleaf"][0, 0] = np.nan
    cf, sp, pc = K.species_from_scanfi(sps)
    assert np.isfinite(cf).all() and (cf == 0).all() and (pc == 0).all()


# ── forest typing inside stands (rules 20-26) ────────────────────────────────
def test_spruce_stand_is_c2_rule_20():
    assert _at(_inputs())[:2] == (C.C2, 20)


def test_c1_needs_black_spruce_lichen_and_closure_below_60_percent():
    sp = _species(bs=0.9)
    cov = np.zeros((N, N), np.float32)
    cov[5:15, 5:15] = 0.5
    lichen = np.full((N, N), 0.05, np.float32)
    assert _at(_inputs(cover=cov, species=sp, lichen=lichen))[:2] == (C.C1, 21)
    assert _at(_inputs(cover=cov, species=sp, lichen=Z.copy()))[0] == C.C2           # no lichen
    assert _at(_inputs(cover=cov, species=_species(bs=0.6), lichen=lichen))[0] == C.C2  # < 75 % black spruce
    dense = cov.copy()
    dense[5:15, 5:15] = 0.7
    assert _at(_inputs(cover=dense, species=sp, lichen=lichen))[0] == C.C2           # closure >= 60 %


def test_pine_short_and_dense_is_c4_otherwise_c3():
    sp = _species(pine=0.5)
    h8 = np.full((N, N), 8.0, np.float32)
    assert _at(_inputs(species=sp, height=h8))[:2] == (C.C4, 22)
    assert _at(_inputs(species=sp))[:2] == (C.C3, 23)                                # 15 m
    open_ = np.zeros((N, N), np.float32)
    open_[5:15, 5:15] = 0.5
    assert _at(_inputs(cover=open_, species=sp, height=h8))[:2] == (C.C3, 23)        # not dense
    assert _at(_inputs(species=_species(pine=0.2)))[0] == C.C2                        # < 25 % pine


def test_c7_ponderosa_or_open_douglas_fir():
    assert _at(_inputs(species=_species(pp=0.35)))[:2] == (C.C7, 24)
    assert _at(_inputs(species=_species(pp=0.2, df=0.3)))[:2] == (C.C7, 24)
    cov = np.zeros((N, N), np.float32)
    cov[5:15, 5:15] = 0.45
    df = _species(df=0.5)
    clo = np.full((N, N), 0.35, np.float32)
    assert _at(_inputs(cover=cov, species=df, closure=clo))[:2] == (C.C7, 24)
    assert _at(_inputs(species=df))[0] == C.C2                                       # dense Douglas-fir


def test_deciduous_and_mixedwood_keep_v1_cuts_and_percent_conifer():
    assert _at(_inputs(conifer=0.2))[:2] == (C.D2, 25)
    cls, rule, pc = _at(_inputs(conifer=0.5))
    assert (cls, rule, pc) == (C.M2, 26, 50)


def test_v2_keeps_the_v1_fuel_extent_and_matches_v1_when_units_are_uniform():
    rng = np.random.default_rng(1)
    cov = (rng.random((N, N)) > 0.3).astype(np.float32) * 0.8
    x = _inputs(cover=cov)
    # conifer fraction constant within each 5 x 5 unit -> unit typing equals cell typing
    cf = np.repeat(np.repeat(rng.random((N // 5, N // 5)), 5, 0), 5, 1).astype(np.float32)
    x.base.conifer_frac = cf
    cls1, rule1, _ = R.decision_key(x.base)
    cls2, rule2, _, _ = K.decision_key_v2(x)
    assert (cls1 == cls2).all()
    assert (rule2[rule1 != 2] == rule1[rule1 != 2]).all()
    assert set(np.unique(rule2[rule1 == 2])) <= {20, 25, 26}


def test_forest_is_typed_on_the_unit_mean_not_the_cell():
    # one 100 m unit (5 x 5) with 9 pure-conifer cells, the rest at 0.5: unit mean < 0.75 -> M-2
    cf = np.full((N, N), 0.5, np.float32)
    cf[5:8, 5:8] = 1.0
    x = _inputs(conifer=0.5)
    x.base.conifer_frac = cf
    cls, rule, _, pc = K.decision_key_v2(x)
    unit = (slice(5, 10), slice(5, 10))
    forest = cls[unit] != C.NF            # the stand's corner cell falls outside the stand
    assert forest.sum() >= 24 and (cls[unit][forest] == C.M2).all()
    assert len(set(pc[unit][forest].tolist())) == 1 and pc[unit][forest][0] in (65, 70)
    # the 20 m extent is unchanged: cells outside the stand stay outside
    assert (cls[0:5, 0:5] != C.M2).all()


def test_unit_means_ignore_non_forest_cells_and_supplied_unit_conifer_wins():
    ids = K.unit_ids((10, 10), 5)
    a = np.zeros((10, 10), np.float32)
    a[0, 0] = 1.0
    mask = np.zeros((10, 10), bool)
    mask[0, 0] = mask[0, 1] = True
    m = K.unit_mean(a, mask, ids)
    assert m[0, 0] == pytest.approx(0.5) and m[4, 4] == pytest.approx(0.5) and np.isnan(m[9, 9])
    x = _inputs(conifer=0.9, unit_conifer=np.full((N, N), 0.1, np.float32))
    assert _at(x)[:2] == (C.D2, 25)


def test_unit_quantile_map_matches_label_distribution():
    rng = np.random.default_rng(3)
    pred = rng.random(500) * 0.5 + 0.25          # compressed predictions
    lab = rng.random(500)
    xp, yp = K.quantile_map(pred, lab)
    mapped = np.interp(pred, xp, yp)
    assert np.quantile(mapped, 0.9) == pytest.approx(np.quantile(lab, 0.9), abs=0.01)


# ── open conifer and wetland outside stands (rules 30-32) ────────────────────
def _open_conifer(**kw):
    wc = {k: Z.copy() for k in C.WC_BANDS}
    wc[20][:] = 0.6  # shrub: v1 rule 7 -> O-1b
    kw = {"cover2": np.full((N, N), 0.2, np.float32), "treed_con": np.full((N, N), 0.4, np.float32), **kw}
    x = _inputs(cover=Z.copy(), **kw)
    x.base.wc = wc
    return x


def test_open_conifer_outside_stands_is_m_25_percent_conifer():
    assert _at(_open_conifer()) == (C.M2, 31, 25)


def test_open_black_spruce_with_lichen_is_c1():
    x = _open_conifer(species=_species(bs=0.8), lichen=np.full((N, N), 0.02, np.float32))
    assert _at(x)[:2] == (C.C1, 30)


def test_open_conifer_needs_conifer_dominant_treed_cover():
    x = _open_conifer(treed_broad=np.full((N, N), 0.3, np.float32))
    assert _at(x)[:2] == (C.O1B, 7)
    x = _open_conifer(cover2=np.full((N, N), 0.05, np.float32))
    assert _at(x)[:2] == (C.O1B, 7)


def test_open_conifer_never_over_buildings():
    x = _open_conifer()
    x.base.bld_frac = np.full((N, N), 0.3, np.float32)
    assert _at(x)[0] == C.NF


def test_scanfi_treed_gate_types_land_the_canopy_model_misses_only_when_enabled():
    treed = np.zeros((N, N), np.float32)
    treed[5:15, 5:15] = 0.8
    x = _inputs(cover=Z.copy())
    x.base.wc[10][:] = 0.6                     # WorldCover tree: v1 rule 7 -> O-1b
    assert _at(x)[:2] == (C.O1B, 7)            # gate off (Edmonton / St. Albert)
    x.alt_treed, x.alt_closure, x.alt_height = treed, np.full((N, N), 0.5, np.float32), np.full((N, N), 15.0, np.float32)
    assert _at(x)[:2] == (C.C2, 60)
    x.base.conifer_frac[:] = 0.1
    assert _at(x)[:2] == (C.D2, 65)
    x.base.bld_frac[:] = 0.3                   # never over buildings
    assert _at(x)[0] == C.NF


def test_herbaceous_wetland_is_relabelled_but_stays_o1b():
    x = _inputs(cover=Z.copy())
    x.base.wc[90][:] = 0.7
    assert _at(x)[:2] == (C.O1B, 32)
    x = _inputs(cover=Z.copy())
    x.base.aci[:] = 85  # AAFC peatland
    x.base.wc[30][:] = 0.6
    assert _at(x)[:2] == (C.O1B, 32)


# ── fire (rules 40-42; CFS 2026 Table 3) ─────────────────────────────────────
@pytest.mark.parametrize("ysf,expect", [(1, (C.NF, 40, 0)), (2, (C.NF, 40, 0)), (3, (C.D2, 41, 0)),
                                        (5, (C.D2, 41, 0)), (6, (C.M2, 42, 25)), (21, (C.M2, 42, 25)),
                                        (22, (C.C2, 20, 0))])
def test_years_since_fire_table(ysf, expect):
    x = _inputs(years_since_fire=np.full((N, N), ysf, np.int16))
    assert _at(x) == expect


def test_fire_keeps_grass_nonfuel_and_water():
    x = _inputs(years_since_fire=np.full((N, N), 1, np.int16))
    x.base.wc[80][0:3, :] = 1.0
    cls, rule, _, _ = K.decision_key_v2(x)
    assert cls[1, 1] == C.WATER and cls[25, 25] == C.NF and rule[25, 25] == 8  # bare stays rule 8
    x = _inputs(years_since_fire=np.full((N, N), 3, np.int16))
    x.base.wc[30][20:, :] = 0.9   # grass outside the stand
    cls, _, _, _ = K.decision_key_v2(x)
    assert cls[25, 25] == C.O1B


# ── harvest (rules 50-53) ────────────────────────────────────────────────────
def test_recent_harvest_is_slash_by_pre_harvest_composition():
    ysh = np.full((N, N), 3, np.int16)
    assert _at(_inputs(years_since_harvest=ysh))[:2] == (C.S2, 51)
    pine = np.full((N, N), 0.6, np.float32)
    assert _at(_inputs(years_since_harvest=ysh, pre_harvest_pine=pine))[:2] == (C.S1, 50)
    dec = np.full((N, N), 0.1, np.float32)
    assert _at(_inputs(years_since_harvest=ysh, pre_harvest_conifer=dec))[:2] == (C.D2, 52)


def test_slash_lasts_five_years_and_needs_a_treed_block():
    assert _at(_inputs(years_since_harvest=np.full((N, N), 5, np.int16)))[0] == C.S2
    assert _at(_inputs(years_since_harvest=np.full((N, N), 6, np.int16)))[0] == C.C2  # treed again: inputs decide
    x = _inputs(years_since_harvest=np.full((N, N), 2, np.int16), pre_harvest_closure=np.full((N, N), 0.1, np.float32))
    assert _at(x)[0] == C.C2


def test_slash_applies_on_bare_blocks_and_old_bare_blocks_become_o1b():
    x = _inputs(cover=Z.copy(), years_since_harvest=np.full((N, N), 2, np.int16))
    assert _at(x)[:2] == (C.S2, 51)                            # v1 rule 8 (bare) cell
    x = _inputs(cover=Z.copy(), years_since_harvest=np.full((N, N), 10, np.int16))
    assert _at(x)[:2] == (C.O1B, 53)
    x = _inputs(cover=Z.copy(), years_since_harvest=np.full((N, N), 30, np.int16))
    assert _at(x)[:2] == (C.NF, 8)


def test_harvest_never_in_urban_or_crop_context():
    x = _inputs(cover=Z.copy(), years_since_harvest=np.full((N, N), 2, np.int16))
    x.base.osm[:] = C.OSM_URBAN
    assert _at(x)[0] == C.NF
    x = _inputs(cover=Z.copy(), years_since_harvest=np.full((N, N), 2, np.int16))
    x.base.aci[:] = 146
    assert _at(x)[0] == C.O1A


def test_more_recent_disturbance_wins():
    x = _inputs(years_since_fire=np.full((N, N), 10, np.int16), years_since_harvest=np.full((N, N), 2, np.int16))
    assert _at(x)[0] == C.S2
    x = _inputs(years_since_fire=np.full((N, N), 1, np.int16), years_since_harvest=np.full((N, N), 4, np.int16))
    assert _at(x)[0] == C.NF


def test_forest_loss_inside_a_burn_of_the_same_period_is_fire_not_harvest():
    loss = np.array([[2019, 2019, 2015, 0]], np.int32)
    burn = np.array([[True, False, True, True]])
    hy = K.harvest_year(loss, [(2018, burn)])
    assert hy.tolist() == [[0, 2019, 2015, 0]]


def test_years_since_ignores_events_in_or_after_the_map_year():
    ys = K.years_since(np.array([0, 2019, 2023, 2024]), 2023)
    assert ys.tolist() == [-1, 4, -1, -1]


def test_stale_canopy_where_disturbance_postdates_the_image():
    s = K.stale_canopy(np.array([2018, 2018, 2020, 0]), np.array([2019, 2017, 0, 2019]))
    assert s.tolist() == [True, False, False, False]


# ── seasons and codes ────────────────────────────────────────────────────────
def test_leaf_off_changes_only_deciduous_and_mixedwood():
    cls = np.array([C.C1, C.C3, C.C4, C.C7, C.S1, C.S2, C.D2, C.M2, C.O1B])
    off = K.leaf_off_v2(cls)
    assert off.tolist() == [C.C1, C.C3, C.C4, C.C7, C.S1, C.S2, C.D1, C.M1, C.O1B]


def test_every_v2_code_means_the_intended_fuel_type_in_the_engine():
    scheme = CODE_SCHEMES["cfs_national"]
    want = {C.C1: FuelType.C1, C.C2: FuelType.C2, C.C3: FuelType.C3, C.C4: FuelType.C4, C.C7: FuelType.C7,
            C.D1: FuelType.D1, C.D2: FuelType.D2, C.S1: FuelType.S1, C.S2: FuelType.S2,
            C.O1A: FuelType.O1a, C.O1B: FuelType.O1b, C.M1: FuelType.M1, C.M2: FuelType.M2,
            C.NF: None, C.WATER: None}
    cls = np.array(list(want), np.uint8)
    codes = K.encode_ciffc_v2(cls, np.full(cls.shape, 25))
    for k, code in zip(cls, codes):
        assert scheme[int(code)] == want[int(k)], C.CLASS_NAMES_V2[int(k)]
        if want[int(k)] is not None:
            assert want[int(k)] in FUEL_TYPES  # the engine has the type's equations
    assert cfs_national_modifier(int(codes[list(want).index(C.M2)])) == ("pc", 25.0)


def test_v1_code_tables_are_unchanged_so_v1_grids_reproduce():
    assert C.CIFFC == {C.NODATA: 0, C.C2: 2, C.D1: 11, C.D2: 12, C.O1A: 31, C.O1B: 32, C.NF: 101, C.WATER: 102}
    assert C.CANOPY_LIDAR == {C.NODATA: 0, C.C2: 2, C.D2: 12, C.M2: 14, C.O1A: 31, C.O1B: 32, C.NF: 99, C.WATER: 98}
    assert set(C.RULES) == set(range(9)) and C.PIPELINE_VERSION == "1.0.0"


def test_decision_key_v2_is_deterministic():
    rng = np.random.default_rng(7)
    cov = (rng.random((N, N)) > 0.4).astype(np.float32)
    x = _inputs(cover=cov, years_since_harvest=rng.integers(-1, 30, (N, N)).astype(np.int16),
                years_since_fire=rng.integers(-1, 30, (N, N)).astype(np.int16),
                lichen=rng.random((N, N)).astype(np.float32) * 0.05)
    a = K.decision_key_v2(x)
    b = K.decision_key_v2(x)
    for u, v in zip(a, b):
        assert np.array_equal(u, v)


def test_test_areas_are_built_from_inputs_dated_before_the_fire():
    from scripts.fuelgrid.sources2 import epochs_for
    for t in C.TEST_AREAS.values():
        assert all(e < t.year for e in epochs_for(t.year))
        assert t.year - 1 < t.year  # SCANFI v3 and ACI of the previous year
        assert t.year > 2021        # WorldCover 2021 imagery and Meta canopy imagery (<= 2020) pre-date the fire
