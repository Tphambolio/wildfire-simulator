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


# --- v2 (spec §6.4): urban ember source, per-ember building ignition from Himoto & Tanaka,
# parameter uncertainty in the ensemble ---------------------------------------------------
GR_STRUCTURE = 10.0  # pcs/(MW·s) = embers per MJ released: Qin 2025 Table 6.1 p.153 [T]
BETA_B_PER_KJ = GR_STRUCTURE / 1000.0  # HT08 eq 41 β_B in embers per kJ (from Qin) [D]
ALPHA_BETA_RANGE = (0.0, 2.0e-6)  # HT08 Table 3: α_B·β_B ∈ {0, 1, 2}×10⁻⁶ kJ⁻¹ [V, sensitivity set]
ALPHA_BETA_MID = 1.0e-6
ALPHA_PER_EMBER = ALPHA_BETA_MID / BETA_B_PER_KJ  # 1e-4 per ember landed on a building [D]
N_PER_KG_RANGE = (N_PER_KG_LOW, N_PER_KG_THOMPSON)  # published values 250-640 /kg [V]
COUPLE_STEP_MIN = 10.0  # urban coupling step and landing-field bin (v2) [H]
HIMOTO_TAIL_Q = 0.999  # numerical end of the untruncated Himoto kernel (0.1 % dropped) [H]
# Receptive-bed ignition (v2 primary, spec §6.4): a building is ignited through one receptive
# near-home fuel bed of the tested size; per-ember bed ignition from firebrand-shower tests
BED_AREA_M2 = 1.44  # 1.2 m × 1.2 m mulch bed (Suzuki, Manzello et al. 2015 Fire Technol., Fig. 5) [V size; H as receptor]
P_BED_RANGE = (2.0e-4, 7.0e-3)  # 1 / brands to first sustained flaming per ~1.4 m² bed: mulch 980-3,800,
#   pine bark 1,508-2,624, deck 140-5,700 (0.05 g brands; Suzuki/Manzello 2015 Table 1 × times;
#   firebrand-structures review 2026-10-11) [D]; drawn per building, log-uniform [H]
P_BED_MULCH = 5.5e-4  # dry mulch, 6-8 m/s: 7.36 m⁻²s⁻¹ × 1.44 m² × 172 s ≈ 1,823 → 1/1,823 [D]
MASS_FACTOR = 4.0  # model ember 0.2 g (Qin) / tested brand 0.05 g (Suzuki 2015 p.5): mass-equivalent [D]
P_B_RANGE = (0.1, 1.0)  # bed fire → building fire; no published value; lab corner tests (Fire
#   Technol. 53:535) span no ignition to 6/6 [H]; drawn per member, uniform
P_B_FIXED = 0.5
# v3 (spec §6.5). Forms from the literature; values fitted by leave-one-fire-out CV on the
# training fires (Eaton 2025, Palisades 2025, Camp 2018) — see PROJECT_RECORD [R37].
V3_LINK_SCALE_M = 160.0  # fitted [R37]; p_link(d) = exp(-d / scale); Knapp et al. 2021 (Camp): survival 0.058 < 18 m
#                         vs 0.354 >= 18 m from the nearest destroyed structure (decision tree, Fig. 5a)
V3_LINK_CUTOFF_M = 50.0  # graph radius for building-to-building links [H]
V3_SURVIVAL = 0.3  # P(exposed building never becomes fully involved): defence, hardening [fitted]
V3_EMBER_FACTOR = 100.0  # multiplier on q [fitted, R37; grid edge]
V3_STOP_WIND_KMH = 20.0  # wind-event end: 10 m wind < 20 km/h for 2 h, >= 3 h after ignition [H]
URBAN_SIZE_CLASSES = 4  # building-size classes for the urban kernels (quartiles) [H]
URBAN_RASTER_M = 10.0  # urban landing raster (Himoto medians are ~4-12 m) [H]
# v5 (spec §6.7): two-mode transport, surface brands, glowing factor, threshold receptors,
# short contact links. Report "Long-range embers v5 two-mode kernel 2026-10-12".
SARDOY_I_RANGE_MW = (10.0, 40.0)  # fitted plumes: 10-40 MW/m (Sardoy et al. 2008 p.483) [V]
SARDOY_U_RANGE_MS = (6.70, 17.88)  # canopy-top wind, H = 10 m, 6.70-17.88 m/s (p.483) [V]
V5_SHORT_TAIL_Q = 0.999  # short (flaming) mode truncated at its 99.9th percentile [H]; Sardoy's
#                          short mode reaches ~1,000-1,128 m at 30 MW/m (p.486), q99.9 = 1.3 km there
V5_LONG_FRACTION = 0.2762  # long (charring) mode share of landed brands, νc 0.39, 30 MW/m,
#                            11.17 m/s (p.486) [V]; applied to torching/crowning emission
V5_LONG_MEDIAN_M = 6300.0  # long mode: > 4,500 m, maximum near 6,100 m, landings to ~10.5 km
V5_LONG_SIGMA = 0.18  # (p.486, Fig. 6) -> lognormal mode 6.1 km, q01-q99 4.1-9.6 km [D]
V5_LONG_SIGMA_Y_DEG = 5.0  # crosswind spread of the long mode: tan(5°) × distance [H]
V5_LONG_RASTER_M = 100.0  # long-mode landing raster [H] (the kernel is km-wide)
# Long-mode ignition factor relative to a flaming short-mode brand, calibrated so that 2 % of
# spot fires lie beyond 3 km (Storey et al. 2020 Fire 3:10 p.7) [D]:
# 0.02 = f_L k / ((1 - f_L) + f_L k) -> k = 0.02 (1 - f_L) / (0.98 f_L) = 0.0535. Cross-check:
# only 3.26 % of the charring brands landed above 500 K (Sardoy p.486).
V5_LONG_IGNITION = 0.02 * (1.0 - V5_LONG_FRACTION) / (0.98 * V5_LONG_FRACTION)
V5_LONG_IGNITION_1KM = 0.10 * (1.0 - V5_LONG_FRACTION) / (0.90 * V5_LONG_FRACTION)  # 10 % > 1 km (sens.)
# Alternative long component located by Storey's own distribution (pre-registered variant,
# spec §6.7): short Sardoy mode at 30 MW/m, 11.17 m/s (10 m) + lognormal fitted to the pooled
# median 0.1 km, 10 % > 1 km and 2 % > 3 km (Storey et al. 2020 p.7): spot-weighted share
# 0.2565, median 733 m, σ_ln 0.99 (then 0.04 % beyond 13.9 km, Storey's maximum) [D]. With
# f_L = 0.2762 the weight is k = 0.2565 (1 − f_L) / ((1 − 0.2565) f_L) = 0.904 (near-flaming).
V5_STOREY_LONG = {"long_median_m": 733.0, "long_sigma": 0.99,
                  "long_ignition": 0.2565 * (1.0 - V5_LONG_FRACTION) / ((1.0 - 0.2565) * V5_LONG_FRACTION)}
