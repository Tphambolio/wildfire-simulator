"""Daily FWI System against the cffdrs reference implementation (oracle test).

``data/cffdrs_fwi_reference.json`` holds daily FWI outputs computed by cffdrs (Python,
``cffdrs.fwi``; Wang et al. 2017), an independent implementation of Van Wagner & Pickett (1985)
and Van Wagner (1987), for

- the cffdrs 48-day test sequence (``fwi_test_data.csv``, the R package's ``test_fwi`` data,
  start 85 / 6 / 15), chained day to day, with that CSV's own one-decimal values; and
- 21 single-day edge cases: cold days at or below -2.8 C in five months (the Drought Code rule
  fixed on 2026-10-10), heavy and threshold rain, zero wind, RH 0 / 100, codes at 0 and 101.

Every case is computed with the FFMC coefficient patched to 147.2 (what FireSim uses: Van
Wagner 1987 eq 2, ST-X-3 eq 46) and with cffdrs's own 250 * 59.5 / 101 = 147.27723.
Regenerate with ``data/generate_cffdrs_fwi_reference.py``.

Tolerances, and why they are not all at floating-point precision:

- FFMC, ISI and DC: 1e-9 against cffdrs at the same coefficient.
- DMC (and BUI, FWI through it): FireSim converts DMC to moisture after rain with the inverse of
  Van Wagner (1987) eq 16 as printed, M = 20 + exp(5.6348 - P / 43.43) (FTR-35 printed p. 10,
  PDF p. 21), and back with eq 16, P = 244.72 - 43.43 ln(M - 20). cffdrs uses the program form
  M = 20 + 280 / exp(0.023 P) and 43.43 (5.6348 - ln(M - 20)) ("alteration to Eq. 12 to
  calculate more accurately"). The two differ only on days with more than 1.5 mm of rain, by
  at most 0.064 DMC over the 48 days (0.066 BUI, 0.02 FWI). With no DMC rain phase the DMC is
  exact (tested below).
- Against cffdrs's own coefficient 147.27723 (effect-size test) FireSim differs by up to 0.13 FFMC,
  0.26 ISI and 0.25 FWI over the 48 days and up to 0.67 ISI / 0.52 FWI on the edge cases (largest
  at FFMC ~97); with the coefficient swapped those differences vanish (tested below).
- Against the CSV's one-decimal values: at most 0.1 FFMC / DMC / BUI, 0.2 ISI, 0.3 FWI after
  rounding, and DC exactly. The CSV is cffdrs R output at 147.27723, not FireSim's 147.2.
"""

import json
from pathlib import Path

import pytest

import firesim.fwi.calculator as fwi_calc
from firesim.fwi.calculator import FWICalculator

_REF = json.loads((Path(__file__).parent / "data" / "cffdrs_fwi_reference.json").read_text())
_SEQ = _REF["sequence"]
_EDGE = _REF["edge"]
_KEYS = ("ffmc", "dmc", "dc", "isi", "bui", "fwi")
_EXACT = 250.0 * 59.5 / 101.0

# Tolerances against cffdrs at the same coefficient (module docstring)
_TOL_SAME = {"ffmc": 1e-9, "isi": 1e-9, "dc": 1e-9, "dmc": 0.07, "bui": 0.07, "fwi": 0.025}


def _run_sequence() -> list[dict[str, float]]:
    calc = FWICalculator(ffmc_prev=85.0, dmc_prev=6.0, dc_prev=15.0)
    out = []
    for day in _SEQ:
        i = day["inputs"]
        r = calc.calculate_daily(temp=i["temp"], rh=i["rh"], wind=i["wind"], rain=i["rain"], month=i["month"])
        out.append({k: getattr(r, k) for k in _KEYS})
    return out


def _run_edge(case: dict) -> dict[str, float]:
    i = case["inputs"]
    calc = FWICalculator(ffmc_prev=i["ffmc_prev"], dmc_prev=i["dmc_prev"], dc_prev=i["dc_prev"])
    r = calc.calculate_daily(temp=i["temp"], rh=i["rh"], wind=i["wind"], rain=i["rain"], month=i["month"])
    return {k: getattr(r, k) for k in _KEYS}


def _max_diff(a: list[dict], b: list[dict]) -> dict[str, float]:
    return {k: max(abs(x[k] - y[k]) for x, y in zip(a, b)) for k in _KEYS}


def test_engine_uses_the_printed_coefficient():
    """FireSim's FWI and FBP layers use the same printed coefficient (VW 1987 eq 2, ST-X-3 eq 46)."""
    from firesim.fbp.calculator import FFMC_COEF

    assert fwi_calc.FFMC_COEFFICIENT == 147.2
    assert FFMC_COEF == fwi_calc.FFMC_COEFFICIENT


def test_fixture_shape():
    assert len(_SEQ) == 48
    assert len(_EDGE) == 21


