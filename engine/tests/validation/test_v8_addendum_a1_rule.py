"""Unit tests for PREREG_LR8 addendum A1 (2026-10-24), geometry part: v8_rule/v8_viirs_jumps.py epoch merging and
v8_rule/jump_rate_summary.py count rule, on the [R61] fixture cases (synthetic UTM 32611 detections;
reports/data/v7-arrivals-recoded-2026-10-24/scripts/v8_rule_fixtures.py). Skipped without geopandas/pyogrio."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

VAL = Path(__file__).resolve().parents[3] / "scripts" / "validation"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── v8_rule/v8_viirs_jumps.py and jump_rate_summary.py on the [R61] geometry fixtures ───────────

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")
pytest.importorskip("pyogrio")
J = _load("v8_viirs_jumps_a1", VAL / "v8_rule" / "v8_viirs_jumps.py")
R = _load("jump_rate_summary_a1", VAL / "v8_rule" / "jump_rate_summary.py")

UTM = 32611
CX, CY = 500000.0, 6100000.0
T0 = pd.Timestamp("2023-06-01 20:00")
SEPS = pd.DataFrame(columns=["src", "lon", "lat", "t"])


def _fire(per_box, rows):
    from shapely.geometry import box
    per = gpd.GeoSeries([box(*per_box)], crs=UTM).to_crs(4326).iloc[0]
    d = pd.DataFrame(rows, columns=["x", "y", "t", "confidence"])
    ll = gpd.GeoSeries(gpd.points_from_xy(d.x, d.y), crs=UTM).to_crs(4326)
    det = pd.DataFrame(dict(longitude=ll.x, latitude=ll.y, t=d.t, confidence=d.confidence, scan=0.4, track=0.4,
                            type=0, satellite="N", daynight="D", frp=5.0, fire_key="SYN:1"))
    fr = SimpleNamespace(fire_key="SYN:1", region="CAN", year=2023, assignment="training", geometry=per,
                         t0=T0 - pd.Timedelta(hours=1), t1=pd.NaT)
    return fr, det


def _block(t, cx=CX, cy=CY, half=1500):
    return [(cx + dx, cy + dy, t, "n") for dx in np.arange(-half, half + 1, 375) for dy in np.arange(-half, half + 1, 375)]


@pytest.mark.parametrize("gap,epochs", [(59, 2), (60, 3), (61, 3)])
def test_r61_epoch_merge_boundary(gap, epochs):
    big = (CX - 15000, CY - 15000, CX + 15000, CY + 15000)
    rows = _block(T0) + _block(T0 + pd.Timedelta(minutes=gap)) + _block(T0 + pd.Timedelta(hours=11))
    fr, det = _fire(big, rows)
    info, _ = J.catalogue_fire(fr, det, SEPS)
    assert info["n_epochs"] == epochs


def test_new_epoch_function():
    t = pd.to_datetime(["2023-06-01 00:00", "2023-06-01 00:59", "2023-06-01 01:59", "2023-06-01 03:00"]).values
    assert J.new_epoch(t).tolist() == [True, False, True, True]
    assert J.new_epoch(t[:0]).tolist() == []


def test_r61_count_rule_includes_never_reached():
    # [R61] R2-L fixture: members inside (~1.8 km out), 450 m and 700 m outside the final perimeter's east edge
    east_edge = CX + 1500 + 2500
    per2 = (CX - 15000, CY - 15000, east_edge, CY + 15000)
    t1 = T0 + pd.Timedelta(hours=11)
    rows = _block(T0) + [(CX + 1500 + 1800, CY + 6000, t1, "n"), (east_edge + 450, CY + 1500, t1, "n"),
                         (east_edge + 700, CY - 2500, t1, "n")]
    fr, det = _fire(per2, rows)
    _, js = J.catalogue_fire(fr, det, SEPS)
    Jd = pd.DataFrame(js)
    assert len(Jd) == 3
    M = R.rule_masks(Jd)
    assert int(M["rules"]["primary"].sum()) == 1            # ranked rule unchanged
    assert int(M["never_reached"].sum()) == 1
    assert int(M["out_375_540"].sum()) == 1
    assert int(M["rules"]["count_secondary"].sum()) == 2    # positives + never reached; 375-540 m excluded
