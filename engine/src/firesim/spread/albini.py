"""Maximum spotting distance: Albini (1979, 1981, 1983) with Chase (1981) and Morris (1987).

All functions are in SI units and flat terrain. They give the *maximum potential*
spotting distance from a source; Albini's models give no landing distribution, ignition
probability or number of spots (Albini 1979 pp. 4-5).

Sources (USDA Forest Service, Intermountain Research Station):
    Albini F.A. 1979. Spot fire distance from burning trees: a predictive model. GTR INT-56.
    Albini F.A. 1981. Spot fire distance from isolated sources: extensions of a predictive
        model. Res. Note INT-309.
    Chase C.H. 1981. Spot fire distance equations for pocket calculators. Res. Note INT-310.
    Albini F.A. 1983. Potential spotting distance from wind-driven surface fires. Res. Pap. INT-309.
    Chase C.H. 1984. Spotting distance from wind-driven surface fires: extensions of
        equations for pocket calculators. Res. Note INT-346 (corrects Albini 1983 Table 4).
    Morris G.A. 1987. A simple method for computing spotting distances from wind-driven
        surface fires. Res. Note INT-374.

Not implemented: the terrain correction (Albini 1979 eqs F23-F33), burning piles, and the
active crown fire model of Albini, Alexander & Cruz (2012), which the 1979 model explicitly
does not cover; crown fires use the torching-tree model with several trees, which
underestimates their spotting distance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.81  # m/s^2

# Height of the "20-ft" reference wind used by Chase/BehavePlus, and the 1/7 power law
# Albini (1983, p.13) uses to convert the 10 m wind to it: U(6.1 m) = 0.93 U(10 m).
_H20FT = 6.096


def wind_20ft_from_10m(u10: float) -> float:
    """20-ft (6.1 m) wind from the 10 m open wind, 1/7 power law (Albini 1983 p.13). Same units."""
    return u10 * (_H20FT / 10.0) ** (1.0 / 7.0)


def critical_cover_height(z0: float) -> float:
    """Effective cover height h_c (m) for a firebrand lofted to z0 (m) (Albini 1981 p.8)."""
    return max(z0, 1e-6) ** 0.337 - 1.22


def flat_terrain_distance(z0: float, cover_height: float, u20_kmh: float) -> float:
    """Distance (m) a firebrand lofted to z0 (m) drifts before landing, flat terrain.

    Albini 1979 eq F22, X = 21.9 U_H (H/g)^1/2 {0.362 + (z0/H)^1/2 ln(z0/H) / 2}, with the
    treetop wind U_H = 2/3 of the 20-ft wind (Chase 1981 p.2) and H the effective cover
    height max(mean downwind cover height, h_c) (Albini 1981). Equivalent to Chase 1981's
    metric form 1.30e-3 U h*^1/2 {...} km.
    """
    h = max(cover_height, critical_cover_height(z0), 1e-6)
    u_h = (2.0 / 3.0) * u20_kmh / 3.6
    term = 0.362 + 0.5 * math.sqrt(z0 / h) * math.log(z0 / h)
    return max(0.0, 21.9 * u_h * math.sqrt(h / G) * term)


def surface_fire_spot_distance(
    intensity_kw_m: float,
    u10_kmh: float,
    cover_height: float,
    a: float = 322.0,
    b: float = -1.01,
) -> float:
    """Maximum spotting distance (m) from a wind-driven surface fire (Albini 1983, Morris 1987).

    Thermal strength E = I A U10^B (kJ/m; I in kW/m, U10 in m/s), with Morris's (1987)
    fuel-independent A = 322 s, B = -1.01 (fuel and moisture act through I). Firebrands are
    lofted to H = 0.173 E^1/2 (m) and drift X = 2.78 U(H) H^1/2 while lofting, with
    U(H) = U10 (H/10)^1/7 (Albini 1983 eqs 2-4); they then fall as in
    ``flat_terrain_distance``. Morris's fit applies for 20-ft winds of 4.2 mi/h (about
    2 m/s at 10 m) and above; lighter winds are evaluated at 2 m/s.

    ``a``, ``b`` may be set to a fuel model's corrected Albini 1983 coefficients (Chase 1984
    Table 1), e.g. NFFL model 1: a = 545, b = -1.21.
    """
    if intensity_kw_m <= 0.0 or u10_kmh <= 0.0:
        return 0.0
    u10 = max(u10_kmh / 3.6, 2.0)
    energy = intensity_kw_m * a * u10**b
    loft = 0.173 * math.sqrt(energy)
    drift = 2.78 * u10 * (loft / 10.0) ** (1.0 / 7.0) * math.sqrt(loft)
    return flat_terrain_distance(loft, cover_height, wind_20ft_from_10m(u10_kmh)) + drift


# Chase (1981) power-law fits to Albini 1979 Figs. 3-5 (metric: d.b.h. in cm, heights in m).
# Steady flame height h_F = a d^b n^0.4 (Chase 1981 p.5).
_FLAME_HEIGHT = {
    "fir": (3.11, 0.515),  # grand fir, balsam fir
    "spruce": (3.14, 0.451),  # Engelmann spruce, subalpine fir, Douglas-fir, western hemlock
    "pine": (2.58, 0.453),  # ponderosa, lodgepole, white pine
}
# Dimensionless flame duration d_F = c d^e n^-0.2 (Chase 1981 p.5).
_DURATION = {
    "pine_spruce": (16.0, -0.256),  # ponderosa pine, lodgepole pine, Engelmann spruce
    "fir": (13.9, -0.278),  # subalpine fir, Douglas-fir, balsam fir, grand fir, white pine
    "hemlock": (7.95, -0.249),  # western hemlock
}
# Species -> (flame-height group, duration group), Chase 1981 Tables.
SPECIES = {
    "engelmann_spruce": ("spruce", "pine_spruce"),
    "subalpine_fir": ("spruce", "fir"),
    "douglas_fir": ("spruce", "fir"),
    "western_hemlock": ("spruce", "hemlock"),
    "grand_fir": ("fir", "fir"),
    "balsam_fir": ("fir", "fir"),
    "ponderosa_pine": ("pine", "pine_spruce"),
    "lodgepole_pine": ("pine", "pine_spruce"),
    "white_pine": ("pine", "fir"),
}


def torching_tree_spot_distance(
    species: str,
    dbh_cm: float,
    tree_height: float,
    cover_height: float,
    u20_kmh: float,
    n_trees: int = 1,
) -> float:
    """Maximum spotting distance (m) from torching trees (Albini 1979, Chase 1981 fits).

    Args:
        species: key of ``SPECIES`` (Chase's western species groups).
        dbh_cm: diameter at breast height of the torching tree(s).
        tree_height: height of the torching tree(s), m.
        cover_height: mean height of the vegetation downwind, m (half the treetop height
            for broken forest cover, Albini 1979 p.6).
        u20_kmh: 20-ft wind above the vegetation, km/h.
        n_trees: trees torching together as one flame.
    """
    fh_group, dur_group = SPECIES[species]
    a, b = _FLAME_HEIGHT[fh_group]
    c, e = _DURATION[dur_group]
    n = max(int(n_trees), 1)
    h_f = a * dbh_cm**b * n**0.4
    d_f = c * dbh_cm**e * n**-0.2
    ratio = tree_height / h_f
    if ratio >= 1.0:
        z0 = 4.24 * d_f**0.332 * h_f
    elif ratio >= 0.5:
        z0 = 3.64 * d_f**0.391 * h_f
    elif d_f < 3.5:
        z0 = 2.78 * d_f**0.418 * h_f
    else:
        z0 = 4.70 * h_f
    z0 += tree_height / 2.0
    return flat_terrain_distance(z0, cover_height, u20_kmh)


@dataclass(frozen=True)
class StandDefaults:
    """Stand description assumed for an FBP fuel type (not part of FBP; overridable)."""
    cover_height: float  # mean downwind cover height, m
    species: str | None = None  # torching species group; None = no torching trees
    tree_height: float = 0.0  # m
    dbh_cm: float = 0.0


# Assumed stands per FBP fuel type. FBP carries no stand height or tree size, so these are
# modelling assumptions chosen to be typical of each type (ST-X-3 Appendix descriptions):
# boreal spruce mapped to Chase's Engelmann spruce group, jack/lodgepole pine to the
# lodgepole group, red/white pine to the white pine group, balsam fir to the fir group.
STAND_DEFAULTS: dict[str, StandDefaults] = {
    "C1": StandDefaults(4.0, "engelmann_spruce", 8.0, 12.0),  # open lichen woodland: half height
    "C2": StandDefaults(12.0, "engelmann_spruce", 12.0, 15.0),
    "C3": StandDefaults(18.0, "lodgepole_pine", 18.0, 20.0),
    "C4": StandDefaults(8.0, "lodgepole_pine", 8.0, 10.0),
    "C5": StandDefaults(25.0, "white_pine", 25.0, 40.0),
    "C6": StandDefaults(15.0, "lodgepole_pine", 15.0, 20.0),
    "C7": StandDefaults(10.0, "ponderosa_pine", 20.0, 35.0),  # open stands: half height
    "D1": StandDefaults(15.0),
    "D2": StandDefaults(15.0),
    "M1": StandDefaults(18.0, "engelmann_spruce", 18.0, 20.0),
    "M2": StandDefaults(18.0, "engelmann_spruce", 18.0, 20.0),
    "M3": StandDefaults(15.0, "balsam_fir", 15.0, 18.0),
    "M4": StandDefaults(15.0, "balsam_fir", 15.0, 18.0),
    "O1a": StandDefaults(0.3),
    "O1b": StandDefaults(0.5),
    "S1": StandDefaults(1.0),
    "S2": StandDefaults(1.0),
    "S3": StandDefaults(1.5),
}


def torching_tree_count(cfb: float) -> int:
    """Trees torching together, assumed to grow with crown fraction burned.

    Not part of Albini's model (it takes n as an input): 1 tree at CFB 0.1 rising to 10 at
    full crowning. Albini 1979 Fig. 6 covers up to 30 trees.
    """
    return max(1, min(10, round(10.0 * cfb)))


def max_spot_distance(fuel_type: str, hfi_kw_m: float, cfb: float, u10_kmh: float) -> float:
    """Maximum spotting distance (m) for an FBP fire, choosing the source model by CFB.

    Surface fires (CFB < 0.1, or no torching species) use the surface-fire model with the
    fuel type's cover height; torching and crowning fires use the torching-tree model with
    ``torching_tree_count(cfb)`` trees (an underestimate for active crown fires).
    """
    stand = STAND_DEFAULTS.get(fuel_type, StandDefaults(1.0))
    if cfb < 0.1 or stand.species is None:
        return surface_fire_spot_distance(hfi_kw_m, u10_kmh, stand.cover_height)
    return torching_tree_spot_distance(
        stand.species, stand.dbh_cm, stand.tree_height, stand.cover_height,
        wind_20ft_from_10m(u10_kmh), torching_tree_count(cfb),
    )
