"""Building units: one node per footprint, with its own state, and a neighbour graph.

Specification: ``docs/structure-spread-spec.md`` §2. Each footprint becomes a unit with an id,
centroid, footprint polygon (local metres), area, square-equivalent size and its edge-to-edge
separation to every neighbour within ``neighbour_cutoff_m``.

Why units and not grid cells: structure-to-structure spread on a grid converges only when the
cell size is at most half the structure size and half the separation, and each structure must
be treated as a single unit (Qin et al. 2026, Fire Safety J. 162: 104686, §3.4, §4, §6,
pp.4, 7-9). Edmonton side yards are a few metres wide; a converged grid would need 1-3 m cells.

The square-equivalent size ``sqrt(area)`` follows Hamada's square-plan assumption (Himoto &
Tanaka 2008, Fire Safety J. 43: 477-494, p.25 of the author manuscript); using it for
irregular footprints is a FireSim choice.

Footprints come in as shapely geometries in (lng, lat), as ``BuildingIndex`` returns them.
Everything is computed in an equirectangular local frame in metres centred on the run area.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

M_PER_DEG_LAT = 111320.0
DEFAULT_NEIGHBOUR_CUTOFF_M = 30.0  # spec §4.4: FireSim heuristic, owner to confirm


@dataclass(frozen=True)
class LocalFrame:
    """Equirectangular frame: x east, y north, metres from (lat0, lng0)."""

    lat0: float
    lng0: float

    @property
    def m_per_deg_lng(self) -> float:
        return M_PER_DEG_LAT * math.cos(math.radians(self.lat0))

    def to_local(self, lat, lng):
        """(lat, lng) degrees -> (x, y) metres."""
        return (
            (np.asarray(lng, dtype=float) - self.lng0) * self.m_per_deg_lng,
            (np.asarray(lat, dtype=float) - self.lat0) * M_PER_DEG_LAT,
        )

    def to_latlng(self, x, y):
        """(x, y) metres -> (lat, lng) degrees."""
        return (
            self.lat0 + np.asarray(y, dtype=float) / M_PER_DEG_LAT,
            self.lng0 + np.asarray(x, dtype=float) / self.m_per_deg_lng,
        )


@dataclass
class StructureUnits:
    """Building units and their neighbour graph (compressed sparse rows, both directions).

    Unit ``i``'s neighbours are ``indices[indptr[i]:indptr[i + 1]]`` at edge-to-edge
    separations ``separation_m[indptr[i]:indptr[i + 1]]`` (metres, 0 for touching footprints).
    """

    frame: LocalFrame
    footprints: np.ndarray  # shapely geometries, local metres
    x: np.ndarray  # centroid, local metres
    y: np.ndarray
    lat: np.ndarray  # centroid, degrees
    lng: np.ndarray
    area_m2: np.ndarray
    size_m: np.ndarray  # sqrt(area): side of the equal-area square
    nearest_separation_m: np.ndarray  # inf when no neighbour within the cutoff
    neighbour_cutoff_m: float
    indptr: np.ndarray
    indices: np.ndarray
    separation_m: np.ndarray

    def __len__(self) -> int:
        return len(self.x)

    @property
    def ids(self) -> np.ndarray:
        return np.arange(len(self), dtype=np.int64)

    @property
    def n_edges(self) -> int:
        """Number of neighbour pairs (each pair once)."""
        return len(self.indices) // 2

    def neighbours(self, i: int) -> tuple[np.ndarray, np.ndarray]:
        """(neighbour ids, separations in metres) of unit ``i``."""
        a, b = self.indptr[i], self.indptr[i + 1]
        return self.indices[a:b], self.separation_m[a:b]


def _empty(frame: LocalFrame, cutoff: float) -> StructureUnits:
    z = np.zeros(0)
    return StructureUnits(
        frame=frame, footprints=np.zeros(0, dtype=object), x=z, y=z, lat=z, lng=z,
        area_m2=z, size_m=z, nearest_separation_m=z, neighbour_cutoff_m=cutoff,
        indptr=np.zeros(1, dtype=np.int64), indices=np.zeros(0, dtype=np.int64),
        separation_m=z,
    )


def build_units(
    footprints,
    *,
    neighbour_cutoff_m: float = DEFAULT_NEIGHBOUR_CUTOFF_M,
    bbox: tuple[float, float, float, float] | None = None,
    frame: LocalFrame | None = None,
) -> StructureUnits:
    """Building units from footprints, clipped to the run area.

    Args:
        footprints: shapely Polygons / MultiPolygons in (lng, lat). Invalid ones are repaired
            with ``make_valid``; empty or non-areal ones are dropped.
        neighbour_cutoff_m: largest edge-to-edge separation kept in the neighbour graph.
        bbox: ``(lat_min, lat_max, lng_min, lng_max)``: keep footprints whose centroid is
            inside (the run area). ``None`` keeps all.
        frame: local metric frame; default centred on the kept footprints.

    Returns:
        ``StructureUnits``. Unit ids are positions in the kept list, in input order.
    """
    import shapely

    if neighbour_cutoff_m < 0:
        raise ValueError("neighbour_cutoff_m must be >= 0")
    geoms = np.asarray(list(footprints), dtype=object)
    if len(geoms) == 0:
        return _empty(frame or LocalFrame(0.0, 0.0), neighbour_cutoff_m)

    ok = ~(shapely.is_missing(geoms) | shapely.is_empty(geoms))
    geoms = geoms[ok]
    bad = ~shapely.is_valid(geoms)
    if bad.any():
        # make_valid can return collections; keep the areal part
        geoms[bad] = [_areal(g) for g in shapely.make_valid(geoms[bad])]
    geoms = geoms[~shapely.is_empty(geoms) & (shapely.area(geoms) > 0)]

    cent = shapely.centroid(geoms)
    clng, clat = shapely.get_x(cent), shapely.get_y(cent)
    if bbox is not None:
        lat_min, lat_max, lng_min, lng_max = bbox
        keep = (clat >= lat_min) & (clat <= lat_max) & (clng >= lng_min) & (clng <= lng_max)
        geoms, clat, clng = geoms[keep], clat[keep], clng[keep]
    if len(geoms) == 0:
        return _empty(frame or LocalFrame(0.0, 0.0), neighbour_cutoff_m)

    if frame is None:
        frame = LocalFrame(float((clat.min() + clat.max()) / 2), float((clng.min() + clng.max()) / 2))
    mlng, mlat = frame.m_per_deg_lng, M_PER_DEG_LAT

    def _to_local(coords):
        return np.column_stack([(coords[:, 0] - frame.lng0) * mlng, (coords[:, 1] - frame.lat0) * mlat])

    local = shapely.transform(geoms, _to_local)
    area = shapely.area(local)
    lc = shapely.centroid(local)
    x, y = shapely.get_x(lc), shapely.get_y(lc)
    lat, lng = frame.to_latlng(x, y)

    n = len(local)
    # Candidate pairs from envelopes grown by the cutoff (cheap), then exact distances; each
    # unordered pair is measured once and mirrored.
    tree = shapely.STRtree(local)
    b = shapely.bounds(local)
    c = neighbour_cutoff_m
    i, j = tree.query(shapely.box(b[:, 0] - c, b[:, 1] - c, b[:, 2] + c, b[:, 3] + c))
    up = i < j
    i, j = i[up], j[up]
    sep = shapely.distance(local[i], local[j]) if len(i) else np.zeros(0)
    near = sep <= c
    i, j, sep = i[near], j[near], sep[near]
    i, j, sep = np.concatenate([i, j]), np.concatenate([j, i]), np.concatenate([sep, sep])
    order = np.lexsort((j, i))
    i, j, sep = i[order], j[order], sep[order]
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(indptr, i + 1, 1)
    indptr = np.cumsum(indptr)
    nearest = np.full(n, np.inf)
    if len(i):
        np.minimum.at(nearest, i, sep)

    return StructureUnits(
        frame=frame, footprints=local, x=x, y=y, lat=np.asarray(lat), lng=np.asarray(lng),
        area_m2=area, size_m=np.sqrt(area), nearest_separation_m=nearest,
        neighbour_cutoff_m=float(neighbour_cutoff_m),
        indptr=indptr, indices=j.astype(np.int64), separation_m=np.asarray(sep, dtype=float),
    )


def _areal(g):
    """The polygonal part of a geometry (make_valid can return a collection)."""
    import shapely

    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    if g.geom_type == "GeometryCollection":
        polys = [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")]
        if polys:
            return shapely.union_all(polys)
    return shapely.Polygon()