# Fine fuel moisture < 4 %: "even tiny glowing particles had sufficient energy to start new
# spotfires" (Cruz et al. 2012 p.281 [V], after Ellis 2011). 4 % moisture = FFMC 96.75 by the
# FF-scale moisture relation m = 147.2 (101 - F) / (59.5 + F) (ST-X-3 / Van Wagner 1987) [D].
V5_DRY_MOISTURE_PCT = 4.0
V5_DRY_FFMC = (147.2 * 101.0 - V5_DRY_MOISTURE_PCT * 59.5) / (147.2 + V5_DRY_MOISTURE_PCT)  # 96.75
V5_DRY_FACTOR = 3.0  # long-mode factor × 3 when dry [H] (Cruz: "increase significantly"; no number)
# Surface-fuel brand emission share in mass (f_s): Filkov et al. 2017 Table 1 p.3267: 12-238
# brands > 5 mg per m² landed (surface fire plots) over 0.92-1.21 kg/m² consumed (p.3266) ->
# 10-260 /kg, × ~15 mg (mode 10-20 mg, p.3263) = 0.15-3.9 g/kg vs 100 g/kg for crown fuel
# (500 /kg × 0.2 g, Qin) -> f_s 0.0015-0.039; the plot median (~48 /kg) -> 0.007 [D].
V5_F_SURFACE = 0.007
V5_F_SURFACE_RANGE = (0.0015, 0.039)
# Short contact/radiant link: P(no spread from the first house) = 17/50 at 6 m spacing, 7 m/s
# (Lee & Davidson 2010 Application p.692 [V]); λ = 6 m gives (1 - e^-1 (1 - s))^4 ≈ 0.30 with
# s 0.3 [D]; home-to-home ignition at 3-6 m spacing (Hakes et al. 2017 pp.498, 500).
V5_LINK_SCALE_M = 6.0
V5_LINK_CUTOFF_M = 30.0  # p(30 m) = e^-5 = 0.007 at λ 6 m [H]
# Receptor ignition by accumulated brand mass (k-hit, replaces the per-brand p_bed · p_b):
# published thresholds per receptor, all [V] except the mulch value (second-hand, unverified):
#   3.5 g  shredded-hardwood mulch bed 1.22 m × 1.22 m (Suzuki & Manzello 2020 via
#          Masoudvaziri et al. 2021 SWUIFT p.8) [S, unverified]
#   4.6 g  pile 0.059 g/cm² (ψ*, FSJ104686, PTW 17.9 m/s) on SWUIFT's 78.5 cm² circle [D]
#   24 g   roof pile: 24 g on 78.5 cm² = 0.31 g/cm² (SWUIFT p.8, after Santamaria et al. 2015)
#   91 g   mulch bed: ~1,823 brands × 0.05 g to sustained flaming (Suzuki et al. 2015, v2 p_bed) [D]
V5_THRESHOLD_BOUNDS_G = (3.5, 91.0)
V5_THRESHOLD_G = 24.0  # primary before fitting [H]; fitted within the bounds (spec §6.7)
MODEL_EMBER_G = 0.2  # Qin 2025 model ember (C29)
V5_SURVIVAL = 0.3  # baseline survival s0 before covariates [fitted]


