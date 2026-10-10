"""Combustible-roof scenario for the ember stage (opt-in; **a scenario, not observed roofs**).

Specification: ``docs/structure-spread-spec.md`` §6.2 (structure step 3, 2026-10-10). No open
data holds roof covering for Edmonton or Jasper (step-3 data report §4; roof report §2), so the
roof term is a pre-specified scenario, as the roof report ranks first (§6, rank 1):

- ``combustible_roof_share`` s ∈ {0, 0.05, 0.15, 0.30} (default 0 = the model without a roof
  term). Each building is assigned a combustible roof with probability s, independently, from
  a hash of the run seed and the building's id (or its centroid when there is no id). The
  assignment is therefore deterministic for a seed and does not depend on which buildings a
  run builds or in what order [H]. Random placement has no spatial skill: it gives an
  envelope of outcomes, not a map of roofs.
- A combustible-roof building ignites from embers at a lower pooled ember load:
  ψ*_roof = k · ψ*(v_air), with ψ* the FSJ104686 eq 1 threshold for pine treated wood (PTW).
  **k = 0.375**: DeBeer (2023, UMD PhD, Tables 10-1 and 10-4, pp.202-205) [P] exposed
  horizontally mounted Western Red Cedar (WRC, the wood of cedar shakes) and PTW to glowing
  firebrand piles at 0.06 and 0.16 g/cm² in a wind tunnel (n = 9 per condition). At 1.4 m/s
  the lighter 0.06 g/cm² pile ignited WRC (P = 0.11) at least as often as the heavier
  0.16 g/cm² pile ignited PTW (P = 0.05, 5 cm-edge equivalent; Table 10-4). WRC therefore
  needs at most 0.06 / 0.16 = 0.375 of PTW's pile loading for the same ignition probability
  at that airflow. Reading that bound as a factor on ψ* is FireSim's [H] (WRC boards are not
  a shake roof assembly; FireSim's v_air is mostly below DeBeer's 0.9-2.7 m/s; small samples).
  Sensitivity: k = 0.5 (the roof report's placeholder, §6 rank 1 [H]) and k = 1 (no roof
  effect). No roof data from Jasper or CAL FIRE set k.
"""

from __future__ import annotations

import numpy as np

ROOF_SHARES = (0.0, 0.05, 0.15, 0.30)
ROOF_PSI_FACTOR = 0.375  # DeBeer 2023 Tables 10-1, 10-4 (pp.202-205) [P]; as a ψ* factor [H]
ROOF_PSI_FACTOR_SENSITIVITY = (0.5, 1.0)
ROOF_SOURCE = ("DeBeer 2023 (UMD PhD) Tables 10-1, 10-4, pp.202-205: WRC vs PTW firebrand-pile "
               "ignition; factor on psi* is FireSim's [H]")

_M1 = np.uint64(0xBF58476D1CE4E5B9)
_M2 = np.uint64(0x94D049BB133111EB)
_GOLD = np.uint64(0x9E3779B97F4A7C15)


def _splitmix64(z: np.ndarray) -> np.ndarray:
    z = z.astype(np.uint64)
    with np.errstate(over="ignore"):
        z = z + _GOLD
        z = (z ^ (z >> np.uint64(30))) * _M1
        z = (z ^ (z >> np.uint64(27))) * _M2
        return z ^ (z >> np.uint64(31))


def building_keys(units) -> np.ndarray:
    """A stable 64-bit key per unit: its building id when known, else its centroid rounded
    to 1e-6° (~0.1 m), which does not depend on the order units were built in."""
    ids = getattr(units, "building_id", None)
    lat = np.rint(np.asarray(units.lat, dtype=float) * 1e6).astype(np.int64)
    lng = np.rint(np.asarray(units.lng, dtype=float) * 1e6).astype(np.int64)
    geo = ((lat.astype(np.uint64) & np.uint64(0xFFFFFFFF)) << np.uint64(32)) | (
        lng.astype(np.uint64) & np.uint64(0xFFFFFFFF))
    geo = geo | np.uint64(1 << 63)  # never collides with a small non-negative id
    if ids is None:
        return geo
    ids = np.asarray(ids, dtype=np.int64)
    return np.where(ids >= 0, ids.astype(np.uint64), geo)


def assign_combustible_roofs(keys, share: float, seed: int) -> np.ndarray:
    """Which buildings get a combustible roof in the scenario (deterministic for ``seed``)."""
    if not 0.0 <= share <= 1.0:
        raise ValueError("combustible_roof_share must be in [0, 1]")
    keys = np.asarray(keys, dtype=np.uint64)
    if share <= 0.0 or len(keys) == 0:
        return np.zeros(len(keys), dtype=bool)
    s = _splitmix64(np.full(1, np.uint64(int(seed) & 0xFFFFFFFFFFFFFFFF)))[0]
    u = _splitmix64(keys ^ s).astype(np.float64) / 18446744073709551616.0  # 2**64
    return u < share


def roof_label(share: float) -> str:
    return f"scenario: {round(100 * share):g} % combustible roofs (not observed roofs)"


def psi_factors(combustible: np.ndarray, k: float = ROOF_PSI_FACTOR) -> np.ndarray:
    """Per-unit factor on ψ*: k on combustible roofs, 1 elsewhere."""
    if not 0.0 < k <= 1.0:
        raise ValueError("the roof psi factor must be in (0, 1]")
    return np.where(np.asarray(combustible, dtype=bool), float(k), 1.0)
