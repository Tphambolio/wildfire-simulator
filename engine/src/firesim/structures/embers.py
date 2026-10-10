"""Ember (firebrand) ignition of building units: generation, transport, pooling, ignition.

Specification: ``docs/structure-spread-spec.md`` §6. **Illustrative — not validated in
Canada.** Built from the published papers only (no ELMFIRE source was opened):

- **FSJ104686** Qin, Purnomo, Theodori, Zamanialaei, Lautenberger, Gollner, Trouvé (2026),
  *Fire Safety J.* 162: 104686 (pages as printed).
- **Qin25** Qin (2025), PhD dissertation, University of Maryland (printed page numbers).
- **HT08** Himoto & Tanaka (2008), *Fire Safety J.* 43: 477-494 (author manuscript pages).
- **PROCI24** Purnomo et al. (2024), *Proc. Combust. Inst.* 40: 105755.
- **Andrews09** Andrews (2009), BehavePlus 5.0 Variables, USDA FS RMRS-GTR-213WWW, p.58.

Model (one unit per building, spec §2):

1. A unit that is involved (front contact, Hamada or embers) burns a **design fire** from its
   involvement time: HRR = HRRPUA(t) · footprint area (Qin25 eqs 6.2, 6.10, pp.146, 169;
   PROCI24 p.3). Default 150 kW/m², 5 / 1 / 60 min (PROCI24 p.3; owner decision D1);
   scenario 400 kW/m², 300 / 3600 / 300 s (FSJ104686 p.2, Fig. 1; Qin25 p.147).
2. It emits ``GR = GR' · HRR`` embers per second (Qin25 eq 3.1 and Table 6.1, p.153;
   FSJ104686 p.4): GR' = 10 pcs/(MW·s) for structures [T] (5.68, Qin25 eq 3.17 p.66, as a
   scenario) and 33.3 pcs/(MW·s) for vegetation (Qin25 eq 3.8, p.60).
3. Embers are carried downwind with the Himoto lognormal flight-distance PDF for structures
   (HT08 eqs 38-40, pp.22-24; Qin25 eqs 6.3-6.4, pp.148-149) or the Sardoy lognormal for
   vegetation (Qin25 eqs 4.1-4.6, pp.72-73), truncated at the 99th percentile (Qin25 eqs
   4.8-4.9, pp.74-75; FSJ104686 p.3), times a normal crosswind PDF with σ_Y = 0.92 D (HT08
   eq 39, p.23). Emission is from the unit's centroid and nothing lands on the emitting
   footprint (Qin25 §6.3.1 item 3, p.170).
4. Embers that land on a unit's footprint are pooled per unit (Qin25 eq 6.9, p.168) as an
   expected value (the deterministic Eulerian deposition of Qin25 eq 4.17, p.80 and eq 6.5,
   p.156; no random draws, so runs are repeatable).
5. The unit ignites when the pooled ember mass per area ψ = N · 0.2 g / A reaches
   ψ*(v_air) = C / ((v_air − v_min)(v_max − v_air)) (FSJ104686 eq 1, p.3; Qin25 eq 5.12,
   p.120), after the delays TOA + t_ign,small + t_ign,large = flight time + 42 s + 300 s
   (FSJ104686 pp.4-5), and then starts its own design fire (Qin25 p.122).

FireSim choices (labelled [H] in the spec) are marked in the code. The wind heights are the
least certain part; see ``u10_to_u6`` and the spec §6 notes (C15-C17).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.special import ndtr, ndtri

# ----------------------------------------------------------------------------- constants

GR_STRUCTURE = 10.0  # pcs/(MW·s) [T]: Qin25 p.147, Table 6.1 p.153; FSJ104686 p.4
GR_STRUCTURE_REFERENCE = 5.68  # pcs/(MW·s): Qin25 eq 3.17, p.66 (scenario)
GR_VEGETATION = 33.3  # pcs/(MW·s): Qin25 eq 3.8, p.60; Table 6.1 p.153
EMBER_MASS_G = 0.2  # FSJ104686 p.3; Qin25 eq 3.7 / p.157

# Himoto & Tanaka transport (HT08 eqs 39-40, pp.23-24; Qin25 eqs 6.3-6.4, p.149)
HIMOTO_MU = 0.47  # mean flight distance / D = 0.47 B*^(2/3)
HIMOTO_SIGMA = 0.88  # std of flight distance / D = 0.88 B*^(1/3)
HIMOTO_SIGMA_Y = 0.92  # crosswind std / D (HT08 eq 39, p.23)
RHO_P = 100.0  # firebrand density, kg/m3 (Qin25 p.149)
D_P = 0.005  # firebrand size, m (Qin25 p.149)
RHO_AIR = 1.1  # kg/m3 (Qin25 p.73; "identical to Sardoy's", p.149)
CP_AIR = 1.0  # kJ/(kg K)
T_AIR = 300.0  # K
G = 9.81  # m/s2
TRUNCATION_Q = 0.99  # Qin25 eq 4.8 (ε = 0.01); FSJ104686 p.3 ("truncated at the 99th percentile")

# Ignition (FSJ104686 eq 1, p.3; Qin25 eq 5.12, p.120): PTW at 2.0 % moisture
PSI_C = 0.211  # g/cm2
V_MIN = -0.073  # m/s
V_MAX = 4.111  # m/s
V_AIR_FACTOR = 0.064  # v_air(2 m) / U(6.1 m): Qin25 p.157 ("correction factor of 0.064"),
#                       FSJ104686 p.5 (17.9 m/s -> v_air ≈ 1.1 m/s)
TAU_SMALL_S = 42.0  # FSJ104686 p.4 (C4: Qin25 p.121 has 47 s)
T_LARGE_S = 300.0  # FSJ104686 p.4; Qin25 p.150 (structures, t² growth to 1 MW)
U10_TO_U6 = 1.0 / 1.15  # Andrews09 p.58: 10 m wind / 1.15 = 20 ft (6.1 m) wind [H]

DEFAULT_DT_MIN = 0.5  # accumulation step [H]; crossing times interpolated inside it

SOURCE_FRONT = 1
SOURCE_STRUCTURE = 2  # Hamada building to building
SOURCE_EMBER = 3


@dataclass(frozen=True)
class DesignFire:
    """Linear growth -> plateau -> linear decay of HRR per unit footprint area."""

    hrrpua_kw_m2: float
    grow_s: float
    full_s: float
    decay_s: float

    @property
    def duration_s(self) -> float:
        return self.grow_s + self.full_s + self.decay_s

    def hrrpua(self, t_s):
        """HRR per unit area (kW/m²) at ``t_s`` seconds after involvement (Qin25 eq 6.2)."""
        t = np.asarray(t_s, dtype=float)
        p, g, f, d = self.hrrpua_kw_m2, self.grow_s, self.full_s, self.decay_s
        out = np.where(t < g, p * t / g, p)
        out = np.where(t >= g + f, p * (1.0 - (t - g - f) / d), out)
        return np.where((t < 0) | (t >= g + f + d), 0.0, out)

    def energy_per_area(self, t_s):
        """∫₀ᵗ HRRPUA dt (kJ/m²); vectorised."""
        t = np.clip(np.asarray(t_s, dtype=float), 0.0, self.duration_s)
        p, g, f, d = self.hrrpua_kw_m2, self.grow_s, self.full_s, self.decay_s
        e_grow = p * np.minimum(t, g) ** 2 / (2.0 * g)
        e_full = p * np.clip(t - g, 0.0, f)
        td = np.clip(t - g - f, 0.0, d)
        e_decay = p * (td - td * td / (2.0 * d))
        return e_grow + e_full + e_decay


DESIGN_FIRES = {
    # PROCI24 p.3 (after Maranghides & Johnsson): 150 kW/m², 5 min growth, 1 min fully
    # developed, 60 min decay. Default (owner decision D1, spec C1).
    150: DesignFire(150.0, 300.0, 60.0, 3600.0),
    # FSJ104686 p.2 (Fig. 1) and Qin25 p.147: 400 kW/m², 300 s / 3600 s / 300 s. Scenario.
    400: DesignFire(400.0, 300.0, 3600.0, 300.0),
}


@dataclass(frozen=True)
class EmberOptions:
    """Ember stage settings (spec §6). Defaults are the specified values."""

    design_fire_kw_m2: int = 150
    gr_structure: float = GR_STRUCTURE
    gr_vegetation: float = GR_VEGETATION
    from_wildland: bool = True  # Sardoy embers from the burning grid cells onto units
    lateral: bool = True  # HT08 crosswind normal; False = no crosswind loss (1-D benchmark)
    hamada: bool = True  # keep Hamada building-to-building spread alongside embers
    dt_min: float = DEFAULT_DT_MIN

    @property
    def design_fire(self) -> DesignFire:
        try:
            return DESIGN_FIRES[int(self.design_fire_kw_m2)]
        except KeyError as e:
            raise ValueError(f"design fire must be one of {sorted(DESIGN_FIRES)} kW/m²") from e

    def params(self) -> dict:
        return {"embers": True, "design_fire_kw_m2": int(self.design_fire_kw_m2),
                "ember_generation_pcs_per_mw_s": float(self.gr_structure),
                "embers_from_wildland": bool(self.from_wildland)}


# ----------------------------------------------------------------------------- physics


def u10_to_u6(u10_ms):
    """6.1 m (20 ft) wind from FireSim's 10 m open wind: ÷ 1.15 (Andrews09 p.58) [H].

    The ember papers fly embers at the 6.1 m wind (FSJ104686 p.3; Qin25 p.78). The 1.15 ratio
    is the BehavePlus open-terrain convention; applying it in a town is a FireSim choice.
    """
    return np.asarray(u10_ms, dtype=float) * U10_TO_U6


def v_air(u6_ms):
    """Near-ground (2 m) wind at the ember pile: 0.064 × the 6.1 m wind (Qin25 p.157, Albini's
    profile, eq 5.13 p.122, with the factor the authors used; FSJ104686 p.5: 17.9 -> 1.1 m/s)."""
    return V_AIR_FACTOR * np.asarray(u6_ms, dtype=float)


def psi_critical(v_air_ms):
    """Critical ember mass per area ψ* (g/cm²) for small-flame ignition of PTW (FSJ104686 eq 1,
    p.3; Qin25 eq 5.12, p.120). Infinite outside v_min < v_air < v_max (no ignition)."""
    v = np.asarray(v_air_ms, dtype=float)
    ok = (v > V_MIN) & (v < V_MAX)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = PSI_C / ((v - V_MIN) * (V_MAX - v))
    return np.where(ok, out, np.inf)


def critical_embers(area_m2, v_air_ms):
    """Embers needed on a footprint of ``area_m2``: ψ*/m_e × A (FSJ104686 p.5; Qin25 p.157)."""
    return psi_critical(v_air_ms) / EMBER_MASS_G * np.asarray(area_m2, dtype=float) * 1.0e4


def _mean_std_to_lognormal(mean, std):
    """(μ_L, σ_L) of ln X from the mean and std of X (HT08 p.23: "μ_X and σ_X need to be
    converted into the form of logarithm natural")."""
    s2 = np.log1p((np.asarray(std, float) / np.asarray(mean, float)) ** 2)
    return np.log(mean) - s2 / 2.0, np.sqrt(s2)


def himoto_b_star(hrr_kw, u_ms, d_m):
    """Himoto's dimensionless number B* (HT08 eq 40, p.24; Qin25 eq 6.4, p.149).

    ``u_ms`` is the wind for the PDF. FireSim passes its **10 m** wind: the published X_max
    of 65 / 84 / 109 m (FSJ104686 p.3) are reproduced only with B* evaluated at 1.15 × the
    17.9 m/s 6.1 m wind (spec §6, C16). ``d_m`` = √(floor area) (HT08 p.24).
    """
    u = np.asarray(u_ms, float)
    d = np.asarray(d_m, float)
    q = np.asarray(hrr_kw, float)
    return (u / np.sqrt(G * d) * (RHO_P * D_P / (RHO_AIR * d)) ** -0.75
            * np.sqrt(np.maximum(q, 0.0) / (RHO_AIR * CP_AIR * T_AIR * math.sqrt(G) * d ** 2.5)))


def himoto_lognormal(hrr_kw, u10_ms, d_m):
    """(μ_L, σ_L) of ln(flight distance) for a burning structure (HT08 eqs 38-39; Qin25 eq 6.3).

    Undefined (nan) at zero wind or zero HRR; callers skip those sources."""
    b = himoto_b_star(hrr_kw, u10_ms, d_m)
    d = np.asarray(d_m, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return _mean_std_to_lognormal(HIMOTO_MU * b ** (2.0 / 3.0) * d,
                                      HIMOTO_SIGMA * b ** (1.0 / 3.0) * d)


def sardoy_lognormal(ib_mw_m, u_ms):
    """(μ, σ) of ln(flight distance) from a burning vegetation cell (Sardoy et al. 2008 as
    printed in Qin25 eqs 4.2-4.6, pp.72-73; FSJ104651 SI; IJWF24 eqs 6-10).

    ``u_ms`` is the 6.1 m wind: Qin25's worked case (p.124, 6.71 m/s at 6.1 m, 3,189 kW/m)
    gives μ = 2.18, σ = 1.23 only with the 6.1 m wind, although p.72 says 10 m (spec C17).
    """
    ib = np.maximum(np.asarray(ib_mw_m, float), 1e-9)
    u = np.maximum(np.asarray(u_ms, float), 1e-9)
    lc = (1000.0 * ib / (RHO_AIR * CP_AIR * T_AIR * math.sqrt(G))) ** (2.0 / 3.0)
    fr = u / np.sqrt(G * lc)
    low = fr <= 1.0
    mu = np.where(low, 1.47 * ib ** 0.54 * u ** -0.55 + 1.14, 1.32 * ib ** 0.26 * u ** 0.11 - 0.02)
    sg = np.where(low, 0.86 * ib ** -0.21 * u ** 0.44 + 0.19, 4.95 * ib ** -0.01 * u ** -0.02 - 3.48)
    return mu, np.maximum(sg, 1e-6)


_Z_TRUNC = float(ndtri(TRUNCATION_Q))  # one-sided 99th percentile of the standard normal
_Z_TRUNC_Y = float(ndtri(0.5 + TRUNCATION_Q / 2.0))  # two-sided 99 % (crosswind) [H]


def x_max(mu_l, sigma_l):
    """99th-percentile flight distance X_max (Qin25 eq 4.8, p.74; FSJ104686 p.3)."""
    return np.exp(np.asarray(mu_l, float) + np.asarray(sigma_l, float) * _Z_TRUNC)


def _cdf_x(x, mu_l, sigma_l):
    """Truncated lognormal CDF (Qin25 eq 4.9): F(min(x, X_max)) / 0.99, 0 for x <= 0."""
    x = np.asarray(x, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (np.log(np.maximum(x, 1e-12)) - mu_l) / sigma_l
    return np.where(x > 0, np.minimum(ndtr(z), TRUNCATION_Q) / TRUNCATION_Q, 0.0)


def _cdf_y(y, sigma_y):
    """Crosswind normal CDF truncated at the two-sided 99 % and renormalised [H]
    (HT08 eq 38 normal; truncation by analogy with Qin25 p.75)."""
    z = np.clip(np.asarray(y, float) / sigma_y, -_Z_TRUNC_Y, _Z_TRUNC_Y)
    return (ndtr(z) - (1.0 - TRUNCATION_Q) / 2.0) / TRUNCATION_Q


def landing_fraction(x0, x1, y0, y1, mu_l, sigma_l, sigma_y=None, fill=1.0):
    """Expected share of the embers landing on a target, in the source's wind frame.

    The target is its bounding box in that frame, [x0, x1] downwind of the source's downwind
    edge and [y0, y1] across the wind from the source centroid, times ``fill`` = footprint
    area / box area (exact for a wind-aligned rectangle; FireSim approximation otherwise [H]).
    ``sigma_y=None`` drops the crosswind factor (the 1-D benchmark of FSJ104686 §3).
    """
    fx = _cdf_x(x1, mu_l, sigma_l) - _cdf_x(x0, mu_l, sigma_l)
    if sigma_y is None:
        fy = 1.0
    else:
        fy = _cdf_y(y1, sigma_y) - _cdf_y(y0, sigma_y)
    return np.maximum(fx, 0.0) * np.maximum(fy, 0.0) * fill


# ----------------------------------------------------------------------------- geometry


@dataclass
class _Frames:
    """Per wind direction: each unit's extent along (sx) and across (sy) the wind."""

    sx0: np.ndarray
    sx1: np.ndarray
    sy0: np.ndarray
    sy1: np.ndarray
    cx: np.ndarray
    cy: np.ndarray
    fill: np.ndarray


def _unit_frames(units, ux: float, uy: float) -> _Frames:
    import shapely

    coords, idx = shapely.get_coordinates(units.footprints, return_index=True)
    along = coords[:, 0] * ux + coords[:, 1] * uy
    across = -coords[:, 0] * uy + coords[:, 1] * ux
    n = len(units)
    starts = np.searchsorted(idx, np.arange(n))
    sx0 = np.minimum.reduceat(along, starts)
    sx1 = np.maximum.reduceat(along, starts)
    sy0 = np.minimum.reduceat(across, starts)
    sy1 = np.maximum.reduceat(across, starts)
    box = np.maximum((sx1 - sx0) * (sy1 - sy0), 1e-9)
    fill = np.clip(units.area_m2 / box, 0.0, 1.0)
    return _Frames(sx0, sx1, sy0, sy1, units.x * ux + units.y * uy,
                   -units.x * uy + units.y * ux, fill)


@dataclass
class WildlandSources:
    """Burning grid cells as ember sources (Sardoy), in the units' local frame (metres)."""

    x: np.ndarray
    y: np.ndarray
    start_min: np.ndarray
    end_min: np.ndarray
    intensity_kw_m: np.ndarray
    cell_size_m: float

    @classmethod
    def from_emitters(cls, emitters, frame) -> "WildlandSources | None":
        """From the grid run's ``Emitters`` (needs ``intensity_kw_m``)."""
        inten = getattr(emitters, "intensity_kw_m", None)
        if emitters is None or inten is None or len(emitters.x) == 0:
            return None
        lat = emitters.lat0 + np.asarray(emitters.y) / emitters.m_per_deg_lat
        lng = emitters.lng0 + np.asarray(emitters.x) / emitters.m_per_deg_lng
        x, y = frame.to_local(lat, lng)
        keep = np.asarray(inten) > 0
        return cls(np.asarray(x)[keep], np.asarray(y)[keep],
                   np.asarray(emitters.start_min, float)[keep],
                   np.asarray(emitters.end_min, float)[keep],
                   np.asarray(inten, float)[keep], float(emitters.cell_size))

    def safe_reach_m(self, u6_speeds, duration_min: float, gr: float = GR_VEGETATION,
                     footprint_allowance_m: float = 100.0) -> float:
        """Distance from the burned cells beyond which wildland embers alone cannot ignite any
        building, for the reachable box (spec §2, §6).

        Sardoy's X_max reaches kilometres at FBP crown-fire intensities (Fr <= 1 branch), so
        the box is not grown to X_max. Instead: a footprint of area A at distance >= r from
        every burned cell centre lies at |X| >= r/√2 or |Y| >= r/√2 in any wind frame, so it
        receives at most A · Σ_c N_c · sup f_X · sup f_Y embers, with N_c the cell's whole
        emission and the sups taken over that region (and over the run's wind speeds). Its
        ember mass per area is then below Σ_c N_c m_e sup f_X sup f_Y, independent of A; the
        returned r is the smallest (10 m steps) where that bound is below the smallest ψ* of
        the run, plus ``footprint_allowance_m`` for footprints whose wind-frame box is larger
        than the footprint (FireSim bound [H]).
        """
        speeds = [float(u) for u in np.atleast_1d(u6_speeds) if u > 0]
        if len(self.x) == 0 or not speeds:
            return 0.0
        psi_min = float(np.min(psi_critical(v_air(np.asarray(speeds)))))
        if not np.isfinite(psi_min):
            return 0.0
        psi_min_g_m2 = psi_min * 1.0e4
        dur_s = np.clip(np.minimum(self.end_min, duration_min) - self.start_min, 0.0, None) * 60.0
        n_c = gr * self.intensity_kw_m / 1000.0 * self.cell_size_m * dur_s  # embers per cell
        h = self.cell_size_m / math.sqrt(2.0)  # largest centre-to-edge offset along any wind
        sy = HIMOTO_SIGMA_Y * self.cell_size_m
        y_max = _Z_TRUNC_Y * sy
        fy_max = 1.0 / (sy * math.sqrt(2.0 * math.pi)) / TRUNCATION_Q
        r_grid = np.arange(0.0, 20000.0, 10.0)
        worst = np.zeros(len(r_grid))
        for u6 in speeds:
            mu, sg = sardoy_lognormal(self.intensity_kw_m / 1000.0, u6)
            xm = x_max(mu, sg)
            mode = np.minimum(np.exp(mu - sg * sg), xm)

            def fx(x):
                x = np.maximum(x, 1e-9)
                d = np.exp(-((np.log(x) - mu) ** 2) / (2 * sg * sg)) / (x * sg * math.sqrt(2 * math.pi))
                return np.where(x <= xm, d / TRUNCATION_Q, 0.0)

            fx_max = fx(mode)
            tot = np.zeros(len(r_grid))
            for k, r in enumerate(r_grid):
                a = max(r / math.sqrt(2.0) - h, 0.0)
                fx_sup = fx(np.maximum(a, mode))
                b = r / math.sqrt(2.0)
                fy_b = (math.exp(-b * b / (2 * sy * sy)) / (sy * math.sqrt(2 * math.pi)) / TRUNCATION_Q
                        if b <= y_max else 0.0)
                dens = np.maximum(fx_sup * fy_max, fx_max * fy_b)
                tot[k] = float(np.sum(n_c * dens)) * EMBER_MASS_G
                if tot[k] < psi_min_g_m2 and k > 0 and tot[k] <= tot[k - 1]:
                    tot[k + 1:] = 0.0
                    break
            worst = np.maximum(worst, tot)
        below = np.nonzero(worst < psi_min_g_m2)[0]
        r = float(r_grid[below[0]]) if len(below) else float(r_grid[-1])
        return r + footprint_allowance_m

    def reach_m(self, u6_max: float) -> float:
        """Farthest landing point from any cell centre (X_max + crosswind truncation)."""
        if len(self.x) == 0 or u6_max <= 0:
            return 0.0
        mu, sg = sardoy_lognormal(self.intensity_kw_m / 1000.0, u6_max)
        return float(np.max(x_max(mu, sg))) + self.cell_size_m * (0.5 * math.sqrt(2.0)
                                                                    + HIMOTO_SIGMA_Y * _Z_TRUNC_Y)


def structure_ember_reach(units, options: EmberOptions, u10_max: float) -> np.ndarray:
    """Per unit, the farthest a landing point can be from its footprint (metres): the
    circumradius plus X_max at the design-fire peak in the strongest wind, plus the crosswind
    truncation. Used to size the reachable box (spec §2)."""
    n = len(units)
    if n == 0 or u10_max <= 0:
        return np.zeros(n)
    import shapely

    coords, idx = shapely.get_coordinates(units.footprints, return_index=True)
    r = np.zeros(n)
    np.maximum.at(r, idx, np.hypot(coords[:, 0] - units.x[idx], coords[:, 1] - units.y[idx]))
    peak = options.design_fire.hrrpua_kw_m2 * units.area_m2
    d = units.size_m
    mu, sg = himoto_lognormal(peak, u10_max, d)
    xm = np.nan_to_num(x_max(mu, sg), nan=0.0)
    ym = HIMOTO_SIGMA_Y * d * _Z_TRUNC_Y if options.lateral else np.zeros(n)
    return np.hypot(r + xm, ym)


# ----------------------------------------------------------------------------- the coupled run


@dataclass
class CoupledResult:
    t_min: np.ndarray
    source: np.ndarray
    parent: np.ndarray
    ember_from_wildland: np.ndarray
    embers_received: np.ndarray = field(default_factory=lambda: np.zeros(0))


def coupled_spread(units, t_front_min, wind, *, duration_min: float, fb: float,
                   options: EmberOptions, wildland: WildlandSources | None = None,
                   cross=None) -> CoupledResult:
    """Front contact + Hamada building-to-building + ember ignition, first times per unit.

    Event-driven for front and Hamada (exact crossing times, as ``hamada_spread``), stepped
    every ``options.dt_min`` for ember accumulation (expected-value pooling; the step at
    which a unit's pool reaches its threshold is interpolated linearly). Deterministic.

    Args:
        units: building units (``StructureUnits``).
        t_front_min: front-contact minutes per unit (inf = none).
        wind: ``WindPeriod`` list (10 m open wind, km/h, FROM degrees).
        duration_min: end of the run.
        fb: Hamada combustible fraction.
        options: ember settings.
        wildland: burning grid cells as ember sources (``None`` = structures only).
        cross: the Hamada crossing function (``spread._cross``), injected to avoid a cycle.
    """
    import shapely

    n = len(units)
    df = options.design_fire
    burn_min = df.duration_s / 60.0
    t = np.asarray(t_front_min, dtype=float).copy()
    t[t > duration_min] = np.inf
    source = np.where(np.isfinite(t), SOURCE_FRONT, 0).astype(np.int8)
    parent = np.full(n, -1, dtype=np.int64)
    from_wild = np.zeros(n, dtype=bool)
    pool = np.zeros(n)
    committed = np.zeros(n, dtype=bool)
    done = np.zeros(n, dtype=bool)

    starts = np.array([w.start_min for w in wind], dtype=float)
    u10 = np.array([w.wind_speed_kmh / 3.6 for w in wind])
    to = np.radians(np.array([w.wind_direction_deg for w in wind]) + 180.0)
    wx, wy = np.sin(to), np.cos(to)  # downwind unit vector (x east, y north)

    heap = [(float(t[i]), int(i)) for i in np.nonzero(np.isfinite(t))[0]]
    heapq.heapify(heap)

    tree = shapely.STRtree(units.footprints) if n else None
    frames: dict[int, _Frames] = {}
    cand_struct: dict[tuple[int, int], np.ndarray] = {}
    cand_wild: dict[int, list] = {}
    burning: list[int] = []  # involved units still emitting (pruned as they burn out)

    def frames_for(k):
        if k not in frames:
            frames[k] = _unit_frames(units, float(wx[k]), float(wy[k]))
        return frames[k]

    def swath(cx, cy, edge_along, k, length, half_width):
        """Rectangles downwind of sources (local metres), for candidate queries; vectorised."""
        ux, uy = float(wx[k]), float(wy[k])
        nx, ny = -uy, ux
        cx, cy = np.atleast_1d(np.asarray(cx, float)), np.atleast_1d(np.asarray(cy, float))
        a0 = np.atleast_1d(np.asarray(edge_along, float)) - (cx * ux + cy * uy)
        a1 = a0 + np.atleast_1d(np.asarray(length, float))
        hw = np.broadcast_to(np.asarray(half_width, float), cx.shape)
        corners = []
        for a, s in ((a0, -hw), (a1, -hw), (a1, hw), (a0, hw)):
            corners.append(np.stack([cx + a * ux + s * nx, cy + a * uy + s * ny], axis=-1))
        return shapely.polygons(np.stack(corners, axis=1))

    peak_kw = df.hrrpua_kw_m2 * units.area_m2 if n else np.zeros(0)

    def struct_candidates(i, k):
        key = (i, k)
        if key not in cand_struct:
            fr = frames_for(k)
            mu, sg = himoto_lognormal(peak_kw[i], u10[k], units.size_m[i])
            xm = float(x_max(mu, sg)) if np.isfinite(mu) else 0.0
            if xm <= 0:
                cand_struct[key] = np.zeros(0, dtype=np.int64)
            else:
                hw = HIMOTO_SIGMA_Y * units.size_m[i] * _Z_TRUNC_Y if options.lateral else 1e6
                poly = swath(units.x[i], units.y[i], fr.sx1[i], k, xm, hw)[0]
                c = tree.query(poly)
                cand_struct[key] = c[c != i]
        return cand_struct[key]

    def wild_candidates(k):
        if k not in cand_wild:
            u6 = float(u10_to_u6(u10[k]))
            ws = wildland
            if ws is None or u6 <= 0 or len(ws.x) == 0:
                cand_wild[k] = []
            else:
                mu, sg = sardoy_lognormal(ws.intensity_kw_m / 1000.0, u6)
                xm = x_max(mu, sg)
                h = ws.cell_size_m / 2.0 * (abs(wx[k]) + abs(wy[k]))
                hw = HIMOTO_SIGMA_Y * ws.cell_size_m * _Z_TRUNC_Y if options.lateral else 1e6
                along = ws.x * wx[k] + ws.y * wy[k]
                src, tgt = tree.query(swath(ws.x, ws.y, along + h, k, xm, hw))
                cand_wild[k] = [mu, sg, h, src, tgt]
        return cand_wild[k]

    def period_at(tm):
        return max(int(np.searchsorted(starts, tm, side="right")) - 1, 0)

    def finalize_until(t_end):
        """Pop heap events up to ``t_end``; involved units start burning; Hamada crossings."""
        while heap and heap[0][0] <= t_end:
            ti, i = heapq.heappop(heap)
            if done[i] or ti > t[i]:
                continue
            done[i] = True
            burning.append(i)
            if not options.hamada:
                continue
            nb, sep = units.neighbours(i)
            if len(nb) == 0:
                continue
            keep = ~done[nb]
            nb, sep = nb[keep], sep[keep]
            if len(nb) == 0:
                continue
            a0 = (units.size_m[i] + units.size_m[nb]) / 2.0
            dx, dy = units.x[nb] - units.x[i], units.y[nb] - units.y[i]
            dist = np.hypot(dx, dy)
            safe = np.where(dist > 0, dist, 1.0)
            arrive = cross(ti, a0, sep, dx / safe, dy / safe, dist > 0, starts, u10, wx, wy, fb,
                           duration_min)
            better = arrive < t[nb]
            for j, tj in zip(nb[better], arrive[better]):
                t[j] = tj
                source[j] = SOURCE_STRUCTURE
                parent[j] = i
                from_wild[j] = False
                heapq.heappush(heap, (float(tj), int(j)))

    wild_end = float(np.max(wildland.end_min)) if wildland is not None and len(wildland.x) else -1.0
    use_wild = options.from_wildland and wildland is not None and len(wildland.x) > 0
    dt = float(options.dt_min)
    tc = 0.0
    while tc < duration_min:
        t_end = min(tc + dt, duration_min)
        finalize_until(t_end)
        burning[:] = [i for i in burning if t[i] + burn_min > tc]
        wild_active = use_wild and tc <= wild_end
        if not burning and not wild_active:
            if not heap:
                break
            nxt = heap[0][0]
            if nxt >= duration_min:
                break
            tc = max(tc + dt, math.floor(nxt / dt) * dt)
            continue
        k = period_at(tc)
        u6 = float(u10_to_u6(u10[k]))
        tgts, cnts, srcs, dists, wild_flags = [], [], [], [], []
        if u6 > 0:
            fr = frames_for(k)
            src_b = np.asarray(burning, dtype=np.int64)
            src_b = src_b[t[src_b] < t_end]
            if len(src_b):
                ti_b = t[src_b]
                a_b = np.maximum(tc, ti_b)
                e_kj = (df.energy_per_area((t_end - ti_b) * 60.0)
                        - df.energy_per_area((a_b - ti_b) * 60.0))
                count_b = options.gr_structure * e_kj * units.area_m2[src_b] / 1000.0  # pcs
                hrr_b = df.hrrpua(((a_b + t_end) / 2.0 - ti_b) * 60.0) * units.area_m2[src_b]
                ok = (count_b > 0) & (hrr_b > 0)
                src_b, count_b, hrr_b = src_b[ok], count_b[ok], hrr_b[ok]
            if len(src_b):
                cands = [struct_candidates(int(i), k) for i in src_b]
                lens = np.fromiter((len(c) for c in cands), dtype=np.int64, count=len(cands))
                if lens.sum():
                    cand = np.concatenate(cands)
                    pos = np.repeat(np.arange(len(src_b)), lens)
                    keep = ~(done[cand] | committed[cand])
                    cand, pos = cand[keep], pos[keep]
                    i_ = src_b[pos]
                    mu, sg = himoto_lognormal(hrr_b, u10[k], units.size_m[src_b])
                    edge = fr.sx1[i_]
                    sy = HIMOTO_SIGMA_Y * units.size_m[i_] if options.lateral else None
                    frac = landing_fraction(fr.sx0[cand] - edge, fr.sx1[cand] - edge,
                                            fr.sy0[cand] - fr.cy[i_], fr.sy1[cand] - fr.cy[i_],
                                            mu[pos], sg[pos], sy, fr.fill[cand])
                    hit = np.isfinite(frac) & (frac > 0)
                    if hit.any():
                        tgts.append(cand[hit])
                        cnts.append(count_b[pos[hit]] * frac[hit])
                        srcs.append(i_[hit])
                        dists.append(np.hypot(units.x[cand[hit]] - units.x[i_[hit]],
                                              units.y[cand[hit]] - units.y[i_[hit]]))
                        wild_flags.append(np.zeros(int(hit.sum()), dtype=bool))
            if wild_active:
                ws = wildland
                mu_w, sg_w, h, src, tgt = wild_candidates(k)
                if len(src):
                    a = np.maximum(tc, ws.start_min[src])
                    b = np.minimum(t_end, ws.end_min[src])
                    act = (b > a) & ~(done[tgt] | committed[tgt])
                    if act.any():
                        s, g_ = src[act], tgt[act]
                        hrr_mw = ws.intensity_kw_m[s] / 1000.0 * ws.cell_size_m  # Qin25 eq 6.1
                        count = options.gr_vegetation * hrr_mw * (b[act] - a[act]) * 60.0
                        along = ws.x[s] * wx[k] + ws.y[s] * wy[k] + h
                        across = -ws.x[s] * wy[k] + ws.y[s] * wx[k]
                        sy = HIMOTO_SIGMA_Y * ws.cell_size_m if options.lateral else None
                        frac = landing_fraction(fr.sx0[g_] - along, fr.sx1[g_] - along,
                                                fr.sy0[g_] - across, fr.sy1[g_] - across,
                                                mu_w[s], sg_w[s], sy, fr.fill[g_])
                        hit = frac > 0
                        if hit.any():
                            tgts.append(g_[hit])
                            cnts.append(count[hit] * frac[hit])
                            srcs.append(np.full(int(hit.sum()), -1, dtype=np.int64))
                            dists.append(np.hypot(units.x[g_[hit]] - ws.x[s[hit]],
                                                  units.y[g_[hit]] - ws.y[s[hit]]))
                            wild_flags.append(np.ones(int(hit.sum()), dtype=bool))
        if tgts:
            tg = np.concatenate(tgts)
            ct = np.concatenate(cnts)
            sr = np.concatenate(srcs)
            ds = np.concatenate(dists)
            wf = np.concatenate(wild_flags)
            add = np.zeros(n)
            np.add.at(add, tg, ct)
            touched = np.unique(tg)
            before = pool[touched].copy()
            pool[touched] += add[touched]
            need = critical_embers(units.area_m2[touched], float(v_air(u6)))
            over = pool[touched] >= need
            if over.any():
                # top contributor per target (parent, flight distance)
                order = np.lexsort((-ct, tg))
                first = np.ones(len(order), dtype=bool)
                first[1:] = tg[order][1:] != tg[order][:-1]
                top = order[first]
                top_by_target = dict(zip(tg[top].tolist(), top.tolist()))
                for j, b0, need_j in zip(touched[over], before[over], need[over]):
                    frac_step = (need_j - b0) / max(add[j], 1e-12)
                    t_cross = tc + (t_end - tc) * float(np.clip(frac_step, 0.0, 1.0))
                    r = top_by_target[int(j)]
                    toa_min = ds[r] / u6 / 60.0  # flight at the 6.1 m wind (FSJ104686 p.3)
                    ti = t_cross + toa_min + (TAU_SMALL_S + T_LARGE_S) / 60.0
                    committed[j] = True
                    if ti < t[j] and ti <= duration_min:
                        t[j] = ti
                        source[j] = SOURCE_EMBER
                        parent[j] = int(sr[r])
                        from_wild[j] = bool(wf[r])
                        heapq.heappush(heap, (float(ti), int(j)))
        tc = t_end
    return CoupledResult(t_min=t, source=source, parent=parent, ember_from_wildland=from_wild,
                         embers_received=pool)