@dataclass(frozen=True)
class LongRangeOptions:
    """Long-range ember settings (spec §6.3 v1, §6.4 v2). Defaults are the primary values.

    ``model``: ``"v2"`` (default since 2026-10-11, spec §6.4) adds burning buildings as ember
    sources (iterated every ``step_min``), ignites a building per ember landed on its
    footprint with α = α_B (Himoto & Tanaka 2008) and draws α_B·β_B and the per-kg yield per
    member from their published ranges; ``"v1"`` is the 2026-10-11 chain of §6.3 (receptive
    ring, p_e · k_g · p_b, wildland source only, fixed parameters).
    """

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
    # v2
    model: str = "v2"
    urban_source: bool = True
    param_draws: bool = True  # draw p_b, n_kg (per member) and p_bed (per building)
    receptive: str = "bed"  # "bed" (primary), "footprint" (HT08 α), "ring" (v1 A_r with α)
    bed_area_m2: float = BED_AREA_M2
    p_bed_range: tuple = P_BED_RANGE
    p_bed: float = P_BED_MULCH  # without draws
    mass_factor: float = MASS_FACTOR
    p_b_range: tuple = P_B_RANGE
    p_b: float = P_B_FIXED  # without draws
    alpha: float = ALPHA_PER_EMBER  # HT08 per-ember probability ("footprint"/"ring") without draws
    alpha_beta_range: tuple = ALPHA_BETA_RANGE
    n_per_kg_range: tuple = N_PER_KG_RANGE
    step_min: float = COUPLE_STEP_MIN
    gr_structure: float = GR_STRUCTURE
    structure_tail_q: float = HIMOTO_TAIL_Q
    design_fire_kw_m2: int = 150
    # v3 (spec §6.5): probabilistic building-to-building links, survival, ember factor, run end
    link_scale_m: float = V3_LINK_SCALE_M  # p_link = exp(-separation / scale)
    link_cutoff_m: float = V3_LINK_CUTOFF_M
    survival: float = V3_SURVIVAL  # P(an exposed building never becomes fully involved)
    ember_factor: float = V3_EMBER_FACTOR  # multiplier on the per-ember probability q
    stop_wind_kmh: float | None = V3_STOP_WIND_KMH  # wind-event end rule (None: run to the end)
    # v5 (spec §6.7): two-mode transport, surface brands, glowing factor, threshold receptor
    long_fraction: float = V5_LONG_FRACTION  # share of torching/crowning emission in the long mode
    long_median_m: float = V5_LONG_MEDIAN_M
    long_sigma: float = V5_LONG_SIGMA
    long_ignition: float = V5_LONG_IGNITION  # long-mode (charring) brand weight vs a flaming brand
    dry_factor: float = V5_DRY_FACTOR  # × long_ignition when FFMC >= V5_DRY_FFMC (moisture < 4 %)
    threshold_g: float = V5_THRESHOLD_G  # brand mass on the 1.44 m² receptor to ignite it
    ember_mass_g: float = MODEL_EMBER_G
    short_tail_q: float = V5_SHORT_TAIL_Q
    long_raster_m: float = V5_LONG_RASTER_M

    @classmethod
    def for_model(cls, model: str, **kw) -> "LongRangeOptions":
        """Options with the model's own primary values (v3: fitted λ 160 m, s 0.3, k 100 [R37];
        v5: two-mode kernel, f_s, threshold receptor, λ 6 m, cutoff 30 m, s0; spec §6.7)."""
        if model == "v5":
            base = {"f_surface": V5_F_SURFACE, "link_scale_m": V5_LINK_SCALE_M,
                    "link_cutoff_m": V5_LINK_CUTOFF_M, "survival": V5_SURVIVAL, "ember_factor": 1.0}
            base.update(kw)
            return cls(model=model, **base)
        return cls(model=model, **kw)

    def __post_init__(self):
        if self.tail not in ("cap", "q99", "x2"):
            raise ValueError("tail must be 'cap', 'q99' or 'x2'")
        if not 1 <= int(self.members) <= MAX_MEMBERS:
            raise ValueError(f"members must be 1-{MAX_MEMBERS}")
        if self.model not in ("v1", "v2", "v3", "v5"):
            raise ValueError("model must be 'v1', 'v2', 'v3' or 'v5'")
        if not 0.0 <= self.long_fraction < 1.0:
            raise ValueError("long_fraction must be in [0, 1)")
        if not self.threshold_g > 0 or not self.ember_mass_g > 0:
            raise ValueError("threshold_g and ember_mass_g must be > 0")
        if not 0.0 <= self.survival < 1.0:
            raise ValueError("survival must be in [0, 1)")
        if not self.link_scale_m > 0:
            raise ValueError("link_scale_m must be > 0")
        if self.receptive not in ("bed", "footprint", "ring"):
            raise ValueError("receptive must be 'bed', 'footprint' or 'ring'")
        if not self.step_min > 0:
            raise ValueError("step_min must be > 0")

    @property
    def field_bin_min(self) -> float:
        """Landing-field bin: the coupling step in v2, ``bin_min`` in v1."""
        return float(self.bin_min if self.model == "v1" else self.step_min)

    def params(self) -> dict:
        out = {"long_range_embers": True, "long_range_members": int(self.members),
               "long_range_model": self.model,
               "long_range_embers_per_kg": float(self.n_per_kg),
               "long_range_tail": self.tail}
        if self.model == "v5":
            out.update({"long_range_long_fraction": float(self.long_fraction),
                        "long_range_long_median_m": float(self.long_median_m),
                        "long_range_long_ignition": float(self.long_ignition),
                        "long_range_surface_share": float(self.f_surface),
                        "long_range_threshold_g": float(self.threshold_g)})
        if self.model in ("v3", "v5"):
            out.update({"long_range_link_scale_m": float(self.link_scale_m),
                        "long_range_link_cutoff_m": float(self.link_cutoff_m),
                        "long_range_survival": float(self.survival),
                        "long_range_ember_factor": float(self.ember_factor),
                        "long_range_stop_wind_kmh": self.stop_wind_kmh})
        if self.model == "v1":
            out.update({"long_range_p_building": float(self.p_building),
                        "long_range_glowing_factor": float(self.k_glowing)})
        else:
            out.update({"long_range_urban_source": bool(self.urban_source),
                        "long_range_parameter_draws": bool(self.param_draws),
                        "long_range_receptive": self.receptive,
                        "long_range_embers_per_kg_range": list(self.n_per_kg_range)
                        if self.param_draws else float(self.n_per_kg),
                        "long_range_step_min": float(self.step_min)})
        return out


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
    emit_scale: np.ndarray | None = None  # v4: per-cell emission multiplier (e.g. VIIRS FRP)

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
        torching/crowning cells emit unless f_s > 0 (then every burning cell; v5: surface
        cells emit f_s SFC only, their CFC is 0 below CFB 0.1)."""
        fuel_kg = opts.f_surface * self.sfc + np.where(self.cfb >= CFB_TORCHING, self.cfc, 0.0) \
            if opts.model == "v5" else opts.f_surface * self.sfc + self.cfc
        src = (self.cfb >= CFB_TORCHING) | (opts.f_surface > 0)
        out = np.where(src & (self.hfi_kw_m > 0), opts.n_per_kg * fuel_kg * self.cell_size_m ** 2, 0.0)
        if self.emit_scale is not None:
            out = out * np.asarray(self.emit_scale, float)
        return out

    def emitted_long(self, opts: LongRangeOptions) -> np.ndarray:
        """v5: embers of each cell that enter the long (charring) mode: f_L × the crown-fuel
        emission of torching/crowning cells (Sardoy 2008 p.486 [V share]; surface brands of
        5-50 mg (Filkov 2017 p.3263) are given the short mode only [H])."""
        out = np.where((self.cfb >= CFB_TORCHING) & (self.hfi_kw_m > 0),
                       opts.long_fraction * opts.n_per_kg * self.cfc * self.cell_size_m ** 2, 0.0)
        if self.emit_scale is not None:
            out = out * np.asarray(self.emit_scale, float)
        return out


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
           tail: str = "cap", subsample: int = 4, offset_m: float = 0.0, crop: tuple | None = None,
           return_total: bool = False):
    """Share of one source cell's embers landing in each raster cell (odd-sized, centred on
    the source). Downwind lognormal (μ, σ of ln x) × crosswind normal (σ_Y); embers beyond the
    cap are dropped (``tail='cap'``); ``'q99'`` truncates at the 99th percentile and
    renormalises (Qin 2025 eq 4.9); ``'x2'`` stretches distances beyond the median ×2 (and
    the cap ×2) [H, Thurston 2017 [A]]. ``offset_m``: flight distances are measured from a
    point ``offset_m`` downwind of the source centre (a building's downwind edge, Qin 2025
    §6.3.1; v2 urban source).

    ``crop = (max_rows, max_cols)``: keep only offsets within ±max_rows / ±max_cols of the source
    (still odd-sized and centred). A ``mode="same"`` convolution on a grid of R x C cells only uses
    offsets up to ±(R-1, C-1), so cropping to that is exact on the grid and stops a long-tailed
    kernel (e.g. a short-range cap of tens of km at extreme intensity) from allocating gigabytes
    (2026-10-17: a v7 stage-2 Tubbs run reached 14.6 GB on a 335 x 257 grid). ``return_total``
    also returns the sum of the FULL (uncropped) kernel, for callers that normalise by it."""
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
    ex = abs(ux) * (length + offset_m) + abs(uy) * hw + h
    ey = abs(uy) * (length + offset_m) + abs(ux) * hw + h
    nc, nr = int(math.ceil(ex / h)), int(math.ceil(ey / h))
    kr, kc = (min(nr, int(crop[0])), min(nc, int(crop[1]))) if crop is not None else (nr, nc)
    out = np.zeros((2 * kr + 1, 2 * kc + 1))
    total = 0.0
    q = (np.arange(subsample) + 0.5) / subsample - 0.5
    # The crosswind factor underflows to exactly 0.0 beyond |c| = sqrt(2 * 746) * sigma_y, so only a
    # strip of columns per row block is evaluated (2026-10-17: a 2,400 km cap took 170 s per kernel).
    # Skipped cells are exact zeros; cells kept are computed as before, only the sum order changes.
    c_max = math.sqrt(2.0 * 746.0) * sigma_y + 2.0 * h
    block = 64
    for r0 in range(-nr, nr + 1, block):
        r_off = np.arange(r0, min(r0 + block, nr + 1))[:, None]
        py_lo, py_hi = (-r_off[-1, 0] - 0.5) * h, (-r_off[0, 0] + 0.5) * h
        if abs(uy) > 1e-9:
            ends = [(py * ux + s * c_max) / uy for py in (py_lo, py_hi) for s in (-1.0, 1.0)]
            c0 = max(-nc, int(math.floor(min(ends) / h)) - 1)
            c1 = min(nc, int(math.ceil(max(ends) / h)) + 1)
        else:
            c0, c1 = -nc, nc
        reach = abs(uy) * (nc + 1) * h
        if c0 > c1 or min(abs(py_lo * ux), abs(py_hi * ux)) - reach > c_max and py_lo * py_hi > 0:
            continue
        cols = np.arange(c0, c1 + 1)
        acc = np.zeros((len(r_off), len(cols)))
        for qy in q:
            py = (-r_off + qy) * h  # north-up: row offset +1 = 1 cell south
            for qx in q:
                px = (cols + qx) * h
                a = px * ux + py * uy - offset_m
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
        acc = acc * h * h / subsample ** 2  # same evaluation order as before the crop (bit-identical)
        total += float(acc.sum())
        rows = r_off[:, 0]
        keep = np.abs(rows) <= kr
        k0, k1 = max(c0, -kc), min(c1, kc)
        if keep.any() and k0 <= k1:
            out[rows[keep] + kr, k0 + kc:k1 + kc + 1] = acc[keep][:, k0 - c0:k1 - c0 + 1]
    if tail == "q99":
        out /= 0.99
        total /= 0.99
    return (out, total) if return_total else out


@dataclass
class LandingField:
    """Expected landed embers per m² per time bin: ``density[b]`` over ``[edges[b], edges[b+1])``."""

    raster: Raster
    edges: np.ndarray
    density: np.ndarray  # (bins, rows, cols) float32
    emitted_total: float = 0.0
    landed_total: float = 0.0
    classes: list = field(default_factory=list)
    # v5: the long (charring) mode on its own coarse raster, and the Sardoy validity flag
    long_raster: Raster | None = None
    long_density: np.ndarray | None = None  # (bins, rows_L, cols_L) float32
    long_emitted: float = 0.0
    extrapolated_emitted: float = 0.0  # short-mode emission outside I 10-40 MW/m, U 6.7-17.9 m/s

    @property
    def extrapolated_share(self) -> float:
        """Share of short-mode emission whose Sardoy kernel is used outside the fitted range."""
        return float(self.extrapolated_emitted / self.emitted_total) if self.emitted_total > 0 else 0.0


def sardoy_extrapolated(i_mw_m: float, u10_ms: float) -> bool:
    """True when the Sardoy fit is used outside its plumes: I 10-40 MW/m, canopy-top (10 m)
    wind 6.70-17.88 m/s (Sardoy et al. 2008 p.483) [V]."""
    return not (SARDOY_I_RANGE_MW[0] <= float(i_mw_m) <= SARDOY_I_RANGE_MW[1]
                and SARDOY_U_RANGE_MS[0] <= float(u10_ms) <= SARDOY_U_RANGE_MS[1])


def long_mode_kernel(opts: LongRangeOptions, h: float, to_deg: float) -> np.ndarray:
    """v5 long (charring) mode: lognormal in distance (median ``long_median_m``, σ_ln
    ``long_sigma``; Sardoy 2008 p.486 Fig. 6: > 4.5 km, maximum ~6.1 km, to ~10.5 km at
    30 MW/m, 11.17 m/s) × normal crosswind σ_Y = tan(5°) × median [H], cut at its 99.9th
    percentile; shares per raster cell (sum ≈ 1)."""
    mu = math.log(opts.long_median_m)
    cap = math.exp(mu + opts.long_sigma * float(ndtri(0.999)))
    sy = math.tan(math.radians(V5_LONG_SIGMA_Y_DEG)) * opts.long_median_m
    return kernel(mu, opts.long_sigma, sy, cap, h, to_deg, "cap", subsample=2)


def long_ignition_factor(opts: LongRangeOptions, ffmc: float | None) -> float:
    """Weight of a long-mode (charring) brand relative to a flaming one (v5): ``long_ignition``
    (Storey 2020 calibration), × ``dry_factor`` when the fine fuel moisture is below 4 %
    (FFMC >= 96.75; Cruz et al. 2012 p.281), at most 1."""
    k = float(opts.long_ignition)
    if ffmc is not None and float(ffmc) >= V5_DRY_FFMC:
        k *= float(opts.dry_factor)
    return float(min(k, 1.0))


def landing_field(sources: EmberSourceCells, wind, duration_min: float, opts: LongRangeOptions,
                  raster: Raster | None = None, pad_m: float | None = None) -> LandingField:
    """The expected landing field (FFT convolution per time bin × wind period × class).

    ``wind``: list of (start_min, wind_speed_kmh, wind_from_deg) in time order (10 m open
    wind). Classes: kind (crown/torch/surface) × fuel × quarter-decade of intensity; each
    class's kernel uses its emission-weighted geometric-mean intensity.
    """
    from scipy.signal import fftconvolve

    v5 = opts.model == "v5"
    emit = sources.emitted(opts)
    keep = emit > 0
    emit_long = sources.emitted_long(opts) if v5 else np.zeros(len(emit))
    emit_short = emit - emit_long
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
    edges = np.unique(np.concatenate([np.arange(0.0, duration_min, opts.field_bin_min), [duration_min],
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
    if v5:  # the long mode on a coarse raster around the sources [H]
        hl = max(float(opts.long_raster_m), h)
        reach_l = math.exp(math.log(opts.long_median_m) + opts.long_sigma * float(ndtri(0.999)))
        xs_ = sources.x[keep] if keep.any() else sources.x
        ys_ = sources.y[keep] if keep.any() else sources.y
        out.long_raster = Raster.around(xs_, ys_, reach_l + 4.0 * hl, hl)
        out.long_density = np.zeros((nb, out.long_raster.rows, out.long_raster.cols), dtype=np.float32)
        rl, cl, onl = out.long_raster.index(sources.x, sources.y)
        long_kernels = {}
    for kk in np.unique(key):
        m = key == kk
        ii = idx[m]
        w = emit[ii]
        ws = emit_short[ii]
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
            cnt = ws * ov
            if not np.any(w * ov > 0):
                continue
            k = int(period[b])
            if u10[k] <= 0:
                continue
            if k not in kernels:
                cap = cap_distance(kind, hfi, cfb, u10[k], fuel)
                if v5:  # Sardoy's own 10 m canopy-top wind (p.483; C40), short mode truncated
                    mu, sg = sardoy_lognormal(hfi / 1000.0, u10[k])
                    cap = min(cap, float(math.exp(float(mu) + float(sg) * float(ndtri(opts.short_tail_q)))))
                else:
                    mu, sg = sardoy_lognormal(hfi / 1000.0, float(u10_to_u6(u10[k])))
                kernels[k] = kernel(float(mu), float(sg), sigma_y, cap, raster.h, float(to_deg[k]),
                                    opts.tail)
                caps[(kk, k)] = cap
                rec = {"class": kk, "hfi_kw_m": round(hfi, 1), "cfb": round(cfb, 3),
                       "wind_period": k, "cap_m": round(cap, 1),
                       "median_m": round(float(math.exp(mu)), 1),
                       "kernel_share": round(float(kernels[k].sum()), 4)}
                if v5:
                    rec["sardoy_extrapolated"] = sardoy_extrapolated(hfi / 1000.0, u10[k])
                out.classes.append(rec)
            grid = np.zeros((raster.rows, raster.cols))
            np.add.at(grid, (r[ii], c[ii]), cnt)
            landed = fftconvolve(grid, kernels[k], mode="same")
            np.maximum(landed, 0.0, out=landed)
            dens[b] += (landed / (raster.h * raster.h)).astype(np.float32)
            out.emitted_total += float(cnt.sum())
            if v5 and sardoy_extrapolated(hfi / 1000.0, u10[k]):
                out.extrapolated_emitted += float(cnt.sum())
            if v5:
                cnt_l = emit_long[ii] * ov
                sel_l = onl[ii] & (cnt_l > 0)
                if sel_l.any():
                    if k not in long_kernels:
                        long_kernels[k] = long_mode_kernel(opts, out.long_raster.h, float(to_deg[k]))
                    gl = np.zeros((out.long_raster.rows, out.long_raster.cols))
                    np.add.at(gl, (rl[ii][sel_l], cl[ii][sel_l]), cnt_l[sel_l])
                    ll = fftconvolve(gl, long_kernels[k], mode="same")
                    np.maximum(ll, 0.0, out=ll)
                    out.long_density[b] += (ll / (out.long_raster.h ** 2)).astype(np.float32)
                    out.long_emitted += float(cnt_l[sel_l].sum())
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


# ----------------------------------------------------------------------------- v2 (spec §6.4)


def receptive_m2(units, opts: LongRangeOptions) -> np.ndarray:
    """Receptive area per unit (m²): one receptive bed of the tested size (``"bed"``), the
    footprint (``"footprint"``, HT08 eq 37: the brand "lands on the building"), or footprint +
    ring (``"ring"``, v1)."""
    if opts.receptive == "bed":
        return np.full(len(units), float(opts.bed_area_m2))
    if opts.receptive == "footprint":
        return np.asarray(units.area_m2, float)
    return receptive_area(units, opts.ring_m)


def landed_on_buildings(units, fld: LandingField, opts: LongRangeOptions) -> np.ndarray:
    """(bins, units) expected wildland embers landing on each building's receptive area
    (λ_b at its centroid × ``receptive_m2``). Scales linearly with ``n_per_kg``."""
    n = len(units)
    r, c, ok = fld.raster.index(units.x, units.y)
    lam = np.zeros((len(fld.edges) - 1, n))
    if ok.any():
        lam[:, ok] = fld.density[:, r[ok], c[ok]]
    return lam * receptive_m2(units, opts)[None, :]


def landed_long_on_buildings(units, fld: LandingField, opts: LongRangeOptions) -> np.ndarray:
    """(bins, units) v5 long-mode (charring) embers landing on each building's receptive area
    (unweighted; multiply by ``long_ignition_factor``). Zeros without a long field."""
    n = len(units)
    out = np.zeros((len(fld.edges) - 1, n))
    if fld.long_density is None or n == 0:
        return out
    r, c, ok = fld.long_raster.index(units.x, units.y)
    if ok.any():
        out[:, ok] = fld.long_density[:, r[ok], c[ok]]
    return out * receptive_m2(units, opts)[None, :]


def landed_v5(units, fld: LandingField, opts: LongRangeOptions, ffmc: float | None) -> np.ndarray:
    """(bins, units) v5 effective embers on each receptor: short (flaming, weight 1; > 99 % of
    the short mode lands flaming, Sardoy p.486) + long × ``long_ignition_factor``."""
    return (landed_on_buildings(units, fld, opts)
            + long_ignition_factor(opts, ffmc) * landed_long_on_buildings(units, fld, opts))


def weighted_hrrpua(design_fire) -> float:
    """Emission-weighted mean HRR per area of a design fire, ∫q²dt / ∫q dt (kW/m²) [D]: the
    HRR at which an average ember is emitted (embers ∝ HRR, Qin 2025 eq 3.1)."""
    t = np.linspace(0.0, design_fire.duration_s, 4001)
    q = design_fire.hrrpua(t)
    trap = getattr(np, "trapezoid", None) or np.trapz
    return float(trap(q * q, t) / max(trap(q, t), 1e-12))


@dataclass
class UrbanSource:
    """Burning buildings as ember sources (v2, spec §6.4).

    Embers per unit and step: GR' · ∫HRR dt (Qin 2025 eq 3.1, Table 6.1: GR' = 10 per MJ),
    carried with the Himoto & Tanaka (2008) lognormal flight distance from the source's
    downwind edge (eqs 38-40; ``embers.himoto_lognormal`` at the 10 m wind, spec C17) and a
    normal crosswind spread σ_Y = 0.92 D (eq 39), **not** truncated at Qin's 99th percentile
    (the kernel ends at the 99.9th [H]). One kernel per building-size class (quartiles of D
    [H]) at the class's emission-weighted HRR and per wind period, on a 10 m raster [H]; the
    landing density at a target is the kernel value at the target's cell offset from the
    source's cell (the raster convolution evaluated only at building cells, as a sparse
    source → target matrix).
    """

    weights: dict  # period -> scipy.sparse csr (targets × sources), embers/m² per ember
    area: np.ndarray
    design_fire: object
    gr: float
    reach: float

    @classmethod
    def build(cls, units, wind, opts: LongRangeOptions, h: float = URBAN_RASTER_M) -> "UrbanSource":
        """``wind``: list of (start_min, wind_speed_kmh, wind_from_deg)."""
        from scipy.sparse import csr_matrix
        from scipy.spatial import cKDTree

        from firesim.structures.embers import DESIGN_FIRES, himoto_lognormal

        df = DESIGN_FIRES[int(opts.design_fire_kw_m2)]
        n = len(units)
        area = np.asarray(units.area_m2, float)
        q_w = weighted_hrrpua(df)
        d = np.asarray(units.size_m, float)
        if n == 0:
            return cls({}, area, df, float(opts.gr_structure), 0.0)
        qs = np.quantile(d, np.linspace(0, 1, URBAN_SIZE_CLASSES + 1))
        cl = np.clip(np.searchsorted(qs[1:-1], d, side="right"), 0, URBAN_SIZE_CLASSES - 1)
        kernels, reach = {}, 0.0
        for k in range(URBAN_SIZE_CLASSES):
            m = cl == k
            if not m.any():
                continue
            dk = float(np.median(d[m]))
            for p, (_, kmh, wfrom) in enumerate(wind):
                u10 = float(kmh) / 3.6
                if u10 <= 0:
                    continue
                mu, sg = himoto_lognormal(q_w * dk * dk, u10, dk)
                mu, sg = float(mu), float(sg)
                cap = math.exp(mu + sg * float(ndtri(opts.structure_tail_q)))
                ker = kernel(mu, sg, HIMOTO_SIGMA_Y * dk, cap, h, float(wfrom) + 180.0, "cap",
                             offset_m=dk / 2.0)
                kernels[(k, p)] = ker
                reach = max(reach, math.hypot(ker.shape[0] // 2, ker.shape[1] // 2) * h)
        # cells of every unit on a common 10 m lattice
        cr = np.floor(-np.asarray(units.y, float) / h).astype(np.int64)
        cc = np.floor(np.asarray(units.x, float) / h).astype(np.int64)
        tree = cKDTree(np.column_stack([units.x, units.y]))
        pairs = tree.query_pairs(reach + h, output_type="ndarray")
        src = np.concatenate([pairs[:, 0], pairs[:, 1], np.arange(n)])
        tgt = np.concatenate([pairs[:, 1], pairs[:, 0], np.arange(n)])
        del pairs
        dr, dc = cr[tgt] - cr[src], cc[tgt] - cc[src]
        weights = {}
        for p in range(len(wind)):
            val = np.zeros(len(src), dtype=np.float32)
            for k in range(URBAN_SIZE_CLASSES):
                ker = kernels.get((k, p))
                if ker is None:
                    continue
                hr, hc = ker.shape[0] // 2, ker.shape[1] // 2
                m = (cl[src] == k) & (np.abs(dr) <= hr) & (np.abs(dc) <= hc)
                val[m] = ker[hr + dr[m], hc + dc[m]] / (h * h)
            keep = (val > 0) & (src != tgt)  # nothing lands on the emitting footprint (Qin25 p.170)
            weights[p] = csr_matrix((val[keep], (tgt[keep], src[keep])), shape=(n, n))
        return cls(weights, area, df, float(opts.gr_structure), reach)

    def emitted(self, t_inv, a: float, b: float) -> np.ndarray:
        """Embers emitted by each unit in [a, b) minutes (0 if not burning)."""
        t = np.asarray(t_inv, float)
        fin = np.isfinite(t) & (t < b)
        out = np.zeros(len(t))
        if fin.any():
            ti = t[fin]
            e = (self.design_fire.energy_per_area((b - ti) * 60.0)
                 - self.design_fire.energy_per_area((np.maximum(a, ti) - ti) * 60.0))
            out[fin] = self.gr * e * self.area[fin] / 1000.0  # kJ/m² × m² / 1000 = MJ
        return out

    def density_at_units(self, counts: np.ndarray, period: int) -> np.ndarray:
        """Landed embers per m² at each unit from ``counts`` emitted by the units."""
        w = self.weights.get(int(period))
        if w is None:
            return np.zeros(len(counts))
        return np.asarray(w @ np.asarray(counts, float)).ravel()

    @property
    def reach_m(self) -> float:
        return float(self.reach)


class IncrementalHamada:
    """``spread.hamada_spread`` advanced in time steps, with seeds added between steps (v2).

    Same event loop, crossing function, burn-out and link filter as ``hamada_spread``; events
    are processed only up to ``advance(t_end)``. A seed added later than events already
    processed is still propagated from its own time, but units already involved keep their
    times (coupling resolution = the step, spec §6.4 [H]).
    """

    def __init__(self, units, wind, *, duration_min: float, fb: float, burnout_min=None,
                 link_filter=None, blocked=None):
        from firesim.structures.spread import WindPeriod  # noqa: F401  (type of ``wind``)

        self.units, self.fb, self.burnout_min = units, fb, burnout_min
        self.link_filter, self.duration_min = link_filter, float(duration_min)
        # v3 survival: units that never become involved (defended / not transitioning)
        self.blocked = None if blocked is None else np.asarray(blocked, bool)
        self.starts = np.array([w.start_min for w in wind], dtype=float)
        self.speed = np.array([w.wind_speed_kmh / 3.6 for w in wind])
        to = np.radians(np.array([w.wind_direction_deg for w in wind]) + 180.0)
        self.sx, self.sy = np.sin(to), np.cos(to)

    def reset(self, t_front_min) -> None:
        from firesim.structures.spread import SOURCE_FRONT, SOURCE_NONE

        n = len(self.units)
        t = np.asarray(t_front_min, dtype=float).copy()
        t[t > self.duration_min] = np.inf
        if self.blocked is not None:
            t[self.blocked] = np.inf
        self.t = t
        self.source = np.where(np.isfinite(t), SOURCE_FRONT, SOURCE_NONE).astype(np.int8)
        self.done = np.zeros(n, dtype=bool)
        self.heap = [(float(t[i]), int(i)) for i in np.nonzero(np.isfinite(t))[0]]
        import heapq

        heapq.heapify(self.heap)

    def add_seeds(self, idx, times) -> None:
        import heapq

        from firesim.structures.spread import SOURCE_FRONT

        for i, ti in zip(np.asarray(idx, int), np.asarray(times, float)):
            if self.done[i] or not ti < self.t[i] or ti > self.duration_min:
                continue
            if self.blocked is not None and self.blocked[i]:
                continue
            self.t[i] = ti
            self.source[i] = SOURCE_FRONT
            heapq.heappush(self.heap, (float(ti), int(i)))

    def advance(self, t_end: float) -> np.ndarray:
        import heapq

        from firesim.structures.spread import SOURCE_STRUCTURE, _cross

        u, t, done = self.units, self.t, self.done
        while self.heap and self.heap[0][0] <= t_end:
            ti, i = heapq.heappop(self.heap)
            if done[i] or ti > t[i]:
                continue
            done[i] = True
            nb, sep = u.neighbours(i)
            if len(nb) == 0:
                continue
            keep = ~done[nb]
            if self.blocked is not None:
                keep &= ~self.blocked[nb]
            nb, sep = nb[keep], sep[keep]
            if len(nb) and self.link_filter is not None:
                keep = self.link_filter(i, nb, sep)
                nb, sep = nb[keep], sep[keep]
            if len(nb) == 0:
                continue
            a0 = (u.size_m[i] + u.size_m[nb]) / 2.0
            dx, dy = u.x[nb] - u.x[i], u.y[nb] - u.y[i]
            dist = np.hypot(dx, dy)
            safe = np.where(dist > 0, dist, 1.0)
            arrive = _cross(ti, a0, sep, dx / safe, dy / safe, dist > 0, self.starts, self.speed,
                            self.sx, self.sy, self.fb, self.duration_min,
                            burnout_at=math.inf if self.burnout_min is None else ti + self.burnout_min)
            better = arrive < t[nb]
            for j, tj in zip(nb[better], arrive[better]):
                t[j] = tj
                self.source[j] = SOURCE_STRUCTURE
                heapq.heappush(self.heap, (float(tj), int(j)))
        return t

    def final(self):
        self.advance(self.duration_min)
        return self.t.copy(), self.source.copy()


class RerunStage:
    """Stage adapter for a structure-stage function ``spread_fn(seed_times, horizon_min)``
    (e.g. ``spread_with_embers`` with short-range embers): re-run after every new seed."""

    def __init__(self, spread_fn, duration_min: float):
        self.fn, self.duration_min = spread_fn, float(duration_min)

    def reset(self, t_front_min) -> None:
        self.seeds = np.asarray(t_front_min, float).copy()
        self.seeds[self.seeds > self.duration_min] = np.inf
        self._res, self._h = None, -1.0

    def add_seeds(self, idx, times) -> None:
        idx = np.asarray(idx, int)
        times = np.asarray(times, float)
        better = (times < self.seeds[idx]) & (times <= self.duration_min)
        if better.any():
            self.seeds[idx[better]] = times[better]
            self._res = None

    def advance(self, t_end: float) -> np.ndarray:
        if self._res is None or t_end > self._h:
            self._h = min(self.duration_min, max(t_end, self._h) + 60.0)
            self._res = self.fn(self.seeds, self._h)
        return np.asarray(self._res.t_min, float)

    def final(self):
        res = self.fn(self.seeds, self.duration_min)
        return np.asarray(res.t_min, float), np.asarray(res.source).astype(np.int8)


@dataclass
class MemberDraw:
    q: np.ndarray  # per-unit probability that one ember landed on the receptive area ignites the building
    n_scale: float  # n_kg / opts.n_per_kg (the landing field was built with opts.n_per_kg)
    p_b: float = float("nan")
    alpha: float = float("nan")
    info: dict = field(default_factory=dict)  # filled by run_member_v2 (diagnostics)


def member_draw(opts: LongRangeOptions, rng, n: int) -> MemberDraw:
    """Per-member parameters (v2) [H: uniform over published values / ranges].

    ``receptive="bed"``: q_i = mass_factor · p_bed,i · p_b, with p_bed,i ~ log-uniform over
    ``p_bed_range`` per building (bed material varies between buildings) and p_b ~ U(p_b_range)
    per member (no published value). ``"footprint"``/``"ring"``: q = α = α_B·β_B / β_B with
    α_B·β_B ~ U(HT08 range) per member. n_kg ~ U(250, 640) per member. Fixed values when
    ``param_draws`` is False. The draw order is fixed (n_kg, p_b or α, then p_bed)."""
    draws = opts.model in ("v2", "v3", "v5") and opts.param_draws
    nk = float(rng.uniform(*opts.n_per_kg_range)) if draws else float(opts.n_per_kg)
    if opts.model == "v5":  # every landed (weighted) model ember counts toward the mass threshold
        return MemberDraw(np.ones(n), nk / opts.n_per_kg)
    if opts.receptive == "bed":
        p_b = float(rng.uniform(*opts.p_b_range)) if draws else float(opts.p_b)
        if draws:
            lo, hi = np.log(opts.p_bed_range[0]), np.log(opts.p_bed_range[1])
            p_bed = np.exp(rng.uniform(lo, hi, size=n))
        else:
            p_bed = np.full(n, float(opts.p_bed))
        k = float(opts.ember_factor) if opts.model == "v3" else 1.0
        q = np.minimum(k * opts.mass_factor * p_bed * p_b, 1.0)
        return MemberDraw(q, nk / opts.n_per_kg, p_b=p_b)
    a = float(rng.uniform(*opts.alpha_beta_range)) / BETA_B_PER_KJ if draws else float(opts.alpha)
    return MemberDraw(np.full(n, a), nk / opts.n_per_kg, alpha=a)


def run_member_v2(units, t_front_min, wild_landed, edges, opts: LongRangeOptions, stage,
                  duration_min: float, rng, urban: UrbanSource | None, wind_periods=None,
                  alpha_factor=None, draw: MemberDraw | None = None, thresholds=None):
    """One v2 member: wildland + urban embers, per-ember building ignition, structure stage.

    Every unit draws E ~ Exp(1); its hazard H(t) = q Σ (embers landed on its receptive
    area, ``receptive_m2``) grows bin by bin; it ignites when H crosses E (time interpolated inside the bin), becomes
    involved ``delay_s`` later and, once involved (by front, Hamada or embers), emits embers
    for its design fire's length. ``stage``: ``IncrementalHamada`` or ``RerunStage``.

    ``wild_landed``: (bins, n) wildland embers landed per unit (``landed_on_buildings``) or
    None. ``alpha_factor``: per-unit factor on q (roof scenario). The draw (``member_draw``) is
    taken from ``rng`` first, then the thresholds. Returns (t_min, source, ember_seeded,
    landed, draw); ``draw.info`` counts ember ignitions by dominant source (urban / wildland)."""
    from firesim.structures.spread import SOURCE_EMBER, SOURCE_FRONT

    n = len(units)
    draw = draw or member_draw(opts, rng, n)
    a_unit = (draw.q if alpha_factor is None
              else np.minimum(draw.q * np.asarray(alpha_factor, float), 1.0))
    a_rec = receptive_m2(units, opts)
    e_thr = rng.exponential(1.0, size=n) if thresholds is None else np.asarray(thresholds, float)
    hz = np.zeros(n)
    landed = np.zeros(n)
    tf = np.asarray(t_front_min, float).copy()
    tf[tf > duration_min] = np.inf
    stage.reset(tf)
    ember_t = np.full(n, np.inf)
    nb = len(edges) - 1
    starts = np.array([float(w[0]) for w in wind_periods]) if wind_periods else np.zeros(1)
    delay = opts.delay_s / 60.0
    n_urb = n_wild = 0
    for b in range(nb):
        a, e = float(edges[b]), float(edges[b + 1])
        t_inv = stage.advance(e)
        add = (np.asarray(wild_landed[b], float) * draw.n_scale if wild_landed is not None
               else np.zeros(n))
        add_u = None
        if urban is not None and opts.urban_source:
            cnt = urban.emitted(t_inv, a, e)
            if cnt.any():
                p = max(int(np.searchsorted(starts, a, side="right")) - 1, 0)
                add_u = urban.density_at_units(cnt, p) * a_rec
                add = add + add_u
        landed += add
        inc = a_unit * add
        new = hz + inc
        cross = (hz < e_thr) & (new >= e_thr) & ~np.isfinite(ember_t)
        if cross.any():
            idx = np.nonzero(cross)[0]
            frac = np.clip((e_thr[idx] - hz[idx]) / np.maximum(inc[idx], 1e-300), 0.0, 1.0)
            ti = a + frac * (e - a) + delay
            ember_t[idx] = ti
            ok = (ti <= duration_min) & (ti < t_inv[idx])
            by_urban = (add_u[idx] > 0.5 * add[idx]) if add_u is not None else np.zeros(len(idx), bool)
            n_urb += int(np.sum(ok & by_urban))
            n_wild += int(np.sum(ok & ~by_urban))
            stage.add_seeds(idx[ok], ti[ok])
        hz = new
    t, src = stage.final()
    src = src.copy()
    lr = np.isfinite(ember_t) & (ember_t < tf) & np.isclose(t, ember_t)
    src[lr] = SOURCE_EMBER
    src[(src == SOURCE_FRONT) & ~lr & ~np.isfinite(tf)] = SOURCE_EMBER  # seeded, time shifted
    draw.info = {"ember_ignitions_urban": n_urb, "ember_ignitions_wildland": n_wild,
                 "involved": int(np.sum(np.isfinite(t)))}
    return t, src, lr, landed, draw


def run_ensemble_v2(units, t_front_min, wild_landed, edges, opts: LongRangeOptions, stage,
                    duration_min: float, urban: UrbanSource | None, wind_periods, alpha_factor=None):
    """M v2 members (seeded ``default_rng([seed, m])``). Returns (t (M, n) float32,
    source (M, n) int8, ember_seeded (M, n) bool, p_ember (n,) = share of members in which the
    unit was ember-ignited)."""
    n = len(units)
    m_count = int(opts.members)
    t_all = np.full((m_count, n), np.inf, dtype=np.float32)
    s_all = np.zeros((m_count, n), dtype=np.int8)
    lr_all = np.zeros((m_count, n), dtype=bool)
    for m in range(m_count):
        rng = np.random.default_rng([int(opts.seed), m])
        t, s, lr, _, _ = run_member_v2(units, t_front_min, wild_landed, edges, opts, stage,
                                       duration_min, rng, urban, wind_periods, alpha_factor)
        t_all[m], s_all[m], lr_all[m] = t, s, lr
    return t_all, s_all, lr_all, (lr_all.mean(axis=0) if m_count else np.zeros(n))


# ----------------------------------------------------------------------------- v3 (spec §6.5)


def wind_event_end(times_min, speeds_kmh, stop_kmh: float | None = V3_STOP_WIND_KMH,
                   hold_min: float = 120.0, min_after: float = 180.0) -> float:
    """End of the wind-driven run (minutes): the first time >= ``min_after`` from which the
    10 m wind stays below ``stop_kmh`` for ``hold_min`` [H]. ``inf`` if never (or no rule)."""
    if stop_kmh is None:
        return math.inf
    t = np.asarray(times_min, float)
    v = np.asarray(speeds_kmh, float)
    order = np.argsort(t)
    t, v = t[order], v[order]
    for k in range(len(t)):
        if t[k] < min_after or v[k] >= stop_kmh:
            continue
        j = k
        while j + 1 < len(t) and v[j + 1] < stop_kmh:
            j += 1
        if t[j] - t[k] >= hold_min:
            return float(t[k])
    return math.inf


def link_filter_v3(rng, scale_m: float, cutoff_m: float):
    """Probabilistic building-to-building transmission (v3): a link of separation ``sep``
    passes fire with probability exp(-sep / scale_m) (0 beyond ``cutoff_m``), one draw per link
    per member. Form after Knapp et al. 2021 (Camp: survival rises sharply beyond ~18 m from the
    nearest destroyed structure), Zamanialaei et al. 2025 (spacing is a critical factor) and
    Syphard et al. 2012 (housing arrangement); the scale is fitted [R37]."""
    def f(i, nb, sep):
        sep = np.asarray(sep, float)
        p = np.where(sep <= cutoff_m, np.exp(-np.maximum(sep, 0.0) / scale_m), 0.0)
        return rng.random(len(sep)) < p
    return f


def run_member_v3(units, t_front_min, wild_landed, edges, opts: LongRangeOptions, wind,
                  duration_min: float, rng, urban: UrbanSource | None, wind_periods,
                  burnout_min=None, fb: float = 1.0, alpha_factor=None, stop_min: float = math.inf,
                  extra_filter=None, survival_unit=None):
    """One v3 member: v2 embers (q × ember_factor) + probabilistic links + survival + run end.

    ``wind``: ``WindPeriod`` list for the Hamada timing. ``stop_min``: no building becomes
    involved after it (wind-event end or a documented end of the run). ``units`` should carry a
    neighbour graph to ``opts.link_cutoff_m``. ``extra_filter``: optional link filter combined
    with the probabilistic one (e.g. the vegetation bridge). Returns as ``run_member_v2``.
    """
    n = len(units)
    draw = member_draw(opts, rng, n)
    sub = np.random.default_rng(rng.integers(0, 2**63 - 1))
    s_unit = float(opts.survival) if survival_unit is None else np.asarray(survival_unit, float)
    blocked = sub.random(n) < s_unit  # v4: per-building survival (pre-fire vulnerability)
    pf = link_filter_v3(sub, opts.link_scale_m, opts.link_cutoff_m)
    if extra_filter is not None:
        lf = lambda i, nb, sep: pf(i, nb, sep) & extra_filter(i, nb, sep)  # noqa: E731
    else:
        lf = pf
    end = min(float(duration_min), float(stop_min))
    stage = IncrementalHamada(units, wind, duration_min=end, fb=fb, burnout_min=burnout_min,
                              link_filter=lf, blocked=blocked)
    t, s, lr, landed, draw = run_member_v2(units, t_front_min, wild_landed, edges, opts, stage, end,
                                           rng, urban, wind_periods, alpha_factor, draw=draw)
    draw.info["survivors_drawn"] = int(blocked.sum())
    return t, s, lr, landed, draw


def run_ensemble_v3(units, t_front_min, wild_landed, edges, opts: LongRangeOptions, wind,
                    duration_min: float, urban, wind_periods, burnout_min=None, fb: float = 1.0,
                    alpha_factor=None, stop_min: float = math.inf, extra_filter=None):
    """M v3 members (seeded ``default_rng([seed, m])``); returns as ``run_ensemble_v2``."""
    n = len(units)
    m_count = int(opts.members)
    t_all = np.full((m_count, n), np.inf, dtype=np.float32)
    s_all = np.zeros((m_count, n), dtype=np.int8)
    lr_all = np.zeros((m_count, n), dtype=bool)
    for m in range(m_count):
        rng = np.random.default_rng([int(opts.seed), m])
        t, s, lr, _, _ = run_member_v3(units, t_front_min, wild_landed, edges, opts, wind,
                                       duration_min, rng, urban, wind_periods, burnout_min, fb,
                                       alpha_factor, stop_min, extra_filter)
        t_all[m], s_all[m], lr_all[m] = t, s, lr
    return t_all, s_all, lr_all, (lr_all.mean(axis=0) if m_count else np.zeros(n))


# ----------------------------------------------------------------------------- v4 (spec §6.6)


def survival_v4(s0: float, canopy_30_100=None, year_built=None, b_canopy: float = 0.0,
                b_code: float = 0.0, code_year: int = 2008) -> np.ndarray | float:
    """Per-building survival (v4): logit s = logit s0 + b_canopy (canopy_30_100 − 0.3) + b_code
    [year built >= ``code_year``]. Forms: Knapp et al. 2021 (Camp: canopy cover 30-100 m and
    construction year split survival); Syphard & Keeley 2019 (building-code era); California
    Chapter 7A (2008) [H for the year]. Missing values contribute 0. Coefficients fitted [R39]."""
    if canopy_30_100 is None and year_built is None:
        return s0
    n = len(canopy_30_100) if canopy_30_100 is not None else len(year_built)
    z = np.full(n, math.log(s0 / (1 - s0)))
    if canopy_30_100 is not None:
        c = np.asarray(canopy_30_100, float)
        z += np.where(np.isfinite(c), b_canopy * (c - 0.3), 0.0)
    if year_built is not None:
        y = np.asarray(year_built, float)
        z += np.where(np.isfinite(y) & (y >= code_year), b_code, 0.0)
    return 1.0 / (1.0 + np.exp(-z))


def viirs_burn_times(xs, ys, det_x, det_y, det_t, radius_m: float = 750.0, k: int = 3):
    """Burn minute of source cells from VIIRS detections (v4): the earliest detection time among
    the ``k`` nearest detections within ``radius_m`` (375 m pixels; nan if none) [H]."""
    from scipy.spatial import cKDTree

    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    out = np.full(len(xs), np.nan)
    if len(det_x) == 0:
        return out
    tree = cKDTree(np.column_stack([det_x, det_y]))
    d, i = tree.query(np.column_stack([xs, ys]), k=min(k, len(det_x)), distance_upper_bound=radius_m)
    d = np.atleast_2d(d.T).T if d.ndim == 1 else d
    i = np.atleast_2d(i.T).T if i.ndim == 1 else i
    t = np.where(np.isfinite(d), np.asarray(det_t, float)[np.minimum(i, len(det_t) - 1)], np.inf)
    m = t.min(axis=1)
    out[np.isfinite(m)] = m[np.isfinite(m)]
    return out


def viirs_emit_scale(xs, ys, det_x, det_y, det_frp, gamma: float, frp_ref: float = 50.0,
                     radius_m: float = 750.0) -> np.ndarray:
    """Per-cell emission multiplier (v4) = (mean FRP of detections within ``radius_m`` /
    ``frp_ref`` MW)^gamma; cells with no detection get the fire's median detection FRP [H]."""
    from scipy.spatial import cKDTree

    n = len(xs)
    if gamma == 0 or len(det_x) == 0:
        return np.ones(n)
    tree = cKDTree(np.column_stack([det_x, det_y]))
    frp = np.asarray(det_frp, float)
    med = float(np.median(frp))
    vals = np.full(n, med)
    for j, nb in enumerate(tree.query_ball_point(np.column_stack([xs, ys]), radius_m)):
        if nb:
            vals[j] = float(np.mean(frp[nb]))
    return np.maximum(vals / frp_ref, 1e-3) ** gamma


