"""SCANFI v2 -> FBP typing of the pre-fire back-cast build (scripts/fuelgrid/backcast.py).

Synthetic cells only; no downloads. One case (at least) per rule of the CFS national decision
rules the build applies, plus: every code the typing writes is an Alberta Wildfire code that the
engine maps to the intended fuel type, and the NBAC restore key of a polygon.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.fuelgrid import backcast as B  # noqa: E402

from firesim.data.fuel_loader import AB_WILDFIRE_CODES, percent_conifer_for_code  # noqa: E402
from firesim.fbp.constants import FuelType  # noqa: E402


def cell(lc=B.LC_CONIFER, closure=60.0, height=15.0, **cc):
    """Type one cell; ``cc`` gives species crown closure (%), the rest 0."""
    sp = {k: np.array([[float(cc.get(k, 0.0))]], np.float32) for k in B.SPECIES}
    code, rule = B.type_scanfi(np.array([[lc]], np.uint8), np.array([[closure]], np.float32),
                               np.array([[height]], np.float32), sp)
    return int(code[0, 0]), int(rule[0, 0])


class TestLandCover:
    def test_water_and_rock(self):
        assert cell(lc=B.LC_WATER) == (B.WATER, 100)
        assert cell(lc=B.LC_ROCK) == (B.NONFUEL, 101)

    def test_no_value(self):
        assert cell(lc=0) == (0, 0)
        assert cell(lc=255) == (0, 0)

    def test_herbs_are_grass(self):
        assert cell(lc=B.LC_HERBS, closure=0, height=0) == (B.O1, 111)

    def test_shrub_is_m1_25(self):
        assert cell(lc=B.LC_SHRUB, closure=0, height=0, blackSpruce=5) == (B.m12(25), 112)

    def test_open_black_spruce_is_d(self):
        # open rule 3 does not apply when black spruce / tamarack lead: open rule 1 (D-1/2)
        assert cell(lc=B.LC_BRYOID, closure=5, height=3, blackSpruce=5) == (B.D12, 110)
        assert cell(lc=B.LC_CONIFER, closure=8, height=3, tamarack=6) == (B.D12, 110)

    def test_treed_land_cover_under_10_percent_closure_is_open(self):
        assert cell(lc=B.LC_CONIFER, closure=9, height=10, jackPine=9) == (B.O1, 111)


class TestComposition:
    def test_deciduous(self):
        assert cell(lc=B.LC_BROADLEAF, closure=70, broadleaf=70) == (B.D12, 120)

    def test_tamarack_counts_as_broadleaf(self):
        assert cell(closure=60, tamarack=50, blackSpruce=10) == (B.D12, 120)

    def test_mixedwood_low_closure_is_d(self):
        assert cell(lc=B.LC_MIXED, closure=30, broadleaf=15, blackSpruce=15) == (B.D12, 130)

    @pytest.mark.parametrize("needle,pc,rule", [(30, 35, 131), (50, 50, 132), (70, 65, 133)])
    def test_mixedwood_percent_conifer(self, needle, pc, rule):
        assert cell(lc=B.LC_MIXED, closure=60, otherConiferous=needle,
                    broadleaf=100 - needle) == (B.m12(pc), rule)


class TestConifer:
    @pytest.mark.parametrize("closure,height,expect", [
        (25, 10, (B.C1, 141)), (50, 10, (B.C2, 142)), (75, 5, (B.C4, 143)), (75, 12, (B.C2, 144))])
    def test_black_spruce(self, closure, height, expect):
        assert cell(closure=closure, height=height, blackSpruce=closure) == expect

    @pytest.mark.parametrize("closure,height,expect", [
        (60, 8, (B.C4, 145)), (60, 12, (B.C2, 146)), (40, 8, (B.C2, 148)), (40, 12, (B.C3, 149))])
    def test_black_spruce_with_jack_pine(self, closure, height, expect):
        assert cell(closure=closure, height=height, blackSpruce=0.8 * closure,
                    jackPine=0.2 * closure) == expect

    def test_jack_pine_below_15_percent_is_ignored(self):
        assert cell(closure=50, height=12, blackSpruce=45, jackPine=5) == (B.C2, 142)

    def test_white_spruce(self):
        assert cell(closure=20, otherConiferous=20) == (B.D12, 150)
        assert cell(closure=60, otherConiferous=60) == (B.C2, 151)

    def test_balsam_fir(self):
        assert cell(closure=20, balsamFir=20) == (B.m12(25), 152)
        assert cell(closure=60, balsamFir=60) == (B.C2, 153)
        assert cell(closure=80, balsamFir=80) == (B.C4, 154)

    @pytest.mark.parametrize("sp", ["jackPine", "lodgepolePine"])
    def test_pine(self, sp):
        assert cell(closure=30, **{sp: 30}) == (B.C7, 155)
        assert cell(closure=60, height=12, **{sp: 60}) == (B.C3, 157)
        assert cell(closure=60, height=5, **{sp: 60}) == (B.C4, 158)

    def test_open_pine_with_black_spruce_is_m50(self):
        assert cell(closure=30, jackPine=22, blackSpruce=8) == (B.m12(50), 156)

    def test_southern_species(self):
        assert cell(closure=20, whiteRedPine=20) == (B.C7, 160)
        assert cell(closure=50, whiteRedPine=50) == (B.C5, 161)
        assert cell(closure=50, ponderosaPine=50) == (B.C7, 162)
        assert cell(closure=30, douglasFir=30) == (B.C7, 163)
        assert cell(closure=50, douglasFir=50) == (B.C3, 164)
        assert cell(closure=70, douglasFir=70) == (B.C4, 165)


class TestCodes:
    def test_every_code_is_an_alberta_code_with_the_intended_type(self):
        rng = np.random.default_rng(1)
        n = 20000
        lc = rng.integers(0, 10, (1, n)).astype(np.uint8)
        cl = rng.uniform(0, 100, (1, n)).astype(np.float32)
        ht = rng.uniform(0, 30, (1, n)).astype(np.float32)
        sp = {k: rng.uniform(0, 60, (1, n)).astype(np.float32) * (rng.random((1, n)) < 0.4)
              for k in B.SPECIES}
        code, rule = B.type_scanfi(lc, cl, ht, sp)
        valid = (lc >= 1) & (lc <= 8)
        assert (code[valid] != 0).all() and (rule[valid] != 0).all()
        assert (code[~valid] == 0).all()
        for c in np.unique(code[valid]):
            assert int(c) in AB_WILDFIRE_CODES, c
        assert set(np.unique(rule[valid]).tolist()) <= set(B.RULE_NAMES)
        expect = {B.C1: FuelType.C1, B.C2: FuelType.C2, B.C3: FuelType.C3, B.C4: FuelType.C4,
                  B.C5: FuelType.C5, B.C7: FuelType.C7, B.D12: FuelType.D1, B.O1: FuelType.O1b}
        for c, ft in expect.items():
            assert AB_WILDFIRE_CODES[c] is ft
        for pc in (25, 35, 50, 65):
            assert AB_WILDFIRE_CODES[B.m12(pc)] is FuelType.M1
            assert percent_conifer_for_code(B.m12(pc), "ab_wildfire") == pc
        assert AB_WILDFIRE_CODES[B.WATER] is None and AB_WILDFIRE_CODES[B.NONFUEL] is None

    def test_nan_inputs_do_not_raise(self):
        sp = {k: np.array([[np.nan]], np.float32) for k in B.SPECIES}
        code, _ = B.type_scanfi(np.array([[B.LC_CONIFER]], np.uint8),
                                np.array([[np.nan]], np.float32), np.array([[np.nan]], np.float32), sp)
        # no closure -> open; no black spruce / tamarack among the species -> O-1 (open rule 3)
        assert code[0, 0] == B.O1


class TestNbacKey:
    def test_end_dates(self):
        import pandas as pd
        assert B._end_key({"YEAR": 2011, "AG_EDATE": None, "HS_EDATE": None}) == 2011366
        assert B._end_key({"YEAR": 2011, "AG_EDATE": pd.Timestamp("2011-07-28"),
                           "HS_EDATE": pd.Timestamp("2011-06-01")}) == 2011209
        # an end date recorded in another year is ignored (typo), so the year default applies
        assert B._end_key({"YEAR": 2011, "AG_EDATE": pd.Timestamp("2012-01-05"),
                           "HS_EDATE": None}) == 2011366
