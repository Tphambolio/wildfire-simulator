"""Hamada urban fire spread rates (empirical; illustrative, not validated in Canada).

Specification: ``docs/structure-spread-spec.md`` §4. Built from the published papers only.

Equations (direction i = d downwind, s sidewind, u upwind; building size a0 and separation d in
metres; wind V in m/s; combustible fraction fb):

    T_i = (1 - fb) [3 + 0.375 a0 + 8 d / (c4_i + c5_i V)]
          + fb / C_i(V) [5 + 0.625 a0 + 16 d / (c4_i + c5_i V)]          minutes
    C_i(V) = c1_i (1 + c2_i V + c3_i V^2)
    U_i = (a0 + d) / T_i                                                  m/min

Sources:
- Purnomo et al. (2026), Fire Safety J. 161: 104651, eq 2 (p.3) and supplementary material
  ("Overview of HAMADA model": T_i, C(V), coefficient table, Hazus low-wind correction).
- Qin (2025), PhD dissertation, Univ. Maryland: eqs 2.66-2.80 and Table 2.2 (pp.38-40). Its eq
  2.70 prints c4 on V^2; the supplement prints c3 (spec C6).
- Himoto & Tanaka (2008), Fire Safety J. 43: 477-494, eqs 42-43 (p.25 of the author
  manuscript): Hamada's rates in m/min, hence T in minutes (spec C10).

FireSim adaptations (heuristics, spec §4.1-4.2):
- Hazus correction (V < 10 m/s) in its long-time limit: V_i = (V/10) U_i + (1 - V/10) G with
  G = sqrt(((U_d + U_u)/2) U_s), derived from the supplement's / Qin eqs 2.74-2.79.
- Direction: the Hamada ellipse itself (downwind reach V_d, upwind V_u, crosswind V_s), built
  like the FBP ellipse (``spread/ellipse.py``), instead of Qin eq 2.80 (spec C8).
"""

from __future__ import annotations

import numpy as np

# (c1, c2, c3, c4, c5) per direction: Purnomo et al. 2026 SI table = Qin 2025 Table 2.2
HAMADA_COEFFS: dict[str, tuple[float, float, float, float, float]] = {
    "d": (1.6, 0.1, 0.007, 25.0, 2.5),
    "s": (1.0, 0.0, 0.005, 5.0, 0.25),
    "u": (1.0, 0.0, 0.002, 5.0, 0.2),
}
HAZUS_WIND_M_S = 10.0  # correction applies below this wind speed (Purnomo et al. 2026 SI)
DEFAULT_COMBUSTIBLE_FRACTION = 1.0  # Qin 2025 p.224 (Thomas Fire run); not measured for Edmonton


def hamada_time_min(direction: str, a0, d, wind_m_s, fb: float = DEFAULT_COMBUSTIBLE_FRACTION):
    """Hamada T_i: minutes for fire to reach the adjacent building in ``direction`` (d/s/u)."""
    c1, c2, c3, c4, c5 = HAMADA_COEFFS[direction]
    a0 = np.asarray(a0, dtype=float)
    d = np.asarray(d, dtype=float)
    v = np.asarray(wind_m_s, dtype=float)
    if np.any(v < 0) or np.any(a0 < 0) or np.any(d < 0):
        raise ValueError("a0, d and wind speed must be >= 0")
    if not 0.0 <= fb <= 1.0:
        raise ValueError("combustible fraction must be in [0, 1]")
    c_v = c1 * (1.0 + c2 * v + c3 * v * v)
    x = c4 + c5 * v
    return (1.0 - fb) * (3.0 + 0.375 * a0 + 8.0 * d / x) + fb / c_v * (5.0 + 0.625 * a0 + 16.0 * d / x)


def hamada_axis_rates(a0, d, wind_m_s, fb: float = DEFAULT_COMBUSTIBLE_FRACTION,
                      hazus: bool = True):
    """(V_d, V_s, V_u) in m/min: Hamada U_i = (a0 + d) / T_i, with the Hazus low-wind blend."""
    u = {k: (np.asarray(a0, float) + np.asarray(d, float)) / hamada_time_min(k, a0, d, wind_m_s, fb)
         for k in ("d", "s", "u")}
    if not hazus:
        return u["d"], u["s"], u["u"]
    v = np.asarray(wind_m_s, dtype=float)
    w = np.clip(v / HAZUS_WIND_M_S, 0.0, 1.0)  # w = 1 at and above 10 m/s: no correction
    g = np.sqrt((u["d"] + u["u"]) / 2.0 * u["s"])
    return tuple(w * u[k] + (1.0 - w) * g for k in ("d", "s", "u"))


def ellipse_rate(v_d, v_s, v_u, cos_theta):
    """Rate toward angle theta from downwind on the ellipse with downwind reach ``v_d``, upwind
    reach ``v_u`` and semi-minor ``v_s`` (same construction as the FBP ellipse,
    ``spread.ellipse.calculate_ros_at_theta``). Returns v_d at theta = 0 and v_u at pi."""
    v_d, v_s, v_u, ct = (np.asarray(a, dtype=float) for a in (v_d, v_s, v_u, cos_theta))
    ct = np.clip(ct, -1.0, 1.0)
    st2 = 1.0 - ct * ct
    a = (v_d + v_u) / 2.0
    c = (v_d - v_u) / 2.0
    big_a = v_s * v_s * ct * ct + a * a * st2
    disc = np.maximum(v_s * v_s * c * c * ct * ct + big_a * v_d * v_u, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = (v_s * v_s * c * ct + v_s * np.sqrt(disc)) / big_a
    return np.where(big_a > 0.0, r, 0.0)


def rate_toward(a0, d, wind_m_s, cos_theta, fb: float = DEFAULT_COMBUSTIBLE_FRACTION):
    """Hamada spread rate (m/min) from a burning building toward a neighbour at angle theta
    from the downwind direction (``cos_theta`` = cos theta)."""
    v_d, v_s, v_u = hamada_axis_rates(a0, d, wind_m_s, fb)
    return ellipse_rate(v_d, v_s, v_u, cos_theta)


def crossing_time_min(a0, d, wind_m_s, cos_theta, fb: float = DEFAULT_COMBUSTIBLE_FRACTION):
    """Minutes for fire to pass from a burning building to its neighbour: (a0 + d) / rate.
    Straight downwind at V >= 10 m/s this is Hamada's T_d."""
    return (np.asarray(a0, float) + np.asarray(d, float)) / rate_toward(a0, d, wind_m_s, cos_theta, fb)
