"""Unit checks of the sealed-2026 confirm input rules (PREREG_SEALED_CONFIRM_H1H2 §2) that need no network."""
import importlib.util
from datetime import date
from pathlib import Path

import numpy as np

_P = Path(__file__).resolve().parents[3] / "scripts/validation/sealed2026/confirm_inputs.py"
_spec = importlib.util.spec_from_file_location("confirm_inputs", _P)
ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci)


def test_burn_date_day_pass_and_following_night_pass_share_a_day():
    assert ci.burn_date("2026-07-20T12:00:00") == date(2026, 7, 20)   # ~13:30 day pass of the 20th
    assert ci.burn_date("2026-07-21T00:00:00") == date(2026, 7, 20)   # ~01:30 night pass after it
    assert ci.burn_date("2026-07-20T00:00:00") == date(2026, 7, 19)


def test_crosswalk_groups_and_nonfuel():
    fb = np.array([[101, 122, 142, 165, 183, 202], [91, 98, 99, 93, -9999, np.nan]], dtype=np.float32)
    nat = ci.crosswalk(fb)
    assert nat[0].tolist() == [32, 32, 4, 7, 7, 7]          # grass O-1b, shrub C-4, timber C-7
    assert nat[1].tolist() == [32, 102, 101, 101, 101, 101]  # urban O-1b, water, non-fuel, no data


def test_crosswalk_codes_are_cfs_national():
    from firesim.data.fuel_loader import CODE_SCHEMES

    m = CODE_SCHEMES["cfs_national"]
    assert m[32].value == "O1b" and m[4].value == "C4" and m[7].value == "C7"
    assert m[101] is None and m[102] is None
