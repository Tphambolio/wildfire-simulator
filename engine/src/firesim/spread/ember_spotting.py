"""Long-range ember spot fires in the grid fire model (long-range embers v6, spec §6.8).

**Illustrative — not validated in Canada.** Opt-in; used through ``run_cellular_simulation(...,
spot_hook=LongRangeSpotter(...))``. Until v5 the long-range ember chain only ignited buildings
(``firesim.structures.long_range``); the wildland front never received spot fires from it
(spec §6.3 "Not built"). v6 scores embers on **when fire reaches communities** (target A), so the
same ember budget now also starts spot fires in fuel cells ahead of the front, which then spread
by the level-set rule like any other burning cell.

Chain per spotting slice (``dt_minutes`` of the grid run, 10 min in the v6 harness):

1. **Sources** -- cells the front reached in the slice. Crown fraction burned from the cell's
   crossing rate (ST-X-3 eqs 56-58, as ``cellular._frames``); intensity I = 300 TFC ROS (eq 69).
2. **Generation** -- n_kg (f_s SFC + CFC·[CFB >= 0.1]) A_cell embers per cell: n_kg = 500 /kg
   (Qin 2025 eq 3.7, pp.59-61), f_s = 0.007 (Filkov et al. 2017 Table 1 p.3267 [D], v5 C-value),
   CFC = CFB · CFL (ST-X-3).
3. **Transport** -- v5's two-mode kernel (spec §6.7): a short flaming mode, the Sardoy et al.
   (2008) lognormal (Fig. 10 p.488 fit, 10 m wind as canopy-top wind, p.483) at the slice's
   emission-weighted intensity, truncated at its 99.9th percentile, crosswind σ_Y = 0.92 × cell
   size (Himoto & Tanaka 2008 eq 39); and a long charring mode carrying f_L = 0.2762 of the
   torching/crowning emission (Sardoy 2008 p.486), either Sardoy's own (median 6.3 km, σ_ln 0.18,
   ``long="sardoy"``) or located by Storey et al. (2020) *Fire* 3:10 p.7 (median 733 m, σ_ln 0.99,
   ``long="storey"``). Landing = FFT convolution of the emission grid with each kernel.
4. **Establishment** -- expected spot fires in an unburned fuel cell = η · p_e · (landed short +
   k_L · landed long), p_e = Beverly & Wotton (2007) grass-summer P(sustained flaming) at the
   run's FFMC (AMS preprint eq 2.02 p.5), k_L = v5's long-mode weight (×3 when FFMC >= 96.75,
   Cruz et al. 2012 p.281). η (per-ember establishment efficiency) has no published value: it is
   **fitted** on training fires (v6 harness). One Poisson draw per cell; a cell with >= 1 spot
   ignites at the slice end.

**v7 variable establishment (opt-in, spec §6.8 v7).** ``receptivity`` (a per-cell multiplier on η,
e.g. a fuel-group contrast), ``pe_category`` (a per-cell Beverly & Wotton (2007) fuel category:
``grass_summer`` eq 2.02 or ``pine_lichen`` eq 2.03, AMS preprint p.5) and ``ffmc_at(t)`` (the hourly
FFMC of the slice, Van Wagner 1977 hourly model) make the expected spot fires per cell
η · w(cell) · p_e(category(cell), FFMC(t)) · (landed short + k_L(FFMC(t)) · landed long). With all three
unset the v6 behaviour (one p_e at the run's FFMC, w = 1) is unchanged.

Spot events are logged with the distance to the nearest burned cell at ignition time, the
quantity Storey et al. (2020) measured ("distance to the closest point on the edge of the source
fire", p.6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.signal import fftconvolve
from scipy.special import ndtri

from firesim.structures import long_range as LR
from firesim.structures.embers import sardoy_lognormal

LABEL = "illustrative — not validated in Canada"
ETA_DEFAULT = 1e-6  # [H] placeholder; the v6 harness fits η on training fires
STOREY_LONG = {"long_median_m": LR.V5_STOREY_LONG["long_median_m"],
               "long_sigma": LR.V5_STOREY_LONG["long_sigma"],
               "long_ignition": LR.V5_STOREY_LONG["long_ignition"]}
SARDOY_LONG = {"long_median_m": LR.V5_LONG_MEDIAN_M, "long_sigma": LR.V5_LONG_SIGMA,
               "long_ignition": LR.V5_LONG_IGNITION}


@dataclass
class SpotEvent:
    t_min: float
    row: int
    col: int
    dist_to_fire_m: float  # distance to the nearest cell burned before t (Storey's measure)


@dataclass
class LongRangeSpotter:
    """Spot-hook for ``run_cellular_simulation``: long-range ember spot fires in fuel cells.

    ``wind_at(t) -> (u10_kmh, wind_from_deg)``: the 10 m open wind of the period containing t.
    ``ffmc``: the run's FFMC (p_e and the dry factor). ``seed``: Poisson draws.
    """

    cell_m: float
    wind_at: object
    ffmc: float
    eta: float = ETA_DEFAULT
    long: str = "storey"  # "storey" | "sardoy" | "none"
    n_per_kg: float = LR.N_PER_KG
    f_surface: float = LR.V5_F_SURFACE
    long_fraction: float = LR.V5_LONG_FRACTION
    min_dist_m: float = 0.0  # ignore landings closer than this to the fire (0 = all)
    seed: int = 0
    receptivity: np.ndarray | None = None  # v7: per-cell multiplier on eta (None = 1 everywhere)
    pe_category: np.ndarray | None = None  # v7: per-cell Beverly & Wotton category name (None = grass_summer)
    ffmc_at: object = None  # v7: callable t_min -> FFMC of the slice (None = the run's ffmc)
    events: list = field(default_factory=list)
    emitted: float = 0.0
    landed: np.ndarray | None = None  # cumulative landed embers per m² (unweighted, both modes)
    _rng: np.random.Generator | None = None
    _kcache: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.long not in ("storey", "sardoy", "none"):
            raise ValueError("long must be 'storey', 'sardoy' or 'none'")
        if not self.eta >= 0:
            raise ValueError("eta must be >= 0")
        self._rng = np.random.default_rng(self.seed)
        self.p_e = LR.p_sustained_flaming(self.ffmc, "grass_summer")
        lp = STOREY_LONG if self.long == "storey" else SARDOY_LONG
        self._long = lp
        k = float(lp["long_ignition"])
        if self.ffmc >= LR.V5_DRY_FFMC:
            k *= LR.V5_DRY_FACTOR
        self.k_long = min(k, 1.0)
        if self.receptivity is not None and np.any(np.asarray(self.receptivity) < 0):
            raise ValueError("receptivity must be >= 0")
        if self.pe_category is not None:
            bad = set(np.unique(np.asarray(self.pe_category)).tolist()) - set(LR.BW2007)
            if bad:
                raise ValueError(f"unknown pe_category {sorted(bad)}")

    def _pe_and_klong(self, t_mid: float, shape):
        """p_e (scalar or per-cell grid) and k_L for the slice (v7: hourly FFMC, per-cell category)."""
        if self.ffmc_at is None and self.pe_category is None:
            return self.p_e, self.k_long
        f = float(self.ffmc_at(t_mid)) if self.ffmc_at is not None else self.ffmc
        k = float(self._long["long_ignition"]) * (LR.V5_DRY_FACTOR if f >= LR.V5_DRY_FFMC else 1.0)
        if self.pe_category is None:
            return LR.p_sustained_flaming(f, "grass_summer"), min(k, 1.0)
        cat = np.asarray(self.pe_category)
        pe = np.zeros(shape)
        for name in np.unique(cat):
            pe[cat == name] = LR.p_sustained_flaming(f, str(name))
        return pe, min(k, 1.0)

    # ------------------------------------------------------------------ kernels
    def _short_kernel(self, i_mw: float, u10_ms: float, to_deg: float, shape) -> np.ndarray:
        # kernels are cropped to the grid (exact for mode="same"; LR.kernel ``crop``), normalised by the
        # full kernel's sum as before the crop
        key = ("s", round(math.log(max(i_mw, 1e-3)), 1), round(u10_ms, 0), round(to_deg / 5.0), shape)
        if key not in self._kcache:
            mu, sg = (float(v) for v in sardoy_lognormal(i_mw, u10_ms))
            cap = math.exp(mu + sg * float(ndtri(LR.V5_SHORT_TAIL_Q)))
            k, tot = LR.kernel(mu, sg, 0.92 * self.cell_m, cap, self.cell_m, round(to_deg / 5.0) * 5.0,
                               "cap", subsample=2, crop=(shape[0] - 1, shape[1] - 1), return_total=True)
            self._kcache[key] = k / max(tot, 1e-12) * LR.V5_SHORT_TAIL_Q
        return self._kcache[key]

    def _long_kernel(self, to_deg: float, shape) -> np.ndarray:
        key = ("l", round(to_deg / 5.0), shape)
        if key not in self._kcache:
            mu = math.log(self._long["long_median_m"])
            sg = float(self._long["long_sigma"])
            cap = math.exp(mu + sg * float(ndtri(0.999)))
            sy = max(math.tan(math.radians(LR.V5_LONG_SIGMA_Y_DEG)) * self._long["long_median_m"], self.cell_m)
            self._kcache[key] = LR.kernel(mu, sg, sy, cap, self.cell_m, round(to_deg / 5.0) * 5.0, "cap",
                                          subsample=2, crop=(shape[0] - 1, shape[1] - 1))
        return self._kcache[key]

    # ------------------------------------------------------------------ hook
    def __call__(self, newly, t0: float, t1: float, params, arrival, cross_ros):
        """Cells (rows, cols) to ignite at t1 from embers emitted by ``newly`` in (t0, t1]."""
        if len(newly) == 0 or self.eta <= 0:
            return np.empty((0, 2), dtype=np.int64)
        r, c = newly[:, 0], newly[:, 1]
        ros = cross_ros[r, c]
        rso = params.rso[r, c]
        cfl = params.cfl[r, c]
        cfb = np.where((cfl > 0) & (ros > rso), 1.0 - np.exp(-0.23 * np.maximum(ros - rso, 0.0)), 0.0)
        cfc = cfb * cfl
        sfc = params.sfc[r, c]
        hfi = 300.0 * (sfc + cfc) * ros * params.imult[r, c]
        a = self.cell_m ** 2
        crown = np.where(cfb >= LR.CFB_TORCHING, cfc, 0.0)
        e_long = (self.long_fraction * self.n_per_kg * crown * a) if self.long != "none" else np.zeros_like(crown)
        e_short = self.n_per_kg * (self.f_surface * sfc + crown) * a - e_long
        e_short = np.where(hfi > 0, e_short, 0.0)
        e_long = np.where(hfi > 0, e_long, 0.0)
        tot = float(e_short.sum() + e_long.sum())
        if tot <= 0:
            return np.empty((0, 2), dtype=np.int64)
        self.emitted += tot
        u10_kmh, wfrom = self.wind_at(0.5 * (t0 + t1))
        u10 = max(u10_kmh / 3.6, 0.1)
        to_deg = (wfrom + 180.0) % 360.0
        i_mw = float(np.sum(e_short * hfi) / max(e_short.sum(), 1e-12)) / 1000.0
        shape = arrival.shape
        lam = np.zeros(shape)
        if self.landed is None:
            self.landed = np.zeros(shape)
        if e_short.sum() > 0:
            g = np.zeros(shape)
            np.add.at(g, (r, c), e_short)
            ls = np.maximum(fftconvolve(g, self._short_kernel(i_mw, u10, to_deg, shape), mode="same"), 0.0)
            lam += ls
            self.landed += ls / a
        pe, k_long = self._pe_and_klong(0.5 * (t0 + t1), shape)
        if e_long.sum() > 0:
            g = np.zeros(shape)
            np.add.at(g, (r, c), e_long)
            ll = np.maximum(fftconvolve(g, self._long_kernel(to_deg, shape), mode="same"), 0.0)
            lam += k_long * ll
            self.landed += ll / a
        lam = lam * self.eta * pe
        if self.receptivity is not None:
            lam = lam * np.asarray(self.receptivity)
        cand = params.fuel & ~np.isfinite(arrival) & (lam > 1e-9)
        if not cand.any():
            return np.empty((0, 2), dtype=np.int64)
        idx = np.argwhere(cand)
        hits = self._rng.poisson(lam[cand]) > 0
        out = idx[hits]
        if len(out):
            burned = np.isfinite(arrival) & (arrival <= t1)
            d = ndimage.distance_transform_edt(~burned) * self.cell_m
            keep = d[out[:, 0], out[:, 1]] >= self.min_dist_m
            out = out[keep]
            for rr, cc in out:
                self.events.append(SpotEvent(float(t1), int(rr), int(cc), float(d[rr, cc])))
        return out
