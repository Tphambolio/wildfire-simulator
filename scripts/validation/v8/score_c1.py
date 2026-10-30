"""M8-C1 scoring kernel (docs/PREREG_M8_C1.md sections 2-4 [R76]; owner decision D10, 2026-10-30).

For a candidate cell c of a v8 interval (the frozen ``intervals/<epoch>.npz`` product of ``intervals.run_fire``):

    score_C1(c) = exp( f(d_act) ) * p_e(m_ff)

- ``d_act``: v8's stored distance (m) from the candidate block centre to the nearest active-segment cell at t_start
  (``intervals.py`` writes the perimeter distance there when an interval has no active segment; used as stored).
  Converted to km here.
- ``f``: the frozen short-range decay of PREREG_M8_C1 section 3, read from the shape file's ``f_of_d`` (nodes, floor),
  the file pinned by path and SHA-256. Piecewise linear through the seven nodes (1.5 ... 8.0 km), constant at the
  first node's value below 1.5 km and at the floor (-1.03) at and beyond 8.0 km. Training split only.
- ``p_e``: M8's establishment moisture factor exactly as M8 computes it (``intervals.m8_score``):
  exp(B_M (m_ff - 21)), B_M = ``fit.B_M`` (Beverly & Wotton grass curve, -0.2441 per %, fixed in PREREG_LR8 A2),
  m_ff the interval's fine-fuel moisture at the cell's zone. No beta_0 (ranking only), no wind, sector or receptor term.

M8's own path (``intervals.py``, ``common.py``, ``v8_score.py``) is not touched; ``score(npz, kernel=...)`` selects
``kernel="m8"`` (``intervals.m8_score``, unchanged) or ``kernel="c1"``. This module scores nothing by itself: it is the
kernel LR8's registered one-shot unseal calls.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .fit import B_M
from .intervals import m8_score

SHAPE_PATH = Path(os.environ.get(
    "FIRESIM_M8C1_SHAPE",
    Path.home() / "dev/wildfire/reports/data/ml-ember-cast-2026-10-29/stage2/m8c1_decay_shape.json"))
SHAPE_SHA256 = "79aa8e1861a99f46f7e4dc86242f8cd7ecfc8811b54caaedf4ec6299971251d1"
M_REF = 21.0  # % (PREREG_LR8 A2 centring, as m8_score)

_SHAPE = None


def load_shape(path: Path | None = None) -> dict:
    """The frozen ``f_of_d`` block, after the SHA-256 and consistency checks."""
    global _SHAPE
    if path is None and _SHAPE is not None:
        return _SHAPE
    p = Path(path) if path is not None else SHAPE_PATH
    raw = p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHAPE_SHA256:
        raise RuntimeError(f"{p} differs from the PREREG_M8_C1 sha256 {SHAPE_SHA256}")
    f = json.loads(raw)["f_of_d"]
    x = np.array([n[0] for n in f["nodes"]], np.float64)
    y = np.array([n[1] for n in f["nodes"]], np.float64)
    if not (np.all(np.diff(x) > 0) and np.all(np.diff(y) <= 0)):
        raise RuntimeError("f_of_d nodes are not increasing in d and non-increasing in f")
    if f["below_first_node"] != y[0] or f["floor"] != y[-1] or f["floor_from_km"] != x[-1]:
        raise RuntimeError("f_of_d below_first_node / floor / floor_from_km disagree with the nodes")
    shape = dict(x=x, y=y, floor=float(f["floor"]), floor_from_km=float(f["floor_from_km"]),
                 below=float(f["below_first_node"]), check_points={float(k): v for k, v in f["check_points"].items()})
    if path is None:
        _SHAPE = shape
    return shape


def f_of_d(d_km) -> np.ndarray:
    """Frozen log relative likelihood of a counted jump at d km from the active edge (PREREG_M8_C1 section 3)."""
    s = load_shape()
    d = np.asarray(d_km, np.float64)
    v = np.interp(d, s["x"], s["y"], left=s["below"], right=s["floor"])
    return np.where(d >= s["floor_from_km"], s["floor"], v)


def p_e(m_ff) -> np.ndarray:
    """M8's establishment moisture factor without beta_0: exp(B_M (m_ff - 21)), as in ``intervals.m8_score``."""
    return np.exp(B_M * (np.asarray(m_ff, np.float64) - M_REF))


def score_c1_arrays(d_act_km, m_ff_cell) -> np.ndarray:
    """score_C1 from per-candidate d_act (km) and m_ff (%) arrays."""
    return np.exp(f_of_d(d_act_km)) * p_e(m_ff_cell)


def score_c1(npz) -> np.ndarray:
    """score_C1 per candidate cell of one v8 interval product (``d_act`` in m, ``m_ff`` per zone, ``zone``)."""
    d_km = np.asarray(npz["d_act"]).astype(np.float64) / 1000.0
    m = npz["m_ff"][npz["zone"]].astype(np.float64)
    return score_c1_arrays(d_km, m)


def score(npz, kernel: str = "m8", beta: dict | None = None) -> np.ndarray:
    """Kernel selector for the scorer: ``"m8"`` = ``intervals.m8_score(npz, beta)`` unchanged; ``"c1"`` = M8-C1."""
    if kernel == "m8":
        return m8_score(npz, beta)
    if kernel == "c1":
        return score_c1(npz)
    raise ValueError(f"unknown kernel {kernel!r}")
