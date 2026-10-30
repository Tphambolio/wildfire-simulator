"""M8 spot-fire likelihood near an observed active edge (opt-in; the frozen v8 ember model).

What this is
    A port, without refit or tuning, of the ember model M8 that passed the pre-registered v8 test
    (``docs/PREREG_LR8.md`` frozen at ``6452c1d`` with addenda A1 ``e0df434``, A2 ``9fe3a0f``, A3
    ``1c67168``, A4 ``1b55caf``; result report [R70] *Long-range embers v8 result 2026-10-29*). On 203
    decision fires never used by FireSim it ranked where new VIIRS spot fires started 1-15 km beyond
    the observed perimeter better than a fuel-hazard x distance x downwind baseline (S3 +0.147,
    95 % +0.098 to +0.194; region AUC 0.71 Canada, 0.64 US).

What it is not
    Not calibrated: expected spot counts were off by x0.4 (Canada) and x6 (US) and the Brier score
    of P(any jump) was worse than climatology, so only RELATIVE likelihood (rank / tiers) is ever
    returned; the raw score never leaves this module. Weaker at longer distances (AUC 0.72 at 1-2 km,
    0.59 at 3-15 km). No better than the untuned ELMFIRE comparator (M8 - ELMFIRE -0.014). The fitted
    bed-wind coefficient is 0 (beta_w at its bound), so it does not respond to wind speed through
    establishment; wind sets only the landing direction and the short-mode distance.

Model (reference implementation ``scripts/validation/v8/intervals.py``: ``run_fire`` loop body,
``m8_score``; sha256 in PREREG_LR8 A2.12; parity test ``engine/tests/spread/test_embers_m8_parity.py``)
    - 125 m model grid, 375 m candidate cells (3 x 3 model cells) whose centres lie 1-15 km from the
      start perimeter, unburned, >= half burnable.
    - Sources: start-perimeter edge cells on the observed active edge. Spread rate per source = distance
      to the previous observed perimeter / the time between them, floor 0.5 m/min; with no previous
      perimeter, the floor (the v8 rule for a fire's first interval).
    - FBP (ST-X-3) SFC at the interval's FFMC/BUI, CFB from the spread rate vs RSO at the type's default
      CBH, TFC = SFC + CFL x CFB, I = 300 TFC ROS; emission 540 embers/kg x TFC x ROS x interval x cell.
    - Long-mode share 0.2762 x plume factor 1-2 (log10 Byram N_c 2 -> 10); short mode Sardoy (2008) at
      I_eff = min(I, 40 MW/m), U_eff = clip(U6.1, 6.70, 17.88 m/s), crosswind sigma 0.92 cell; long mode
      lognormal median 733 m, sigma_ln 0.99, cut 15.6 km, crosswind sigma tan 5 deg x distance.
    - Bed-wind (WAF) fuel groups: 0 unsheltered (O-1a/b; US grass/shrub/urban) WAF 0.4, 1 D/M 0.2,
      2 C/S (US timber) 0.15; grass cells x curing.
    - Score lambda = exp(b0 + b_m (m_ff - 21)) sum_w exp(b_w ln(1 + U10 WAF_w)) (A_short,w + e^b_L A_long,w).

Engine use (``m8_spot_likelihood``): only with an observed perimeter and an observed active edge (RPAS
restart, observed-perimeter run). Engine-side choices, not part of the frozen model: a local
equirectangular 125 m grid around the perimeter, fuel types sampled from the run's fuel grid (cells
off the grid are non-fuel), uniform grass curing from the run, one weather zone (the run's weather
over the first min(duration, 12) h, hourly records averaged as v8 did for a zone), active-edge
sources = perimeter edge cells within one model cell of the active-edge geometry (within 1 km of
hotspot points, the v8 detection rule).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
from scipy import ndimage
from scipy.signal import fftconvolve
from scipy.special import ndtri

# ── frozen coefficients (do not edit; see docs/validation.md "Long-range embers v8") ──────────

#: The tested M8 establishment fit. Source of truth: ``validation-data/v8-pool/fit/fit_beta.json``
#: (outside the repo), sha256 fda5706c400cd74b07b7243c110031d09aa9160445cdcd2996a63ac8d18ec425,
#: written by ``scripts/validation/v8/fit.py`` on the training split only (PREREG_LR8 A2.6,
#: commit 9fe3a0f). Values copied exactly as stored (JSON floats).
M8_BETA = MappingProxyType({
    "b0": -22.670412340012348,
    "b_w": 0.0,
    "b_L": -4.67467954790124,
    "b_m": -0.24413606414846878,
})

M8_MODEL = MappingProxyType({
    "name": "M8",
    "version": "v8-2026-10-29",
    "beta": M8_BETA,
    "fit_beta_json_sha256": "fda5706c400cd74b07b7243c110031d09aa9160445cdcd2996a63ac8d18ec425",
    "fit_commit": "9fe3a0f",
    "prereg": "docs/PREREG_LR8.md",
    "prereg_frozen_commit": "6452c1d",
    "addenda": MappingProxyType({"A1": "e0df434", "A2": "9fe3a0f", "A3": "1c67168", "A4": "1b55caf"}),
    "report": "[R70] Long-range embers v8 result 2026-10-29",
    "reference_impl": "scripts/validation/v8/intervals.py (m8_score; sha256 "
                      "4303e544736968f29f28dd827f662b0f6dfed9178109154ed309d7a5b4724dbe)",
})

#: The approved description (owner, 2026-10-29) and the short caveat shown with it.
APPROVED_SENTENCE = ("ranks likely spot-fire locations near the active edge better than a "
                     "fuel-and-distance baseline")
SHORT_CAVEAT = ("Relative only, not calibrated (no counts or probabilities); weaker at longer "
                "distances; no better than ELMFIRE; does not respond to wind speed (beta_w = 0). "
                "A post-hoc check that restricted Canadian fuel to hazard class left M8's margin intact ([R70] addendum).")

# ── v8 constants (scripts/validation/v8/intervals.py, common.py, v8_gate.py) ─────────────────

CELL_M = 125.0                  # model grid (m)
CAND_F = 3                      # 3 x 125 m = 375 m candidate cells
PAD_M = 16000.0                 # candidate band 15 km + 1 km
WAF = np.array([0.4, 0.2, 0.15], np.float32)
N_PER_KG = 27.0 / 0.05          # 540 embers per kg consumed
LONG_SHARE = 0.2762
LONG_MED, LONG_SIG, LONG_CUT = 733.0, 0.99, 15600.0
ROS_FLOOR = 0.5                 # m/min
ACTIVE_R = 1000.0               # m, detection -> active edge (v8 rule)
BAND = (1000.0, 15000.0)
I_BINS = np.array([0, 500, 2000, 5000, 10000, 20000, 40000, np.inf])  # kW/m short-kernel classes
I_MAX_MW_M = 40.0
U_LO, U_HI = 6.70, 17.88

TYPES = ["", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "D1", "D2", "M1", "M2", "M3", "M4", "O1a", "O1b",
         "S1", "S2", "S3"]
TI = {t: k for k, t in enumerate(TYPES)}

#: Relative-likelihood tiers by percentile rank among the candidate cells (display choice only).
TIER_HIGHER, TIER_MIDDLE = 0.90, 0.70
TIER_NAMES = ("lower", "middle", "higher")


# ── fuels ─────────────────────────────────────────────────────────────────────────────────────

def waf_class(tid: np.ndarray, us_group: np.ndarray | None = None) -> np.ndarray:
    """0 = unsheltered (O, US grass/shrub/urban), 1 = D/M, 2 = C, S, US timber; -1 = non-fuel."""
    cls = np.full(tid.shape, -1, np.int8)
    cls[(tid >= TI["O1a"]) & (tid <= TI["O1b"])] = 0
    cls[(tid >= TI["D1"]) & (tid <= TI["M4"])] = 1
    cls[((tid >= TI["C1"]) & (tid <= TI["C7"])) | (tid >= TI["S1"])] = 2
    if us_group is not None:
        cls[np.isin(us_group, ["G", "S", "U"])] = 0
        cls[us_group == "T"] = 2
    return cls


def tfc_intensity(names, pcs, ros, ffmc, bui, fmc):
    """Vector FBP: TFC (kg/m2) and Byram intensity (kW/m) at the spread rate (m/min), per source."""
    from firesim.fbp.calculator import calculate_sfc
    from firesim.fbp.constants import FUEL_TYPES, FuelType
    tfc = np.zeros(len(names)); inten = np.zeros(len(names))
    for k in np.unique(names):
        if k == 0:
            continue
        t = TYPES[int(k)]
        m = names == k
        spec = FUEL_TYPES[FuelType(t)]
        for pc in np.unique(pcs[m]):
            mm = m & (pcs == pc)
            sfc = calculate_sfc(spec, ffmc, bui, pc=float(pc) if pc > 0 else 50.0)
            cfl, cbh = float(spec.cfl), float(spec.cbh)
            r = ros[mm]
            cfb = np.zeros_like(r)
            if cfl > 0 and cbh > 0 and sfc > 0:
                csi = 0.001 * cbh ** 1.5 * (460.0 + 25.9 * fmc) ** 1.5
                rso = csi / (300.0 * sfc)
                cfb = np.where(r > rso, 1.0 - np.exp(-0.23 * (r - rso)), 0.0)
                if t in ("M1", "M2"):
                    cfb = cfb * pc / 100.0
            tfc[mm] = sfc + cfl * cfb
            inten[mm] = 300.0 * tfc[mm] * r
    return tfc, inten


# ── kernels ───────────────────────────────────────────────────────────────────────────────────

def sardoy_clamped(ib_mw_m, u_ms):
    """Sardoy et al. (2008) lognormal (mu, sigma) inside its computed domain (v8_gate)."""
    from firesim.structures.embers import sardoy_lognormal
    ib = np.minimum(np.asarray(ib_mw_m, float), I_MAX_MW_M)
    u = np.clip(np.asarray(u_ms, float), U_LO, U_HI)
    return sardoy_lognormal(ib, u)


def _rot(n, cell, to_rad):
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1].astype(np.float64) * cell
    yy = -yy  # row index grows southward
    along = xx * math.sin(to_rad) + yy * math.cos(to_rad)
    cross = xx * math.cos(to_rad) - yy * math.sin(to_rad)
    return along, cross


def long_kernel(cell, to_rad):
    n = int(LONG_CUT // cell) + 1
    a, c = _rot(n, cell, to_rad)
    ap = np.maximum(a, 1e-6)
    f = np.exp(-(np.log(ap / LONG_MED) ** 2) / (2 * LONG_SIG ** 2)) / (ap * LONG_SIG * math.sqrt(2 * math.pi))
    sy = np.maximum(math.tan(math.radians(5.0)) * ap, cell / 2)
    f = f * np.exp(-c ** 2 / (2 * sy ** 2)) / (math.sqrt(2 * math.pi) * sy) * cell * cell
    f[(a <= 0) | (np.hypot(a, c) > LONG_CUT)] = 0.0
    return (f / f.sum() * 0.999).astype(np.float32)  # renormalised to the in-cut share


def short_kernel(cell, to_rad, i_kw, u10):
    from firesim.structures.embers import u10_to_u6
    i_kw = float(i_kw) if np.isfinite(i_kw) else 1.0
    u10 = float(u10) if np.isfinite(u10) else 0.0
    mu, sg = sardoy_clamped(max(i_kw, 1.0) / 1000.0, float(u10_to_u6(u10)))
    mu, sg = float(mu), float(sg)
    reach = math.exp(mu + sg * float(ndtri(0.999)))
    n = max(2, int(reach // cell) + 2)
    a, c = _rot(n, cell, to_rad)
    ap = np.maximum(a, 1e-6)
    f = np.exp(-(np.log(ap) - mu) ** 2 / (2 * sg ** 2)) / (ap * sg * math.sqrt(2 * math.pi))
    sy = 0.92 * cell
    f = f * np.exp(-c ** 2 / (2 * sy ** 2)) / (math.sqrt(2 * math.pi) * sy) * cell * cell
    f[(a <= 0) | (a > reach)] = 0.0
    s = f.sum()
    return (f / s * 0.999).astype(np.float32) if s > 0 else f.astype(np.float32)


# ── weather ───────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ZoneWeather:
    """Interval weather of one zone: mean 10 m wind speed (m/s), vector-mean direction (FROM, deg),
    mean FFMC, BUI at the interval start; ``m_ff`` (FF-scale moisture, %) from the FFMC."""

    u10: float
    wd_from: float
    ffmc: float
    bui: float

    @property
    def to_rad(self) -> float:
        return math.radians((self.wd_from + 180) % 360)

    @property
    def m_ff(self) -> float:
        return 147.2 * (101 - self.ffmc) / (59.5 + self.ffmc)


def zone_weather_from_hours(wind_kmh, wind_from_deg, ffmc, bui: float) -> ZoneWeather:
    """v8 ``zone_weather`` over hourly records: scalar-mean speed, vector-mean direction, mean FFMC."""
    r = np.radians(np.nan_to_num(np.asarray(wind_from_deg, float)))
    u = np.asarray(wind_kmh, float) / 3.6
    ue, un = np.nanmean(u * np.sin(r)), np.nanmean(u * np.cos(r))
    ue, un = float(np.nan_to_num(ue)), float(np.nan_to_num(un))
    frm = math.degrees(math.atan2(ue, un)) % 360
    spd = float(np.nan_to_num(np.nanmean(u))) if len(u) else 0.0
    return ZoneWeather(u10=spd, wd_from=frm, ffmc=float(np.nanmean(np.asarray(ffmc, float))),
                       bui=float(bui))


# ── one interval ──────────────────────────────────────────────────────────────────────────────

def source_ros(act: np.ndarray, prev_closed: np.ndarray | None, prev_dt_min: float | None,
               cell: float = CELL_M) -> np.ndarray:
    """Spread rate (m/min) at each active cell (``np.nonzero(act)`` order): distance to the previous
    perimeter over the time between perimeters (>= 60 min), floor 0.5; the floor without one."""
    aj, ai = np.nonzero(act)
    if prev_closed is None or prev_dt_min is None:
        return np.full(len(aj), ROS_FLOOR)
    dprev = ndimage.distance_transform_edt(~prev_closed)[aj, ai] * cell
    return np.maximum(dprev / max(float(prev_dt_min), 60.0), ROS_FLOOR)


def landing_field(act: np.ndarray, ros: np.ndarray, names: np.ndarray, pcs: np.ndarray,
                  wcls: np.ndarray, gcur: np.ndarray, zone: np.ndarray | None,
                  zones: list[ZoneWeather], hours: float, fmc: float,
                  cell: float = CELL_M, with_reach: bool = False):
    """M8 landing sufficient statistics A[mode (short, long), WAF class, row, col] (embers landed x
    grass curing) and the total emission, for one interval (the v8 ``run_fire`` loop body).

    ``ros`` is per active cell in ``np.nonzero(act)`` order; ``gcur`` is curing on grass cells and
    1 elsewhere; ``zone`` (int grid) is needed only with more than one zone. ``with_reach`` also
    returns the cells inside some kernel's support from some source (exact, float64 indicator
    convolution; for display only: the float32 landing field carries FFT round-off everywhere).
    """
    ny, nx = act.shape
    A = np.zeros((2, 3, ny, nx), np.float32)
    aj, ai = np.nonzero(act)
    ros_all = np.asarray(ros, float)
    emis_tot = 0.0
    reach = np.zeros((ny, nx), bool) if with_reach else None

    def _reach(src, kern):
        if reach is not None:
            reach[...] |= fftconvolve((src > 0).astype(np.float64), (kern > 0).astype(np.float64),
                                      mode="same") > 0.5
    for zi, zw in enumerate(zones):
        sel = zone[aj, ai] == zi if len(zones) > 1 else np.ones(len(aj), bool)
        if not sel.any():
            continue
        sj, si, r = aj[sel], ai[sel], ros_all[sel]
        nm = names[sj, si]; ok = nm != 0
        sj, si, r, nm, pc = sj[ok], si[ok], r[ok], nm[ok], pcs[sj[ok], si[ok]]
        if not len(sj):
            continue
        tfc, inten = tfc_intensity(nm, pc, r, zw.ffmc, zw.bui, fmc)
        tfc, inten = np.nan_to_num(tfc), np.nan_to_num(inten)
        n_emb = N_PER_KG * tfc * (r / 60.0) * (hours * 3600.0) * cell
        emis_tot += float(n_emb.sum())
        # Byram convection number -> long-mode share
        ur = max(zw.u10 - 0.0, 0.5)
        nc = 2 * 9.81 * inten * 1000.0 / (1.2 * 1005.0 * 300.0 * np.maximum(ur - r / 60.0, 0.5) ** 3)
        phi = 1.0 + np.clip((np.log10(np.maximum(nc, 1e-9)) - math.log10(2))
                            / (math.log10(10) - math.log10(2)), 0, 1)
        fl = np.minimum(LONG_SHARE * phi, 0.95)
        src_long = np.zeros((ny, nx), np.float32)
        np.add.at(src_long, (sj, si), (n_emb * fl).astype(np.float32))
        lk = long_kernel(cell, zw.to_rad)
        land_long = fftconvolve(src_long, lk, mode="same")
        _reach(src_long, lk)
        land_short = np.zeros((ny, nx), np.float32)
        cls = np.digitize(inten, I_BINS) - 1
        for k in np.unique(cls):
            m = cls == k
            src = np.zeros((ny, nx), np.float32)
            np.add.at(src, (sj[m], si[m]), (n_emb[m] * (1 - fl[m])).astype(np.float32))
            i_rep = float(np.median(inten[m]))
            sk = short_kernel(cell, zw.to_rad, i_rep, zw.u10)
            land_short += fftconvolve(src, sk, mode="same")
            _reach(src, sk)
        land_long = np.maximum(land_long, 0); land_short = np.maximum(land_short, 0)
        for w in range(3):
            mw = (wcls == w)
            A[0, w] += np.where(mw, land_short * gcur, 0)
            A[1, w] += np.where(mw, land_long * gcur, 0)
    if with_reach:
        return A, emis_tot, reach & (wcls >= 0) & (gcur > 0)
    return A, emis_tot


def candidate_blocks(closed: np.ndarray, burnable: np.ndarray, cell: float = CELL_M,
                     f: int = CAND_F):
    """375 m candidate cells: (bj, bi, centre distance to the start perimeter in m). Centres 1-15 km
    from the perimeter, no start-perimeter cell inside, >= half burnable (v8)."""
    ny, nx = closed.shape
    bny, bnx = ny // f, nx // f
    dist_all = ndimage.distance_transform_edt(~closed) * cell
    burn_blk = burnable[:bny * f, :bnx * f].reshape(bny, f, bnx, f).mean(axis=(1, 3)) >= 0.5
    cy, cx = np.arange(bny) * f + 1, np.arange(bnx) * f + 1
    dcen = dist_all[np.ix_(cy, cx)]
    incl = closed[:bny * f, :bnx * f].reshape(bny, f, bnx, f).any(axis=(1, 3))
    cand = (dcen >= BAND[0]) & (dcen <= BAND[1]) & ~incl & burn_blk
    bj, bi = np.nonzero(cand)
    return bj, bi, dcen[bj, bi]


def block_sums(A: np.ndarray, bj: np.ndarray, bi: np.ndarray, f: int = CAND_F) -> np.ndarray:
    """A summed over each 375 m candidate cell: shape (2, 3, n_candidates)."""
    ny, nx = A.shape[-2:]
    bny, bnx = ny // f, nx // f
    return A[:, :, :bny * f, :bnx * f].reshape(2, 3, bny, f, bnx, f).sum(axis=(3, 5))[:, :, bj, bi]


def m8_score(A: np.ndarray, u10: np.ndarray, m_ff: np.ndarray, zone: np.ndarray,
             beta=M8_BETA) -> np.ndarray:
    """Expected established spots per candidate cell (internal; never displayed: not calibrated).

    ``A`` (2, 3, n), ``u10``/``m_ff`` per zone, ``zone`` per candidate (v8 ``m8_score``)."""
    A = np.asarray(A).astype(np.float64)
    u = np.asarray(u10)[zone].astype(np.float64)
    m = np.asarray(m_ff)[zone].astype(np.float64)
    lam = np.zeros(A.shape[-1])
    for w in range(3):
        lam += np.exp(beta["b_w"] * np.log1p(u * WAF[w])) * (A[0, w] + math.exp(beta["b_L"]) * A[1, w])
    return lam * np.exp(beta["b0"] + beta["b_m"] * (m - 21.0))


def pct_rank(score: np.ndarray) -> np.ndarray:
    """Percentile rank of each candidate among all candidates, ties counted half (Mann-Whitney)."""
    s = np.asarray(score, float)
    n = len(s)
    if n == 0:
        return s
    order = np.sort(s)
    below = np.searchsorted(order, s, side="left")
    equal = np.searchsorted(order, s, side="right") - below
    return (below + 0.5 * (equal - 1)) / max(n - 1, 1)


def tiers(score: np.ndarray, reached: np.ndarray | None = None) -> np.ndarray:
    """0 lower / 1 middle / 2 higher relative likelihood: percentile rank among all candidate cells
    >= 0.90 higher, >= 0.70 middle. A cell no ember reaches (``reached`` False, or a zero score) is
    always lower: outside every kernel's support the float32 landing field holds only FFT round-off
    (up to ~1e-4 of the highest cell), not a landing. Display rule only: the ranks are v8's."""
    s = np.asarray(score, float)
    r = pct_rank(s)
    t = np.where(r >= TIER_HIGHER, 2, np.where(r >= TIER_MIDDLE, 1, 0)).astype(np.int8)
    t[s <= 0] = 0
    if reached is not None:
        t[~np.asarray(reached, bool)] = 0
    return t


