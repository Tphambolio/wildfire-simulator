"""Long-range ember (firebrand) ignition of buildings from the wildland fire (opt-in).

Specification: ``docs/structure-spread-spec.md`` §6.3. **Illustrative — not validated in
Canada.** Built from the literature review *Long-range ember ignition literature 2026-10-11*
(PROJECT_RECORD [R23]); papers only, no ELMFIRE source opened. Labels: [V] value read in the
paper, [D] derived from verified values, [H] FireSim hypothesis with no published value
(fixed in advance, varied in pre-registered sensitivity runs, never tuned on a scored fire).

Chain (one expected-value landing field, then probabilistic ignition):

G. **Generation.** Ember sources are torching or crowning grid cells (CFB >= 0.1; spec M4).
   A source cell emits ``n_kg · (f_s · SFC + CFC) · A_cell`` embers, n_kg = 500 per kg of fuel
   consumed (Qin 2025 PhD eq 3.7 with yield 0.10 and m_b = 0.2 g, pp.59-61 [V]; Thompson et
   al. 2022 Fire Technol. 58:1630 measured ≈640 /kg in an Alberta crown fire [V], the
   sensitivity value), f_s = 0 for surface fuel [H] (NOR-X-433 p.34: surface-fire production is
   much lower). Emission is spread evenly over the cell's burning time (Qin 2025 eq 3.2 [V]).
T. **Transport.** Sardoy et al. (2008) lognormal downwind PDF (as printed in Qin 2025 eqs
   4.2-4.6, pp.72-73 [V]; ``embers.sardoy_lognormal``, 6.1 m wind, spec C18), times a normal
   crosswind PDF with σ_Y = 0.92 × cell size (Himoto & Tanaka 2008 eq 39, as [R12]). **Not
   truncated at the 99th percentile** (Qin's truncation removes the 250 m-3 km band); instead
   the embers beyond a maximum spotting distance are dropped [H]: Albini, Alexander & Cruz
   (2012) Table 1 (1.0 mm final firebrand diameter, the table's own assumption) for crowning
   cells (CFB >= 0.9), with the free flame height from the paper's flame-structure model
   (eq 18, A = 2.65 m³/MJ) and the canopy-top wind U_c = U10 / 1.5 (Table 1 caption);
   Albini (1979) torching trees (``albini.max_spot_distance``) for torching cells. The field
   is computed on a raster by FFT convolution of the emitted counts, binned by time, wind
   period and source class (kind × intensity × fuel), with each class's kernel.
I. **Ignition (probabilistic, per building).** Each unit's receptive area A_r is its
   footprint grown by a 1.5 m ring (FireSmart 0-1.5 m zone) [H]. Over time bin b it receives
   Λ_b = λ_b · A_r embers (λ = landed embers per m² at its centroid); each ember ignites the
   receptive near-home fuel with probability p_e · k_g and that ignition becomes a building
   fire with probability p_b, so the hazard is h_b = Λ_b p_e k_g p_b and
   P(ignited by t) = 1 − exp(−Σ h). p_e: Beverly & Wotton (2007) logistic P(sustained flaming)
   from a match flame, grass-summer: logit = −23.3755 + 0.2895 DFFMC (AMS preprint eq 2.02,
   p.5 [V preprint]); k_g = 0.5 for glowing brands [H] (Ganteaume et al. 2009: flaming brands
   >= 2× as effective [A]); p_b = 0.1 [H] (no published value: the most influential unknown).
   A combustible-roof scenario unit (``roofs``) has p_b × 1/0.375 [H] (the DeBeer ratio of
   the roof scenario, applied to the per-ember probability; capped so a per-ember probability
   stays <= 1).
D. **Delays and structure stage.** An ignited unit becomes involved 42 s + 300 s later (Qin
   et al. 2026 FSJ 162:104686 p.4 [V]; flight time neglected [H]) and then burns its design
   fire in the existing Hamada / burn-out / vegetation-bridge structure stage.
E. **Ensemble.** M members (default 20 in the API; 50 offline, Purnomo et al. 2024 p.3: 50 ≈
   100 [V]) each draw every unit's first ignition time from the inhomogeneous Poisson process
   above and run the structure stage; P(involved by T) = share of members. Not deterministic
   by design: the output is a probability (seeded, so repeatable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.special import ndtr, ndtri

from firesim.structures.embers import HIMOTO_SIGMA_Y, sardoy_lognormal, u10_to_u6

LABEL = "illustrative — not validated in Canada"

N_PER_KG = 500.0  # Qin 2025 eq 3.7 (y 0.10 / m_b 0.2 g), pp.59-61 [V]
N_PER_KG_THOMPSON = 640.0  # Thompson et al. 2022 p.1630, Alberta crown fire [V]
N_PER_KG_LOW = 250.0  # Qin 2025 p.59 lower yield (5 %) [V]
F_SURFACE = 0.0  # surface-fuel share of generation [H] (NOR-X-433 p.34, qualitative)
CFB_TORCHING = 0.1  # Van Wagner / FBP: torching from CFB 0.1 (spec M4)
CFB_CROWNING = 0.9  # FBP active crown fire (ST-X-3: CFB > 0.9)
RING_M = 1.5  # receptive ring around the footprint (FireSmart 0-1.5 m) [H]
K_GLOWING = 0.5  # glowing vs match-flame factor [H] (Ganteaume 2009 [A])
P_BUILDING = 0.1  # near-home fuel ignition -> building fire [H]; no published value
ROOF_FACTOR = 1.0 / 0.375  # combustible-roof scenario on p_b [H] (DeBeer ratio, roofs.py)
DELAY_S = 42.0 + 300.0  # t_ign,small + t_ign,large (FSJ104686 p.4) [V]
DEFAULT_MEMBERS = 20  # API default (offline validation: 50)
MAX_MEMBERS = 50
DEFAULT_BIN_MIN = 30.0  # time bins of the landing field [H]

# Albini, Alexander & Cruz (2012) Table 1, p.L: maximum spot distance (km) for a 1.0 mm
# firebrand on alighting; rows (canopy-top height Hc, free flame height Hf = Hc, 2 Hc),
# columns canopy-top wind U_c (m/s) [V].
A2012_UC = np.array([2.8, 5.6, 8.3, 11.1, 13.9, 16.7])
A2012_HC = np.array([5.0, 10.0, 15.0, 20.0, 25.0])
A2012_TABLE = np.array([
    [[0.23, 0.30, 0.37, 0.45, 0.53, 0.61], [0.61, 0.78, 0.97, 1.2, 1.4, 1.6]],
    [[0.52, 0.64, 0.78, 0.92, 1.1, 1.2], [1.2, 1.5, 1.8, 2.2, 2.5, 2.8]],
    [[0.78, 0.98, 1.2, 1.3, 1.5, 1.7], [1.7, 2.2, 2.6, 3.0, 3.5, 3.9]],
    [[1.1, 1.3, 1.5, 1.7, 1.9, 2.2], [2.1, 2.9, 3.4, 3.9, 4.4, 4.9]],
    [[1.2, 1.6, 1.8, 2.1, 2.4, 2.6], [2.5, 3.5, 4.1, 4.6, 5.2, 5.8]],
])  # shape (Hc, Hf/Hc in {1, 2}, U_c)
A2012_FLAME_A = 2.65  # m³/MJ, empirical crown-fire flame structure constant (p.E) [V]
A2012_UC_FROM_U10 = 1.0 / 1.5  # Table 1 caption: U10 open = 1.5 × U_c [V]

# Beverly & Wotton 2007 (AMS preprint p.5) P(sustained flaming) logistic models [V preprint]
BW2007 = {
    "grass_summer": lambda dffmc, dmc: -23.3755 + 0.2895 * dffmc,  # eq 2.02
    "pine_lichen": lambda dffmc, dmc: -34.8731 + 0.4304 * dffmc,  # eq 2.03
    "mixedwood_moss": lambda dffmc, dmc: -24.3837 + 0.2407 * dffmc + 0.0638 * dmc,  # eq 2.06
    "spruce_moss": lambda dffmc, dmc: -38.9 + 0.4117 * dffmc + 0.0679 * dmc,  # eq 2.08
}


# ----------------------------------------------------------------------------- physics


def p_sustained_flaming(dffmc: float, category: str = "grass_summer", dmc: float = 0.0) -> float:
    """Beverly & Wotton (2007) P(sustained flaming) of ground fuel from a match flame."""
    z = BW2007[category](float(dffmc), float(dmc))
    return float(1.0 / (1.0 + math.exp(-z)))


def free_flame_height(hfi_kw_m: float, uc_ms: float, hc_m: float) -> float:
    """Free flame height above the canopy top (m) from Albini et al. 2012 eq 18:
    A · I = 0.34 U_c H_c (1 + a) ln(1 + a), a = 2.94 H_f / H_c (I in MW/m, A = 2.65 m³/MJ)."""
    rhs = A2012_FLAME_A * hfi_kw_m / 1000.0 / max(0.34 * uc_ms * hc_m, 1e-9)
    lo, hi = 0.0, 1e4
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if (1.0 + mid) * math.log1p(mid) < rhs:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi) * hc_m / 2.94


def albini2012_max_distance(hfi_kw_m: float, u10_ms: float, hc_m: float) -> float:
    """Maximum crown-fire spotting distance (m), Albini, Alexander & Cruz 2012 Table 1 (1 mm
    final firebrand) interpolated in (H_c, H_f/H_c, U_c); inputs outside the table are held
    at its edges [H] (H_c 5-25 m, H_f/H_c 1-2, U_c 2.8-16.7 m/s)."""
    uc = u10_ms * A2012_UC_FROM_U10
    hf = free_flame_height(hfi_kw_m, max(uc, 0.1), max(hc_m, 1.0))
    hc = float(np.clip(hc_m, A2012_HC[0], A2012_HC[-1]))
    r = float(np.clip(hf / max(hc_m, 1.0), 1.0, 2.0))
    u = float(np.clip(uc, A2012_UC[0], A2012_UC[-1]))
    by_hc = np.array([[np.interp(u, A2012_UC, A2012_TABLE[i, j]) for j in range(2)]
                      for i in range(len(A2012_HC))])
    by_r = by_hc[:, 0] + (r - 1.0) * (by_hc[:, 1] - by_hc[:, 0])
    return float(np.interp(hc, A2012_HC, by_r)) * 1000.0


def cap_distance(kind: str, hfi_kw_m: float, cfb: float, u10_ms: float, fuel: str) -> float:
    """Maximum spotting distance (m) for a source class: Albini 2012 for crowning cells,
    Albini 1979 torching trees / Albini 1983 surface fire (``albini.max_spot_distance``)."""
    from firesim.spread.albini import STAND_DEFAULTS, StandDefaults, max_spot_distance

    stand = STAND_DEFAULTS.get(fuel, StandDefaults(1.0))
    if kind == "crown":
        hc = stand.tree_height if stand.tree_height > 0 else stand.cover_height
        return albini2012_max_distance(hfi_kw_m, u10_ms, hc)
    return max_spot_distance(fuel, hfi_kw_m, cfb, u10_ms * 3.6)


@dataclass(frozen=True)
class LongRangeOptions:
    """Long-range ember settings (spec §6.3). Defaults are the primary [V]/[H] values."""

    n_per_kg: float = N_PER_KG
    f_surface: float = F_SURFACE
    k_glowing: float = K_GLOWING
    p_building: float = P_BUILDING
    ring_m: float = RING_M
    fuel_category: str = "grass_summer"
    dffmc: float | None = None  # None: the run's FFMC
    tail: str = "cap"  # "cap" (primary), "q99" (Qin truncation, T1), "x2" (Thurston, T2)
    sigma_y_factor: float = 1.0
    members: int = DEFAULT_MEMBERS
    bin_min: float = DEFAULT_BIN_MIN
    roof_factor: float = ROOF_FACTOR
    delay_s: float = DELAY_S
    raster_m: float | None = None  # None: the source cell size
    seed: int = 0

    def __post_init__(self):
        if self.tail not in ("cap", "q99", "x2"):
            raise ValueError("tail must be 'cap', 'q99' or 'x2'")
        if not 1 <= int(self.members) <= MAX_MEMBERS:
            raise ValueError(f"members must be 1-{MAX_MEMBERS}")

    def params(self) -> dict:
        return {"long_range_embers": True, "long_range_members": int(self.members),
                "long_range_embers_per_kg": float(self.n_per_kg),
                "long_range_p_building": float(self.p_building),
                "long_range_glowing_factor": float(self.k_glowing),
                "long_range_tail": self.tail}


@dataclass
class EmberSourceCells:
    """Burning grid cells as long-range ember sources, in a local metric frame (metres)."""

    x: np.ndarray
    y: np.ndarray
    start_min: np.ndarray
    end_min: np.ndarray
    hfi_kw_m: np.ndarray
    sfc: np.ndarray  # surface fuel consumed (kg/m²)
    cfc: np.ndarray  # crown fuel consumed (kg/m²)
    cfb: np.ndarray
    fuel: np.ndarray  # FBP fuel code per cell (str)
    cell_size_m: float

    @classmethod
    def from_emitters(cls, emitters, frame) -> "EmberSourceCells | None":
        """From the grid run's ``Emitters`` (needs ``intensity_kw_m``, ``cfb``, ``sfc``,
        ``cfc``; ``fuel`` optional, default C2)."""
        need = ("intensity_kw_m", "cfb", "sfc", "cfc")
        if emitters is None or any(getattr(emitters, k, None) is None for k in need):
            return None
        if len(emitters.x) == 0:
            return None
        lat = emitters.lat0 + np.asarray(emitters.y) / emitters.m_per_deg_lat
        lng = emitters.lng0 + np.asarray(emitters.x) / emitters.m_per_deg_lng
        x, y = frame.to_local(lat, lng)
        fuel = getattr(emitters, "fuel", None)
        fuel = (np.asarray(fuel, dtype=object) if fuel is not None
                else np.full(len(emitters.x), "C2", dtype=object))
        return cls(np.asarray(x, float), np.asarray(y, float),
                   np.asarray(emitters.start_min, float), np.asarray(emitters.end_min, float),
                   np.asarray(emitters.intensity_kw_m, float), np.asarray(emitters.sfc, float),
                   np.asarray(emitters.cfc, float), np.asarray(emitters.cfb, float), fuel,
                   float(emitters.cell_size))

    def kinds(self, opts: LongRangeOptions) -> np.ndarray:
        k = np.where(self.cfb >= CFB_CROWNING, "crown",
                     np.where(self.cfb >= CFB_TORCHING, "torch", "surface"))
        return k

    def emitted(self, opts: LongRangeOptions) -> np.ndarray:
        """Embers emitted by each cell over its burning time: n_kg (f_s SFC + CFC) A_cell; only
        torching/crowning cells emit unless f_s > 0 (then every burning cell)."""
        fuel_kg = opts.f_surface * self.sfc + self.cfc
        src = (self.cfb >= CFB_TORCHING) | (opts.f_surface > 0)
        return np.where(src & (self.hfi_kw_m > 0), opts.n_per_kg * fuel_kg * self.cell_size_m ** 2, 0.0)


# ----------------------------------------------------------------------------- the field


@dataclass
class Raster:
    """North-up raster in the local frame: cell (r, c) covers x0 + [c, c+1) h, y_top − [r, r+1) h."""

    x0: float
    y_top: float
    h: float
    rows: int
    cols: int

    @classmethod
    def around(cls, xs, ys, pad_m: float, h: float, max_cells: int = 6_000_000) -> "Raster":
        x0, x1 = float(np.min(xs)) - pad_m, float(np.max(xs)) + pad_m
        y0, y1 = float(np.min(ys)) - pad_m, float(np.max(ys)) + pad_m
        while math.ceil((x1 - x0) / h) * math.ceil((y1 - y0) / h) > max_cells:
            h *= 1.5  # memory guard [H]: coarser raster rather than an out-of-memory error
        return cls(x0, y1, h, int(math.ceil((y1 - y0) / h)), int(math.ceil((x1 - x0) / h)))

    def index(self, x, y):
        c = np.floor((np.asarray(x, float) - self.x0) / self.h).astype(np.int64)
        r = np.floor((self.y_top - np.asarray(y, float)) / self.h).astype(np.int64)
        ok = (r >= 0) & (r < self.rows) & (c >= 0) & (c < self.cols)
        return r, c, ok


def kernel(mu: float, sigma: float, sigma_y: float, cap_m: float, h: float, to_deg: float,
           tail: str = "cap", subsample: int = 4) -> np.ndarray:
    """Share of one source cell's embers landing in each raster cell (odd-sized, centred on
    the source). Downwind lognormal (μ, σ of ln x) × crosswind normal (σ_Y); embers beyond the
    cap are dropped (``tail='cap'``); ``'q99'`` truncates at the 99th percentile and
    renormalises (Qin 2025 eq 4.9); ``'x2'`` stretches distances beyond the median ×2 (and
    the cap ×2) [H, Thurston 2017 [A]]."""
    to = math.radians(to_deg)
    ux, uy = math.sin(to), math.cos(to)
    med = math.exp(mu)
    if tail == "q99":
        length = math.exp(mu + sigma * float(ndtri(0.99)))
    elif tail == "x2":
        length = 2.0 * cap_m
    else:
        length = cap_m
    hw = 4.0 * sigma_y + h
    ex = abs(ux) * length + abs(uy) * hw + h
    ey = abs(uy) * length + abs(ux) * hw + h
    nc, nr = int(math.ceil(ex / h)), int(math.ceil(ey / h))
    out = np.zeros((2 * nr + 1, 2 * nc + 1))
    cols = np.arange(-nc, nc + 1)
    q = (np.arange(subsample) + 0.5) / subsample - 0.5
    for r_off in range(-nr, nr + 1):
        acc = np.zeros(len(cols))
        for qy in q:
            py = (-r_off + qy) * h  # north-up: row offset +1 = 1 cell south
            for qx in q:
                px = (cols + qx) * h
                a = px * ux + py * uy
                c = -px * uy + py * ux
                if tail == "x2":
                    a_eff = np.where(a > med, med + (a - med) / 2.0, a)
                    jac = np.where(a > med, 0.5, 1.0)
                else:
                    a_eff, jac = a, 1.0
                with np.errstate(divide="ignore", invalid="ignore"):
                    fx = np.exp(-((np.log(np.maximum(a_eff, 1e-9)) - mu) ** 2) / (2 * sigma * sigma)) / (
                        np.maximum(a_eff, 1e-9) * sigma * math.sqrt(2 * math.pi))
                fx = np.where((a > 0) & (a <= length), fx * jac, 0.0)
                fy = np.exp(-c * c / (2 * sigma_y * sigma_y)) / (sigma_y * math.sqrt(2 * math.pi))
                acc += fx * fy
        out[r_off + nr] = acc * h * h / subsample ** 2
    if tail == "q99":
        out /= 0.99
    return out


@dataclass
class LandingField:
    """Expected landed embers per m² per time bin: ``density[b]`` over ``[edges[b], edges[b+1])``."""

    raster: Raster
    edges: np.ndarray
    density: np.ndarray  # (bins, rows, cols) float32
    emitted_total: float = 0.0
    landed_total: float = 0.0
    classes: list = field(default_factory=list)


def landing_field(sources: EmberSourceCells, wind, duration_min: float, opts: LongRangeOptions,
                  raster: Raster | None = None, pad_m: float | None = None) -> LandingField:
    """The expected landing field (FFT convolution per time bin × wind period × class).

    ``wind``: list of (start_min, wind_speed_kmh, wind_from_deg) in time order (10 m open
    wind). Classes: kind (crown/torch/surface) × fuel × quarter-decade of intensity; each
    class's kernel uses its emission-weighted geometric-mean intensity.
    """
    from scipy.signal import fftconvolve

    emit = sources.emitted(opts)
    keep = emit > 0
    starts = np.array([float(w[0]) for w in wind])
    u10 = np.array([float(w[1]) / 3.6 for w in wind])
    to_deg = np.array([float(w[2]) + 180.0 for w in wind])
    h = float(opts.raster_m or sources.cell_size_m)
    kinds = sources.kinds(opts)
    caps = {}
    if raster is None:
        if pad_m is None:
            pad_m = 0.0
            for k in range(len(wind)):
                for i in np.nonzero(keep)[0][:: max(1, int(keep.sum()) // 200)]:
                    pad_m = max(pad_m, cap_distance(kinds[i], sources.hfi_kw_m[i], sources.cfb[i],
                                                    u10[k], sources.fuel[i]))
            pad_m = pad_m * (2.0 if opts.tail == "x2" else 1.0) + 500.0
        xs = sources.x[keep] if keep.any() else sources.x
        ys = sources.y[keep] if keep.any() else sources.y
        raster = Raster.around(xs, ys, pad_m, h)
    edges = np.unique(np.concatenate([np.arange(0.0, duration_min, opts.bin_min), [duration_min],
                                      starts[(starts > 0) & (starts < duration_min)]]))
    nb = len(edges) - 1
    dens = np.zeros((nb, raster.rows, raster.cols), dtype=np.float32)
    out = LandingField(raster, edges, dens, emitted_total=0.0)
    if not keep.any() or nb == 0:
        return out
    r, c, on = raster.index(sources.x, sources.y)
    sel = keep & on
    idx = np.nonzero(sel)[0]
    lhfi = np.log10(np.maximum(sources.hfi_kw_m[idx], 1.0))
    key = np.array([f"{kinds[i]}|{sources.fuel[i]}|{int(math.floor(l * 4))}" for i, l in zip(idx, lhfi)])
    s0, s1 = sources.start_min[idx], np.maximum(sources.end_min[idx], sources.start_min[idx] + 1e-6)
    period = np.clip(np.searchsorted(starts, edges[:-1], side="right") - 1, 0, len(wind) - 1)
    sigma_y = HIMOTO_SIGMA_Y * sources.cell_size_m * opts.sigma_y_factor
    for kk in np.unique(key):
        m = key == kk
        ii = idx[m]
        w = emit[ii]
        kind, fuel = kk.split("|")[:2]
        hfi = float(10 ** (np.sum(w * lhfi[m]) / np.sum(w)))
        cfb = float(np.sum(w * sources.cfb[ii]) / np.sum(w))
        kernels = {}
        for b in range(nb):
            a, e = edges[b], edges[b + 1]
            ov = np.clip(np.minimum(s1[m], e) - np.maximum(s0[m], a), 0.0, None) / (s1[m] - s0[m])
            # emission before 0 counts in the first bin (a fire already burning at the start)
            if b == 0:
                ov = ov + np.clip(np.minimum(s1[m], 0.0) - s0[m], 0.0, None) / (s1[m] - s0[m])
            cnt = w * ov
            if not np.any(cnt > 0):
                continue
            k = int(period[b])
            if u10[k] <= 0:
                continue
            if k not in kernels:
                cap = cap_distance(kind, hfi, cfb, u10[k], fuel)
                mu, sg = sardoy_lognormal(hfi / 1000.0, float(u10_to_u6(u10[k])))
                kernels[k] = kernel(float(mu), float(sg), sigma_y, cap, raster.h, float(to_deg[k]),
                                    opts.tail)
                caps[(kk, k)] = cap
                out.classes.append({"class": kk, "hfi_kw_m": round(hfi, 1), "cfb": round(cfb, 3),
                                    "wind_period": k, "cap_m": round(cap, 1),
                                    "median_m": round(float(math.exp(mu)), 1),
                                    "kernel_share": round(float(kernels[k].sum()), 4)})
            grid = np.zeros((raster.rows, raster.cols))
            np.add.at(grid, (r[ii], c[ii]), cnt)
            landed = fftconvolve(grid, kernels[k], mode="same")
            np.maximum(landed, 0.0, out=landed)
            dens[b] += (landed / (raster.h * raster.h)).astype(np.float32)
            out.emitted_total += float(cnt.sum())
    out.landed_total = float(dens.sum(dtype=np.float64) * raster.h * raster.h)
    return out


# ----------------------------------------------------------------------------- buildings


def receptive_area(units, ring_m: float = RING_M) -> np.ndarray:
    """Footprint grown by the ring (m²) [H]."""
    import shapely

    if len(units) == 0:
        return np.zeros(0)
    return shapely.area(shapely.buffer(units.footprints, ring_m))


def building_hazard(units, fld: LandingField, opts: LongRangeOptions, dffmc: float,
                    combustible_roof=None) -> np.ndarray:
    """(bins, units) ignition hazard h_b = λ_b(centroid) A_r p_e k_g p_b (× roof factor)."""
    n = len(units)
    r, c, ok = fld.raster.index(units.x, units.y)
    lam = np.zeros((len(fld.edges) - 1, n))
    if ok.any():
        lam[:, ok] = fld.density[:, r[ok], c[ok]]
    p_e = p_sustained_flaming(opts.dffmc if opts.dffmc is not None else dffmc, opts.fuel_category)
    q = np.full(n, p_e * opts.k_glowing * opts.p_building)
    if combustible_roof is not None:
        q = np.where(np.asarray(combustible_roof, bool), np.minimum(q * opts.roof_factor, 1.0), q)
    return lam * receptive_area(units, opts.ring_m)[None, :] * q[None, :]


def draw_ignition_times(hazard: np.ndarray, edges: np.ndarray, rng) -> np.ndarray:
    """First event time (minutes) of each unit's inhomogeneous Poisson process (hazard
    constant within each bin); inf if none by the last edge."""
    nb, n = hazard.shape
    if n == 0:
        return np.zeros(0)
    cum = np.cumsum(hazard, axis=0)
    e = rng.exponential(1.0, size=n)
    hit = cum >= e[None, :]
    any_hit = hit[-1] if nb else np.zeros(n, bool)
    b = np.argmax(hit, axis=0)
    prev = np.where(b > 0, cum[np.maximum(b - 1, 0), np.arange(n)], 0.0)
    hb = hazard[b, np.arange(n)]
    frac = np.where(hb > 0, (e - prev) / np.where(hb > 0, hb, 1.0), 0.0)
    t = edges[b] + np.clip(frac, 0.0, 1.0) * (edges[b + 1] - edges[b])
    return np.where(any_hit, t, np.inf)


# ----------------------------------------------------------------------------- ensemble


def run_ensemble(units, t_front_min, hazard, edges, opts: LongRangeOptions, spread_fn,
                 duration_min: float):
    """M members: draw ember ignitions, add the delay, run the structure stage.

    ``spread_fn(t_seed) -> StructureSpreadResult`` runs front contact + structure spread from
    the initial involvement times. Returns (t (M, n) float32, source (M, n) int8,
    ember_seeded (M, n) bool, p_direct (n,)).
    """
    from firesim.structures.spread import SOURCE_EMBER, SOURCE_FRONT

    n = len(units)
    m_count = int(opts.members)
    t_all = np.full((m_count, n), np.inf, dtype=np.float32)
    s_all = np.zeros((m_count, n), dtype=np.int8)
    lr_all = np.zeros((m_count, n), dtype=bool)
    tf = np.asarray(t_front_min, float)
    for m in range(m_count):
        rng = np.random.default_rng([int(opts.seed), m])
        t_ign = draw_ignition_times(hazard, edges, rng) + opts.delay_s / 60.0
        t_ign[t_ign > duration_min] = np.inf
        seed_t = np.minimum(tf, t_ign)
        res = spread_fn(seed_t)
        t = np.asarray(res.t_min, float)
        src = np.asarray(res.source).astype(np.int8).copy()
        lr = (src == SOURCE_FRONT) & (t_ign < tf) & np.isclose(t, t_ign)
        src[lr] = SOURCE_EMBER
        t_all[m] = t
        s_all[m] = src
        lr_all[m] = lr
    p_direct = 1.0 - np.exp(-hazard.sum(axis=0)) if hazard.size else np.zeros(n)
    return t_all, s_all, lr_all, p_direct