# ----------------------------------------------------------------------------- v5 (spec §6.7)


def ignition_thresholds_v5(opts: LongRangeOptions, rng, n: int) -> np.ndarray:
    """Per-building ignition thresholds in landed (weighted) model embers (v5): the receptor
    ignites when the brands on it reach ``threshold_g`` (a k-hit Poisson process: the n*-th
    arrival, n* = threshold_g / ember_mass_g), i.e. when the expected count Λ(t) crosses
    G ~ Gamma(n*, 1). Replaces v2-v3's single-hit Exp(1) threshold with p_bed · p_b [H form;
    threshold values from SWUIFT p.8, FSJ104686, Suzuki et al. 2015; spec §6.7]."""
    shape = max(float(opts.threshold_g) / float(opts.ember_mass_g), 1e-6)
    return rng.gamma(shape, 1.0, size=n)


def run_member_v5(units, t_front_min, wild_eff, edges, opts: LongRangeOptions, wind,
                  duration_min: float, rng, urban: UrbanSource | None, wind_periods,
                  burnout_min=None, fb: float = 1.0, alpha_factor=None, stop_min: float = math.inf,
                  extra_filter=None, survival_unit=None):
    """One v5 member (spec §6.7).

    ``wild_eff``: (bins, n) effective wildland embers on each receptor (``landed_v5``: short
    mode + weighted long mode, built with ``opts.n_per_kg``) or None. Urban embers (Himoto,
    flaming, weight 1) add per coupling step as in v2. A building ignites when its effective
    count crosses its Gamma(n*, 1) threshold (``ignition_thresholds_v5``), becomes involved
    42 s + 300 s later, then spreads by short contact links p = exp(−sep / λ_r) (λ_r 6 m,
    cutoff 30 m) with Hamada timing and its own urban embers; ``survival_unit`` (v4 covariates)
    blocks a building from ever becoming involved; no involvement after ``stop_min``.
    Draw order: n_kg (``member_draw``), thresholds, links/survival sub-stream, then v2's.
    Returns as ``run_member_v2``."""
    n = len(units)
    draw = member_draw(opts, rng, n)
    thr = ignition_thresholds_v5(opts, rng, n)
    sub = np.random.default_rng(rng.integers(0, 2**63 - 1))
    s_unit = float(opts.survival) if survival_unit is None else np.asarray(survival_unit, float)
    blocked = sub.random(n) < s_unit
    pf = link_filter_v3(sub, opts.link_scale_m, opts.link_cutoff_m)
    if extra_filter is not None:
        lf = lambda i, nb, sep: pf(i, nb, sep) & extra_filter(i, nb, sep)  # noqa: E731
    else:
        lf = pf
    end = min(float(duration_min), float(stop_min))
    stage = IncrementalHamada(units, wind, duration_min=end, fb=fb, burnout_min=burnout_min,
                              link_filter=lf, blocked=blocked)
    t, s, lr, landed, draw = run_member_v2(units, t_front_min, wild_eff, edges, opts, stage, end,
                                           rng, urban, wind_periods, alpha_factor, draw=draw,
                                           thresholds=thr)
    draw.info["survivors_drawn"] = int(blocked.sum())
    return t, s, lr, landed, draw


