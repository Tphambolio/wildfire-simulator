"""Parity of the engine M8 port with the frozen v8 reference (PREREG_LR8 A2; [R70]).

The fixture (``data/m8_parity/``, README there) holds, for two intervals of each of two v8 decision
fires (one Canadian, one US), the M8 landing-field inputs re-derived with the v8 code and the interval
file the v8 decision run wrote. The engine port must reproduce:

1. the landing statistics A per 375 m candidate cell (``landing_field`` + ``block_sums``), and
2. the M8 score: engine chain (inputs -> A -> ``embers_m8.m8_score``) against the v8 code's own
   ``m8_score`` run on the v8 interval file, with the frozen beta.

The v8 ``m8_score`` is executed from ``scripts/validation/v8/intervals.py`` itself (its ``WAF`` and
``m8_score`` definitions, extracted with ``ast`` so CI needs no geopandas), after checking that file's
sha256 against PREREG_LR8 A2.12.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from firesim.spread import embers_m8 as E

DATA = Path(__file__).parent / "data" / "m8_parity"
REPO = Path(__file__).resolve().parents[3]
V8_INTERVALS = REPO / "scripts" / "validation" / "v8" / "intervals.py"
V8_INTERVALS_SHA256 = "4303e544736968f29f28dd827f662b0f6dfed9178109154ed309d7a5b4724dbe"  # A2.12
FIT_BETA = Path.home() / "dev/wildfire/validation-data/v8-pool/fit/fit_beta.json"

MANIFEST = json.loads((DATA / "manifest.json").read_text())


def _v8_m8_score():
    """``m8_score`` (and ``WAF``) from the frozen v8 file, executed as written."""
    src = V8_INTERVALS.read_text()
    assert hashlib.sha256(src.encode()).hexdigest() == V8_INTERVALS_SHA256
    tree = ast.parse(src)
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name == "m8_score")
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "WAF" for t in n.targets))]
    assert len(keep) == 2
    ns = {"np": np, "math": math}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(V8_INTERVALS), "exec"), ns)
    return ns["m8_score"]


def _load(entry):
    p = DATA / entry["file"]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == entry["sha256"], "fixture changed"
    z = np.load(p)
    shape = tuple(int(v) for v in z["shape"])
    n = shape[0] * shape[1]
    unpk = lambda k: np.unpackbits(z[k])[:n].reshape(shape).astype(bool)  # noqa: E731
    return z, unpk


def test_fixture_covers_two_decision_fires_in_both_regions():
    fires = {(m["fire_key"], m["region"]) for m in MANIFEST}
    assert len(fires) >= 2
    assert {r for _, r in fires} == {"CAN", "US"}


def test_frozen_beta_values():
    assert dict(E.M8_BETA) == {"b0": -22.670412340012348, "b_w": 0.0, "b_L": -4.67467954790124,
                               "b_m": -0.24413606414846878}
    assert E.M8_MODEL["fit_beta_json_sha256"] == (
        "fda5706c400cd74b07b7243c110031d09aa9160445cdcd2996a63ac8d18ec425")
    # b_m is the fixed Beverly & Wotton slope of fit.py
    assert E.M8_BETA["b_m"] == (math.log(0.9 / 0.1) - math.log(0.1 / 0.9)) / (13.0 - 31.0)


@pytest.mark.skipif(not FIT_BETA.exists(), reason="v8 data folder not on this machine (CI)")
def test_beta_matches_source_of_truth():
    raw = FIT_BETA.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == E.M8_MODEL["fit_beta_json_sha256"]
    assert json.loads(raw)["beta"] == dict(E.M8_BETA)


@pytest.mark.parametrize("entry", MANIFEST, ids=[m["file"] for m in MANIFEST])
def test_engine_m8_matches_v8(entry):
    z, unpk = _load(entry)
    act = unpk("act")
    zones = [E.ZoneWeather(u10=float(u), wd_from=float(w), ffmc=float(f), bui=float(b))
             for u, w, f, b in zip(z["z_u10"], z["z_wd_from"], z["z_ffmc"], z["z_bui"])]
    A, emission = E.landing_field(act, z["ros"], z["names"], z["pcs"], z["wcls"], z["gcur"],
                                  z["zone_grid"] if len(zones) > 1 else None, zones,
                                  float(z["hours"]), float(z["fmc"]), float(z["cell"]))
    assert emission > 0
    # the engine's own candidate cells are the v8 ones
    bj, bi, _ = E.candidate_blocks(unpk("closed"), unpk("burnable"), float(z["cell"]))
    np.testing.assert_array_equal(bj, z["v8_bj"])
    np.testing.assert_array_equal(bi, z["v8_bi"])
    Ab = E.block_sums(A, bj, bi)
    np.testing.assert_allclose(Ab, z["v8_A"], rtol=1e-6, atol=0)

    v8_npz = {"A": z["v8_A"], "u10": z["v8_u10"], "m_ff": z["v8_m_ff"], "zone": z["v8_zone"]}
    ref = _v8_m8_score()(v8_npz, dict(E.M8_BETA))
    zb = z["v8_zone"]
    # engine chain: engine A, engine zone weather (u10 / m_ff from the interval weather)
    eng = E.m8_score(Ab, np.array([zw.u10 for zw in zones]), np.array([zw.m_ff for zw in zones]), zb)
    assert ref.shape == eng.shape and (ref > 0).any()
    max_abs = float(np.max(np.abs(eng - ref)))
    max_rel = float(np.max(np.abs(eng - ref) / np.maximum(np.abs(ref), 1e-300)))
    print(f"{entry['file']}: n={len(ref)} max|engine-v8|={max_abs:.3e} max rel={max_rel:.3e}")
    np.testing.assert_allclose(eng, ref, rtol=1e-5, atol=0)
    # ranks (what the app shows) are identical
    np.testing.assert_array_equal(E.tiers(eng), E.tiers(ref))
    # same inputs as v8 (float32 interval weather): identical
    np.testing.assert_allclose(E.m8_score(z["v8_A"], z["v8_u10"], z["v8_m_ff"], zb), ref, rtol=1e-12)