class TestSequence:
    """The 48-day chained sequence from 85 / 6 / 15."""

    def test_matches_cffdrs_at_147_2(self):
        diff = _max_diff(_run_sequence(), [d["coef_147_2"] for d in _SEQ])
        for k in _KEYS:
            assert diff[k] <= _TOL_SAME[k], (k, diff[k])

    def test_matches_cffdrs_with_its_own_coefficient_when_swapped(self, monkeypatch):
        """The coefficient is the whole FFMC / ISI difference from unpatched cffdrs."""
        monkeypatch.setattr(fwi_calc, "FFMC_COEFFICIENT", _EXACT)
        diff = _max_diff(_run_sequence(), [d["coef_exact"] for d in _SEQ])
        for k in _KEYS:
            assert diff[k] <= _TOL_SAME[k], (k, diff[k])

    def test_effect_of_the_coefficient_is_bounded(self):
        """147.2 vs 147.27723: the documented effect size (PROJECT_RECORD section 4.1)."""
        diff = _max_diff(_run_sequence(), [d["coef_exact"] for d in _SEQ])
        assert 0.1 < diff["ffmc"] <= 0.13
        assert 0.2 < diff["isi"] <= 0.27
        assert 0.2 < diff["fwi"] <= 0.26
        assert diff["dc"] <= 1e-9

    def test_one_decimal_values(self):
        """Against the CSV's one-decimal values (cffdrs R at 147.27723)."""
        eng = _run_sequence()
        rounded = [{k: round(x[k], 1) for k in _KEYS} for x in eng]
        diff = _max_diff(rounded, [d["published"] for d in _SEQ])
        limits = {"ffmc": 0.1, "dmc": 0.1, "dc": 0.0, "isi": 0.2, "bui": 0.1, "fwi": 0.3}
        for k in _KEYS:
            assert diff[k] <= limits[k] + 1e-9, (k, diff[k])

    def test_day_one(self):
        """Day 1 (17 C, 42 %, 25 km/h, no rain, April, from 85 / 6 / 15).

        FireSim (147.2) gives FFMC 87.69, ISI 10.85, FWI 10.10; these round to the FTR-33 worked
        example values 87.7 / 10.9 / 10.1 as quoted in Pyra's science test (FTR-33 itself is not on
        disk: secondary source). cffdrs at 147.27723 gives 87.65 and its CSV prints 87.6 / 10.8 / 10.0.
        """
        d1 = _run_sequence()[0]
        assert d1["ffmc"] == pytest.approx(87.69, abs=0.005)
        assert d1["isi"] == pytest.approx(10.85, abs=0.005)
        assert d1["fwi"] == pytest.approx(10.10, abs=0.005)
        assert d1["dc"] == pytest.approx(19.0, abs=0.05)
        assert _SEQ[0]["coef_exact"]["ffmc"] == pytest.approx(87.65, abs=0.005)


class TestEdgeCases:
    @pytest.mark.parametrize("case", _EDGE, ids=[c["label"] for c in _EDGE])
    def test_matches_cffdrs_at_147_2(self, case):
        eng = _run_edge(case)
        tol = dict(_TOL_SAME)
        if case["inputs"]["rain"] <= 1.5:
            tol["dmc"] = tol["bui"] = 1e-9  # no DMC rain phase: exact
            tol["fwi"] = 1e-9
        for k in _KEYS:
            assert eng[k] == pytest.approx(case["coef_147_2"][k], abs=tol[k]), k

    @pytest.mark.parametrize("case", _EDGE, ids=[c["label"] for c in _EDGE])
    def test_matches_cffdrs_with_its_own_coefficient_when_swapped(self, case, monkeypatch):
        monkeypatch.setattr(fwi_calc, "FFMC_COEFFICIENT", _EXACT)
        eng = _run_edge(case)
        for k in ("ffmc", "isi", "dc"):
            assert eng[k] == pytest.approx(case["coef_exact"][k], abs=1e-9), k

    def test_effect_of_the_coefficient_on_edge_cases(self):
        worst = {k: max(abs(_run_edge(c)[k] - c["coef_exact"][k]) for c in _EDGE) for k in _KEYS}
        assert worst["isi"] <= 0.7
        assert worst["fwi"] <= 0.55
        assert worst["ffmc"] <= 0.13


class TestColdDayDroughtCode:
    """M1: at T <= -2.8 C the temperature is floored at -2.8 and the day-length term still counts.

    Van Wagner (1987) eqs 25-26 (FTR-35 printed p. 14, PDF p. 25), Lf from Table 3 (printed p. 15);
    the floor is the Van Wagner & Pickett (1985) program rule as carried by cffdrs.
    """

    @pytest.mark.parametrize(
        "temp,month,dc_prev,expected",
        [
            (-5.0, 5, 200.0, 201.9),    # May Lf 3.8 -> +1.9 (FireSim gave 200.0 before the fix)
            (-3.0, 7, 300.0, 303.2),    # July Lf 6.4 -> +3.2
            (-10.0, 4, 100.0, 100.45),  # April Lf 0.9 -> +0.45
            (-2.8, 10, 350.0, 350.2),   # October Lf 0.4 -> +0.2
            (-20.0, 1, 300.0, 300.0),   # January Lf -1.6 -> V < 0 adds nothing
        ],
    )
    def test_cold_day(self, temp, month, dc_prev, expected):
        dc = FWICalculator().calculate_dc(temp=temp, rain=0.0, month=month, dc_prev=dc_prev)
        assert dc == pytest.approx(expected, abs=1e-9)

    def test_cold_day_equals_floor_temperature(self):
        calc = FWICalculator()
        for month in range(1, 13):
            assert calc.calculate_dc(-15.0, 0.0, month, 250.0) == calc.calculate_dc(-2.8, 0.0, month, 250.0)

    def test_cold_day_after_rain(self):
        """Rain reduces DC first, then the day-length term is added (VW 1987 eq 26)."""
        calc = FWICalculator()
        wet = calc.calculate_dc(temp=-4.0, rain=5.0, month=9, dc_prev=400.0)
        rain_only = calc.calculate_dc(temp=-4.0, rain=5.0, month=1, dc_prev=400.0)
        assert wet == pytest.approx(rain_only + 0.5 * 2.4, abs=1e-9)