def run_ensemble_v5(units, t_front_min, wild_eff, edges, opts: LongRangeOptions, wind,
                    duration_min: float, urban, wind_periods, burnout_min=None, fb: float = 1.0,
                    alpha_factor=None, stop_min: float = math.inf, extra_filter=None,
                    survival_unit=None):
    """M v5 members (seeded ``default_rng([seed, m])``); returns as ``run_ensemble_v2``."""
    n = len(units)
    m_count = int(opts.members)
    t_all = np.full((m_count, n), np.inf, dtype=np.float32)
    s_all = np.zeros((m_count, n), dtype=np.int8)
    lr_all = np.zeros((m_count, n), dtype=bool)
    for m in range(m_count):
        rng = np.random.default_rng([int(opts.seed), m])
        t, s, lr, _, _ = run_member_v5(units, t_front_min, wild_eff, edges, opts, wind,
                                       duration_min, rng, urban, wind_periods, burnout_min, fb,
                                       alpha_factor, stop_min, extra_filter, survival_unit)
        t_all[m], s_all[m], lr_all[m] = t, s, lr
    return t_all, s_all, lr_all, (lr_all.mean(axis=0) if m_count else np.zeros(n))


def two_mode_exceedance(d_m, i_mw_m: float, u10_ms: float, opts: LongRangeOptions | None = None,
                        weighted: bool = True) -> np.ndarray:
    """P(spot distance > d) of the v5 two-mode kernel for one source (downwind distance only):
    short Sardoy mode (10 m wind, truncated at ``short_tail_q``, renormalised) with share
    1 − f_L and the long lognormal with share f_L; ``weighted`` weights the long mode by
    ``long_ignition`` (spot fires rather than landed brands, as Storey et al. 2020 count)."""
    opts = opts or LongRangeOptions.for_model("v5")
    d = np.asarray(d_m, float)
    mu, sg = (float(v) for v in sardoy_lognormal(i_mw_m, u10_ms))
    zc = float(ndtri(opts.short_tail_q))
    cdf_s = np.clip(ndtr((np.log(np.maximum(d, 1e-9)) - mu) / sg) / ndtr(zc), 0.0, 1.0)
    cdf_l = ndtr((np.log(np.maximum(d, 1e-9)) - math.log(opts.long_median_m)) / opts.long_sigma)
    wl = opts.long_fraction * (opts.long_ignition if weighted else 1.0)
    ws = 1.0 - opts.long_fraction
    return (ws * (1.0 - cdf_s) + wl * (1.0 - cdf_l)) / (ws + wl)
