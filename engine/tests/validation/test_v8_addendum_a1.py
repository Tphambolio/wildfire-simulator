"""Unit tests for PREREG_LR8 addendum A1 (2026-10-24): the script fixes, each on the [R61] fixture case
(reports/data/v7-arrivals-recoded-2026-10-24/scripts/v8_rule_fixtures.py, synthetic UTM 32611 detections).

- v8_score.py never-reached guard: a > 540 m member is never ranked, 375-540 m is neither, and the rows skipped
  for an excluded interval or a non-candidate cell are counted; a jumps.csv without out_final_m is refused.
- The epoch-merging and count-rule fixes are tested in test_v8_addendum_a1_rule.py (needs geopandas).
"""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
VAL = ROOT / "scripts" / "validation"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


S = _load("v8_score_a1", VAL / "v8_score.py")


# ── v8_score.py: never-reached guard ────────────────────────────────────────────────────────────

def _cands():
    rng = np.random.default_rng(1)
    cells = np.arange(100, dtype=np.int64)
    return {("SYN:1", "e1"): {"cell_id": cells, "score_M8": rng.uniform(size=100), "score_B1": rng.uniform(size=100)}}


def test_r61_never_reached_row_not_ranked():
    # [R61] R2-L fixture: cell 7 = the inside member, cell 42 = the 700 m 'never reached' member (ranked by the
    # frozen script); here only cell 7 is ranked.
    acc = {}
    pf = S.per_fire(_cands(), [("SYN:1", "CAN", "e1", 7, 0.0), ("SYN:1", "CAN", "e1", 42, 700.0)], ["M8", "B1"], acc)
    assert len(pf["SYN:1"]["r"]["M8"]) == 1
    assert acc["SYN:1"]["never_reached_gt540m"] == 1 and acc["SYN:1"]["ranked"] == 1


@pytest.mark.parametrize("out_m,cls", [(0.0, "positive"), (375.0, "positive"), (375.1, "outside_375_540m"),
                                       (450.0, "outside_375_540m"), (540.0, "outside_375_540m"),
                                       (540.1, "never_reached_gt540m"), (700.0, "never_reached_gt540m")])
def test_final_class_boundaries(out_m, cls):
    assert S.final_class(out_m) == cls


def test_skipped_rows_are_counted():
    rows = [("SYN:1", "CAN", "e1", 7, 0.0), ("SYN:1", "CAN", "e1", 42, 700.0), ("SYN:1", "CAN", "e1", 9, 450.0),
            ("SYN:1", "CAN", "e2", 3, 0.0),        # interval e2 has no candidate file (excluded interval)
            ("SYN:1", "CAN", "e1", 999, 100.0)]    # cell 999 is not a candidate
    acc = {}
    S.per_fire(_cands(), rows, ["M8", "B1"], acc)
    a = acc["SYN:1"]
    assert a["rows"] == 5
    assert (a["ranked"], a["never_reached_gt540m"], a["outside_375_540m"], a["interval_excluded"], a["not_candidate"]) == (1, 1, 1, 1, 1)
    summ = S.accounting_summary(acc)
    assert summ["total"]["count_secondary"] == 4          # 375-540 m is neither: not in the count
    assert summ["by_region"]["CAN"]["rows"] == 5


def test_rows_without_out_final_m_refused(tmp_path):
    with pytest.raises(ValueError):
        S.per_fire(_cands(), [("SYN:1", "CAN", "e1", 7)], ["M8"])
    (tmp_path / "candidates").mkdir()
    (tmp_path / "jumps.csv").write_text("fire_key,region,interval,cell_id\nSYN:1,CAN,e1,7\n")
    with pytest.raises(ValueError):
        S.load_dir(tmp_path)


def test_load_dir_reads_out_final_m(tmp_path):
    (tmp_path / "candidates").mkdir()
    c = _cands()[("SYN:1", "e1")]
    np.savez(tmp_path / "candidates" / "SYN:1__e1.npz", **c)
    (tmp_path / "jumps.csv").write_text("fire_key,region,interval,cell_id,out_final_m\nSYN:1,CAN,e1,7,0\nSYN:1,CAN,e1,42,700\n")
    cands, jumps, gate, counts = S.load_dir(tmp_path)
    assert jumps[1] == ("SYN:1", "CAN", "e1", 42, 700.0)
    acc = {}
    pf = S.per_fire(cands, jumps, ["M8", "B1"], acc)
    assert len(pf["SYN:1"]["r"]["M8"]) == 1