# ── engine use: observed perimeter + observed active edge ─────────────────────────────────────

class LocalGrid:
    """A north-up 125 m grid on a local equirectangular projection centred on (lat0, lng0)."""

    def __init__(self, lat0: float, lng0: float, x0: float, y1: float, ny: int, nx: int,
                 cell: float = CELL_M):
        self.lat0, self.lng0, self.x0, self.y1, self.ny, self.nx, self.cell = lat0, lng0, x0, y1, ny, nx, cell
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110574.0

    def to_xy(self, lng, lat):
        return (np.asarray(lng, float) - self.lng0) * self.kx, (np.asarray(lat, float) - self.lat0) * self.ky

    def to_lnglat(self, x, y):
        return np.asarray(x, float) / self.kx + self.lng0, np.asarray(y, float) / self.ky + self.lat0

    def centres(self):
        jj, ii = np.mgrid[0:self.ny, 0:self.nx]
        x = self.x0 + (ii + 0.5) * self.cell
        y = self.y1 - (jj + 0.5) * self.cell
        return self.to_lnglat(x, y)


def _project(geom, grid: LocalGrid):
    import shapely
    return shapely.transform(geom, lambda c: np.column_stack(grid.to_xy(c[:, 0], c[:, 1])))


def _rasterize(geom_xy, grid: LocalGrid, all_touched: bool) -> np.ndarray:
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    tr = Affine(grid.cell, 0.0, grid.x0, 0.0, -grid.cell, grid.y1)
    if geom_xy.is_empty:
        return np.zeros((grid.ny, grid.nx), bool)
    return rasterize([(geom_xy, 1)], out_shape=(grid.ny, grid.nx), transform=tr, fill=0,
                     all_touched=all_touched, dtype="uint8").astype(bool)


