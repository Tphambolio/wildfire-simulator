"""Structure-to-structure fire spread (opt-in, illustrative; not validated in Canada).

Each building is one unit (a node of a neighbour graph built from its footprint), coupled to
the FBP grid front. Buildings are not rasterised onto the fuel grid: structure spread on a grid
converges only when cells are at most half the building size and spacing (Qin et al. 2026,
Fire Safety J. 162: 104686, pp.7-9). Specification: ``docs/structure-spread-spec.md``.
"""

from firesim.structures.units import LocalFrame, StructureUnits, build_units

__all__ = ["LocalFrame", "StructureUnits", "build_units"]
