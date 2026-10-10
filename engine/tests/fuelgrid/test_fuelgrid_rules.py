"""Decision-key logic of the open-data fuel-grid pipeline (scripts/fuelgrid/rules.py).

Small synthetic rasters only; no downloads. Also checks that the output codes mean what the
engine's code schemes say they mean.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.fuelgrid import config as C  # noqa: E402
from scripts.fuelgrid import rules as R  # noqa: E402

from firesim.data.fuel_loader import CODE_SCHEMES, cfs_national_modifier  # noqa: E402
from firesim.fbp.constants import FuelType  # noqa: E402


def _inputs(shape=(30, 30), **kw) -> R.KeyInputs:
    z = np.zeros(shape, np.float32)
    base = dict(valid=np.ones(shape, bool), cover=z.copy(), conifer_frac=z.copy(), bld_frac=z.copy(),
                wc={k: z.copy() for k in C.WC_BANDS}, aci=np.zeros(shape, np.uint8),
                osm=np.zeros(shape, np.uint8))
    base.update(kw)
    return R.KeyInputs(**base)


# ── stand rule ───────────────────────────────────────────────────────────────
def test_one_hectare_block_of_full_canopy_is_a_stand_smaller_is_not():
    cover = np.zeros((30, 30), np.float32)
    cover[2:7, 2:7] = 1.0        # 5 x 5 cells = 1.00 ha
    cover[20:24, 20:24] = 1.0    # 4 x 4 cells = 0.64 ha
    s = R.stand_mask(cover, np.zeros_like(cover, bool))
    assert s[2:7, 2:7].all() and s.sum() == 25
    assert not s[20:24, 20:24].any()


def test_stand_needs_40_percent_smoothed_cover():
    cover = np.zeros((30, 30), np.float32)
    cover[5:15, 5:15] = 0.39
    assert not R.stand_mask(cover, np.zeros_like(cover, bool)).any()
    cover[5:15, 5:15] = 0.41
    assert R.stand_mask(cover, np.zeros_like(cover, bool))[6:14, 6:14].all()


def test_canopy_over_buildings_does_not_form_or_join_a_stand():
    cover = np.zeros((30, 30), np.float32)
    cover[5:15, 2:28] = 0.9
    bld = np.zeros_like(cover)
    bld[3:17, 14] = 0.3  # a row of houses splits the canopy band
    built = R.built_mask(bld, np.zeros_like(cover))
    s = R.stand_mask(cover, built)
    assert not s[:, 13:16].any()                 # houses and their 60 m neighbourhood excluded
    assert s[6:14, 3:12].all() and s[6:14, 17:27].all()  # both halves still >= 1 ha


def test_built_mask_thresholds():
    bld = np.zeros((9, 9), np.float32)
    bld[4, 4] = 0.019
    assert not R.built_mask(bld, np.zeros_like(bld)).any()
    bld[4, 4] = 0.02
    assert R.built_mask(bld, np.zeros_like(bld))[4, 4]
    wc = np.zeros_like(bld)
    wc[0, 0] = 0.5
    assert R.built_mask(np.zeros_like(bld), wc)[0, 0]


# ── typing ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cf,expected", [(0.0, C.D2), (0.25, C.D2), (0.26, C.M2), (0.5, C.M2),
                                         (0.74, C.M2), (0.75, C.C2), (1.0, C.C2)])
def test_forest_type_thresholds_match_the_lidar_grid(cf, expected):
    assert R.forest_type(np.array([cf]))[0] == expected


def test_decision_key_order_and_classes():
    shape = (40, 40)
    cover = np.zeros(shape, np.float32)
    cf = np.zeros(shape, np.float32)
    osm = np.zeros(shape, np.uint8)
    aci = np.zeros(shape, np.uint8)
    wc = {k: np.zeros(shape, np.float32) for k in C.WC_BANDS}
    bld = np.zeros(shape, np.float32)
    # A: conifer stand inside a park (OSM maintained) -> C-2, not non-fuel
    cover[2:10, 2:10] = 0.8
    cf[2:10, 2:10] = 0.9
    osm[0:12, 0:12] = C.OSM_MAINTAINED
    # B: water over part of the stand -> water wins
    wc[80][2:4, 2:4] = 1.0
    # C: isolated yard trees between houses -> non-fuel
    cover[20:22, 2:4] = 0.9
    bld[20:22, 5] = 0.3
    osm[18:26, 0:10] = C.OSM_URBAN
    # D: crop -> O-1a; E: native grassland outside town -> O-1b
    aci[30:35, 2:8] = 146
    aci[30:35, 20:26] = 110
    # F: mapped scrub inside a park -> O-1b
    osm[12:16, 20:26] = C.OSM_MAINTAINED
    osm[13:15, 21:25] = C.OSM_NATURAL_OPEN
    valid = np.ones(shape, bool)
    valid[-1, :] = False
    cls, rule, stands = R.decision_key(_inputs(shape, valid=valid, cover=cover, conifer_frac=cf,
                                               bld_frac=bld, wc=wc, aci=aci, osm=osm))
    assert (cls[2:4, 2:4] == C.WATER).all() and (rule[2:4, 2:4] == 1).all()
    assert (cls[4:9, 4:9] == C.C2).all() and (rule[4:9, 4:9] == 2).all()
    assert cls[9, 9] == C.NF  # stand corner below 40 % smoothed cover; park context -> non-fuel
    assert (cls[20:22, 2:4] == C.NF).all()
    assert (cls[31:34, 3:7] == C.O1A).all() and (rule[31:34, 3:7] == 6).all()
    assert (cls[31:34, 21:25] == C.O1B).all() and (rule[31:34, 21:25] == 7).all()
    assert (cls[13:15, 21:25] == C.O1B).all() and (rule[13:15, 21:25] == 4).all()
    assert (cls[12, 20] == C.NF)  # maintained park grass
    assert (cls[-1] == C.NODATA).all() and (rule[-1] == 0).all()
    assert (cls[valid] != C.NODATA).all()


def test_open_cells_inside_a_stand_are_o1b():
    cover = np.zeros((20, 20), np.float32)
    cover[2:12, 2:12] = 0.9
    cover[6, 6] = 0.0  # a gap
    cls, rule, _ = R.decision_key(_inputs((20, 20), cover=cover, conifer_frac=np.full((20, 20), 0.1, np.float32)))
    assert cls[6, 6] == C.O1B and rule[6, 6] == 3
    assert cls[3, 3] == C.D2


# ── seasonal variants and encoding ───────────────────────────────────────────
def test_leaf_off_switches_only_deciduous_and_mixedwood():
    cls = np.array([C.C2, C.D2, C.M2, C.O1A, C.O1B, C.NF, C.WATER, C.NODATA], np.uint8)
    off = R.leaf_off(cls)
    assert off.tolist() == [C.C2, C.D1, C.M1, C.O1A, C.O1B, C.NF, C.WATER, C.NODATA]


@pytest.mark.parametrize("doy,season", [(1, "leafoff"), (149, "leafoff"), (150, "leafon"),
                                        (257, "leafon"), (258, "leafoff"), (365, "leafoff")])
def test_season_for_doy(doy, season):
    assert R.season_for_doy(doy) == season


def test_ciffc_encoding_carries_percent_conifer_and_loads_as_intended():
    cls = np.array([C.C2, C.D2, C.M2, C.M1, C.D1, C.O1A, C.O1B, C.NF, C.WATER, C.NODATA], np.uint8)
    cf = np.array([0.9, 0.1, 0.42, 0.42, 0.1, 0, 0, 0, 0, 0], np.float32)
    codes = R.encode_ciffc(cls, cf)
    assert codes.tolist() == [2, 12, 540, 440, 11, 31, 32, 101, 102, 0]
    table = CODE_SCHEMES["cfs_national"]
    assert [table.get(int(c)) for c in codes] == [FuelType.C2, FuelType.D2, FuelType.M2, FuelType.M1,
                                                   FuelType.D1, FuelType.O1a, FuelType.O1b, None, None, None]
    assert cfs_national_modifier(540) == ("pc", 40.0)


def test_canopy_lidar_encoding_matches_engine_scheme_and_rejects_leafless():
    cls = np.array([C.C2, C.D2, C.M2, C.O1A, C.O1B, C.NF, C.WATER], np.uint8)
    codes = R.encode_canopy_lidar(cls)
    table = CODE_SCHEMES["canopy_lidar"]
    assert [table[int(c)] for c in codes] == [FuelType.C2, FuelType.D2, FuelType.M2, FuelType.O1a,
                                               FuelType.O1b, None, None]
    with pytest.raises(ValueError):
        R.encode_canopy_lidar(np.array([C.D1], np.uint8))


def test_percent_conifer_is_rounded_and_clipped_for_ciffc_codes():
    assert R.percent_conifer(np.array([0.0, 0.26, 0.74, 1.0])).tolist() == [5, 25, 75, 95]


# ── agreement statistics ─────────────────────────────────────────────────────
def test_agreement_perfect_and_known_kappa():
    a = R.agreement(np.array([[5, 0], [0, 5]]))
    assert a["oa"] == 1.0 and a["kappa"] == 1.0
    # po = 0.7, pe = 0.5 -> kappa 0.4
    b = R.agreement(np.array([[35, 15], [15, 35]]))
    assert b["oa"] == pytest.approx(0.7) and b["kappa"] == pytest.approx(0.4)
    assert b["precision"] == pytest.approx([0.7, 0.7]) and b["recall"] == pytest.approx([0.7, 0.7])


def test_confusion_counts_rows_reference_cols_prediction():
    ref = np.array([1, 1, 2, 2, 3])
    pred = np.array([1, 2, 2, 2, 9])  # 9 is not a label -> ignored
    cm = R.confusion(ref, pred, [1, 2, 3])
    assert cm.tolist() == [[1, 1, 0], [0, 2, 0], [0, 0, 0]]