def m8_spot_likelihood(perimeter_geojson: dict, active_edges: dict, fuel_grid, zone: ZoneWeather,
                       hours: float, fmc: float, grass_cure_pct: float, percent_conifer: float,
                       percent_dead_fir: float, previous_perimeter_geojson: dict | None = None,
                       previous_dt_min: float | None = None, max_cells: int = 4_000_000) -> dict:
    """Relative spot-fire likelihood (tiers) on 375 m cells 1-15 km beyond an observed perimeter.

    Needs an observed active edge (``active_edges``: GeoJSON geometry, lng/lat). Returns a dict with
    ``tiers`` (GeoJSON FeatureCollection of the higher and middle cells, property ``tier``), counts per
    tier, the sources and the caveats. Never returns the raw score, a count or a probability.
    """
    from shapely.geometry import shape

    from firesim.fbp.constants import FuelType

    per = shape(perimeter_geojson)
    if not per.is_valid:
        per = per.buffer(0)
    edges = shape(active_edges)
    lng0, lat0 = per.centroid.x, per.centroid.y
    g0 = LocalGrid(lat0, lng0, 0.0, 0.0, 1, 1)
    per_xy = _project(per, g0)
    minx, miny, maxx, maxy = per_xy.bounds
    step = CELL_M * CAND_F
    x0 = math.floor((minx - PAD_M) / step) * step
    y1 = math.ceil((maxy + PAD_M) / step) * step
    nx = int(math.ceil((maxx + PAD_M - x0) / step)) * CAND_F
    ny = int(math.ceil((y1 - (miny - PAD_M)) / step)) * CAND_F
    if nx * ny > max_cells:
        raise ValueError("observed perimeter too large for the M8 ember layer "
                         f"({ny} x {nx} cells of {CELL_M:g} m)")
    grid = LocalGrid(lat0, lng0, x0, y1, ny, nx)
    per_xy = _project(per, grid)
    closed = _rasterize(per_xy, grid, all_touched=False) | _rasterize(per_xy.boundary, grid, True)
    edge = closed & ~ndimage.binary_erosion(closed)
    edges_xy = _project(edges, grid)
    pts = edges.geom_type in ("Point", "MultiPoint")
    touched = _rasterize(edges_xy if not pts else edges_xy.buffer(1.0), grid, all_touched=True)
    reach = ACTIVE_R if pts else CELL_M
    act = edge & (ndimage.distance_transform_edt(~touched) * CELL_M <= reach) if touched.any() \
        else np.zeros_like(edge)
    # fuels sampled from the run's grid at the 125 m cell centres (off the grid: non-fuel)
    lng, lat = grid.centres()
    names = np.zeros((ny, nx), np.int8)
    pcs = np.zeros((ny, nx), np.float32)
    inside = ((lat >= fuel_grid.lat_min) & (lat <= fuel_grid.lat_max)
              & (lng >= fuel_grid.lng_min) & (lng <= fuel_grid.lng_max))
    rr = np.clip(((fuel_grid.lat_max - lat) / (fuel_grid.lat_max - fuel_grid.lat_min)
                  * fuel_grid.rows).astype(int), 0, fuel_grid.rows - 1)
    cc = np.clip(((lng - fuel_grid.lng_min) / (fuel_grid.lng_max - fuel_grid.lng_min)
                  * fuel_grid.cols).astype(int), 0, fuel_grid.cols - 1)
    code_of = {ft: TI.get(ft.value.replace("-", ""), 0) for ft in FuelType}
    pc_layer = fuel_grid.pc
    for j, i in zip(*np.nonzero(inside)):
        r, c = rr[j, i], cc[j, i]
        ft = fuel_grid.fuel_types[r][c]
        if ft is None:
            continue
        k = code_of.get(ft, 0)
        names[j, i] = k
        if TYPES[k] in ("M1", "M2"):
            v = pc_layer[r][c] if pc_layer is not None else None
            pcs[j, i] = percent_conifer if v is None else v
        elif TYPES[k] in ("M3", "M4"):
            pcs[j, i] = percent_dead_fir
    wcls = waf_class(names)
    gcur = np.where(wcls == 0, grass_cure_pct / 100.0, 1.0).astype(np.float32)
    prev = None
    if previous_perimeter_geojson is not None and previous_dt_min is not None:
        pg = shape(previous_perimeter_geojson)
        pxy = _project(pg if pg.is_valid else pg.buffer(0), grid)
        prev = _rasterize(pxy, grid, False) | _rasterize(pxy.boundary, grid, True)
    ros = source_ros(act, prev, previous_dt_min)
    A, emission, reach = landing_field(act, ros, names, pcs, wcls, gcur, None, [zone], hours, fmc,
                                       with_reach=True)
    burnable = names != 0
    bj, bi, dist = candidate_blocks(closed, burnable)
    score = m8_score(block_sums(A, bj, bi), np.array([zone.u10]), np.array([zone.m_ff]),
                     np.zeros(len(bj), int))
    f = CAND_F
    reached = reach[:ny // f * f, :nx // f * f].reshape(ny // f, f, nx // f, f).any(axis=(1, 3))[bj, bi]
    t = tiers(score, reached)
    feats = []
    for k in np.nonzero(t > 0)[0]:
        xa = x0 + bi[k] * step
        yb = y1 - bj[k] * step
        lo, la = grid.to_lnglat(np.array([xa, xa + step]), np.array([yb - step, yb]))
        lo, la = [round(float(v), 6) for v in lo], [round(float(v), 6) for v in la]
        feats.append({"type": "Feature", "properties": {"tier": TIER_NAMES[t[k]],
                                                         "distance_km": round(float(dist[k]) / 1000, 1)},
                      "geometry": {"type": "Polygon", "coordinates": [[
                          [lo[0], la[0]], [lo[1], la[0]], [lo[1], la[1]], [lo[0], la[1]], [lo[0], la[0]]]]}})
    on_grid = float(inside.mean())
    return {
        "model": "M8", "model_version": M8_MODEL["version"], "report": "R70",
        "description": APPROVED_SENTENCE, "caveat": SHORT_CAVEAT, "display": "relative tiers only",
        "tiers": {"type": "FeatureCollection", "features": feats},
        "n_candidate_cells": int(len(bj)),
        "n_by_tier": {name: int((t == i).sum()) for i, name in enumerate(TIER_NAMES)},
        "tier_rule": f"higher = top {round((1 - TIER_HIGHER) * 100)} of every 100 candidate cells by "
                     f"rank, middle = next {round((TIER_HIGHER - TIER_MIDDLE) * 100)}, lower = rest "
                     "(and any cell no ember reaches)",
        "cell_m": CELL_M * CAND_F, "band_km": [BAND[0] / 1000, BAND[1] / 1000],
        "active_source_cells": int(act.sum()), "any_landing": bool(reached.any()),
        "hours": float(hours), "spread_rate": "observed from the previous perimeter"
        if prev is not None else "v8 floor 0.5 m/min (no previous observed perimeter)",
        "fuel_grid_coverage": round(on_grid, 3),
        "wind_from_deg": round(zone.wd_from, 1), "wind_10m_kmh": round(zone.u10 * 3.6, 1),
    }


def zone_from_schedule(schedule, hours: float) -> ZoneWeather:
    """One zone's interval weather from a run's weather schedule ((start minute, SpreadConditions),
    ``Simulator.weather_schedule``): the conditions in force at each whole hour 0..hours (both ends,
    as v8 took the hourly records from t0 to t1), averaged as v8 ``zone_weather``; BUI at the start."""
    from firesim.fbp.calculator import calculate_bui

    sched = sorted(schedule, key=lambda e: e[0])

    def at(minute):
        cur = sched[0][1]
        for start, cond in sched:
            if start <= minute + 1e-9:
                cur = cond
        return cur

    conds = [at(h * 60.0) for h in range(int(math.floor(hours)) + 1)]
    c0 = conds[0]
    return zone_weather_from_hours([c.wind_speed for c in conds], [c.wind_direction for c in conds],
                                   [c.ffmc for c in conds], calculate_bui(c0.dmc, c0.dc))


def m8_for_simulator(sim, perimeter_geojson: dict, active_edges: dict,
                     previous_perimeter_geojson: dict | None = None,
                     previous_dt_min: float | None = None) -> dict:
    """``m8_spot_likelihood`` for a restart ``Simulator`` (fuel grid, weather, fuel modifiers) over the
    first min(duration, 12) h; 12 h is the longest interval v8 scored (PREREG_LR8 §4)."""
    if sim.fuel_grid is None:
        raise ValueError("the M8 ember layer needs a fuel grid")
    hours = min(float(sim.config.duration_hours), 12.0)
    base = sim._spread_conditions()
    zone = zone_from_schedule(sim.weather_schedule(), hours)
    out = m8_spot_likelihood(perimeter_geojson, active_edges, sim.fuel_grid, zone, hours, base.fmc,
                             base.grass_cure, base.pc, base.pdf, previous_perimeter_geojson,
                             previous_dt_min)
    return out
