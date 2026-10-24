"""Active / held segments of an observed fire perimeter (backlog A1; docs/active-segments.md).

A restart from an observed perimeter (RPAS trace, agency perimeter) should spread only from
the parts of the boundary that are still active. This module classifies the boundary into
segments from heat detections and returns the ``active_edges`` geometry the grid model and the
API already accept, plus a per-segment status for the map.

**Rule (objective, fixed in code):** the perimeter's outer rings are cut into segments of
``segment_m`` (default 250 m) along the boundary. A segment is **active** if at least one
qualifying detection lies within ``radius_m`` (default 1,000 m) of it, measured to the nearest
point of the segment, and was observed in the ``window_h`` hours (default 12 h) up to and
including the observation time T (``T - window_h <= t_det <= T``). Every other segment is
**held**. Detections without a time are not used (the rule needs one). Qualifying detections:

- RPAS thermal hotspots (GeoJSON Point / MultiPoint features with a time property): all;
- VIIRS 375 m (NASA FIRMS CSV: ``latitude``, ``longitude``, ``acq_date``, ``acq_time`` HHMM UTC,
  ``confidence`` l / n / h): nominal and high confidence only, as proposed in [R53] §6 item 1;
  MODIS rows (numeric confidence) are accepted with confidence >= 30 (FIRMS "nominal").

Source priority in the product: manual active edges, then RPAS hotspots, then VIIRS; the first
source given is used alone (``choose_source``). Distances use a local equirectangular
projection about the perimeter centroid (error < 0.1 % over a few tens of km).

References: Schroeder et al. (2014), *Remote Sens. Environ.* 143:85-96 (VIIRS 375 m active
fire product; nominal 375 m pixels, confidence classes); NASA FIRMS attribute definitions
(firms.modaps.eosdis.nasa.gov, "VIIRS I-Band 375 m Active Fire Data"); [R53] Dixie
early-arrival diagnostic §6 item 1 (the 1 km / 12 h proposal).
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

SEGMENT_M = 250.0
RADIUS_M = 1000.0
WINDOW_H = 12.0
MODIS_MIN_CONF = 30.0
#: Accepted names of a hotspot's time property (GeoJSON), first match wins
TIME_KEYS = ("time", "datetime", "observed_at", "timestamp", "acq_datetime", "date")


@dataclass
class Detection:
    lat: float
    lng: float
    time: datetime
    source: str  # "rpas" | "viirs" | "modis"


@dataclass
class Classification:
    source: str
    segments: list[dict] = field(default_factory=list)  # {"coords": [[lng, lat], ...], "active": bool}
    n_detections_used: int = 0
    n_detections_read: int = 0

    @property
    def n_active(self) -> int:
        return sum(1 for s in self.segments if s["active"])

    @property
    def n_held(self) -> int:
        return len(self.segments) - self.n_active

    def active_edges(self) -> dict | None:
        """MultiLineString of the active segments (None when none is active)."""
        lines = [s["coords"] for s in self.segments if s["active"]]
        return {"type": "MultiLineString", "coordinates": lines} if lines else None

    def feature_collection(self) -> dict:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"status": "active" if s["active"] else "held",
                                               "length_m": round(s["length_m"], 1)},
             "geometry": {"type": "LineString", "coordinates": s["coords"]}}
            for s in self.segments]}

    def summary(self) -> dict:
        la = sum(s["length_m"] for s in self.segments if s["active"])
        lt = sum(s["length_m"] for s in self.segments)
        return {"source": self.source, "n_segments": len(self.segments),
                "n_active": self.n_active, "n_held": self.n_held,
                "active_length_km": round(la / 1000.0, 3),
                "held_length_km": round((lt - la) / 1000.0, 3),
                "n_detections_read": self.n_detections_read,
                "n_detections_used": self.n_detections_used}


def _parse_time(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):  # epoch seconds or milliseconds
        x = float(v)
        return datetime.fromtimestamp(x / 1000.0 if x > 1e11 else x, tz=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def hotspots_from_geojson(gj: dict, default_time: datetime | None = None) -> list[Detection]:
    """RPAS hotspots from a GeoJSON FeatureCollection / Feature / Point / MultiPoint.

    Each point needs a time (a property named as in :data:`TIME_KEYS`, ISO 8601, naive = UTC,
    or epoch seconds/ms); ``default_time`` (e.g. a flight time given separately) applies to
    points without one. Points with no usable time are dropped.
    """
    feats = []
    t = gj.get("type")
    if t == "FeatureCollection":
        feats = gj.get("features") or []
    elif t == "Feature":
        feats = [gj]
    elif t in ("Point", "MultiPoint"):
        feats = [{"type": "Feature", "geometry": gj, "properties": {}}]
    else:
        raise ValueError(f"hotspots must be GeoJSON points, not {t!r}")
    out = []
    for f in feats:
        g = f.get("geometry") or {}
        props = f.get("properties") or {}
        when = next((_parse_time(props[k]) for k in TIME_KEYS if k in props), None) or default_time
        if when is None:
            continue
        pts = ([g["coordinates"]] if g.get("type") == "Point"
               else g.get("coordinates", []) if g.get("type") == "MultiPoint" else [])
        for c in pts:
            out.append(Detection(lat=float(c[1]), lng=float(c[0]), time=when, source="rpas"))
    return out


def detections_from_firms_csv(text: str) -> list[Detection]:
    """VIIRS (or MODIS) detections from a NASA FIRMS CSV (archive or NRT download).

    Keeps VIIRS confidence n / h (nominal, high) and MODIS confidence >= 30; ``acq_time`` is
    HHMM UTC. Raises ValueError when the needed columns are missing.
    """
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    cols = {c.strip().lower() for c in (rd.fieldnames or [])}
    need = {"latitude", "longitude", "acq_date", "acq_time"}
    if not need <= cols:
        raise ValueError("FIRMS CSV needs columns latitude, longitude, acq_date, acq_time "
                         f"(found {sorted(cols)})")
    out = []
    for row in rd:
        r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        conf = r.get("confidence", "")
        inst = r.get("instrument", "").upper()
        if conf.lower() in ("l", "n", "h", "low", "nominal", "high"):
            if conf.lower() in ("l", "low"):
                continue
            src = "viirs"
        else:
            try:
                if float(conf) < MODIS_MIN_CONF:
                    continue
            except ValueError:
                pass
            src = "modis" if inst == "MODIS" or conf else "viirs"
        hhmm = r["acq_time"].zfill(4)
        try:
            when = datetime.strptime(f"{r['acq_date']} {hhmm}", "%Y-%m-%d %H%M").replace(
                tzinfo=timezone.utc)
            out.append(Detection(lat=float(r["latitude"]), lng=float(r["longitude"]), time=when,
                                 source=src))
        except ValueError:
            continue
    return out


def _rings(perimeter: dict) -> list[list[list[float]]]:
    t = perimeter.get("type")
    if t == "Polygon":
        return [perimeter["coordinates"][0]]
    if t == "MultiPolygon":
        return [p[0] for p in perimeter["coordinates"]]
    raise ValueError(f"perimeter must be a Polygon or MultiPolygon, not {t!r}")


def _cut(line, step: float) -> list:
    """Split a projected boundary LineString into ``n = round(length / step)`` equal pieces."""
    from shapely.ops import substring

    n = max(1, int(round(line.length / step)))
    d = np.linspace(0.0, line.length, n + 1)
    return [substring(line, a, b) for a, b in zip(d[:-1], d[1:])]


def classify(perimeter: dict, detections: list[Detection], observed_at: datetime,
             source: str, radius_m: float = RADIUS_M, window_h: float = WINDOW_H,
             segment_m: float = SEGMENT_M) -> Classification:
    """Active / held segments of ``perimeter`` from ``detections`` (the module rule)."""
    import shapely

    if observed_at.tzinfo is None:
        raise ValueError("observed_at needs a UTC offset")
    rings = _rings(perimeter)
    allpts = np.array([c[:2] for r in rings for c in r], dtype=float)
    lng0, lat0 = allpts[:, 0].mean(), allpts[:, 1].mean()
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 111320.0

    def proj(lng, lat):
        return (np.asarray(lng) - lng0) * kx, (np.asarray(lat) - lat0) * ky

    t_lo = observed_at - timedelta(hours=window_h)
    used = [d for d in detections if t_lo <= d.time <= observed_at]
    if used:
        dx, dy = proj([d.lng for d in used], [d.lat for d in used])
        pts = shapely.points(np.column_stack([dx, dy]))
    out = Classification(source=source, n_detections_read=len(detections),
                         n_detections_used=0)
    near_any = np.zeros(len(used), dtype=bool)
    for ring in rings:
        r = np.asarray([c[:2] for c in ring], dtype=float)
        if len(r) < 4:
            continue
        if (r[0] != r[-1]).any():
            r = np.vstack([r, r[:1]])
        x, y = proj(r[:, 0], r[:, 1])
        for line in _cut(shapely.LineString(np.column_stack([x, y])), segment_m):
            active = False
            if used:
                close = shapely.distance(pts, line) <= radius_m + 1e-6
                near_any |= close
                active = bool(close.any())
            c = np.asarray(line.coords)
            coords = np.column_stack([lng0 + c[:, 0] / kx, lat0 + c[:, 1] / ky]).tolist()
            out.segments.append({"coords": coords, "active": active,
                                 "length_m": float(line.length)})
    out.n_detections_used = int(near_any.sum())
    return out


def choose_source(manual: dict | None, hotspots: dict | None, firms_csv: str | None) -> str | None:
    """Which classification source applies: manual, then RPAS hotspots, then VIIRS (FIRMS)."""
    if manual is not None:
        return "manual"
    if hotspots is not None:
        return "rpas_hotspots"
    if firms_csv:
        return "viirs_firms"
    return None


def classify_request(perimeter: dict, observed_at: datetime, hotspots: dict | None = None,
                     firms_csv: str | None = None, radius_m: float = RADIUS_M,
                     window_h: float = WINDOW_H, segment_m: float = SEGMENT_M) -> Classification:
    """Classify from the higher-priority detection source given (hotspots, else FIRMS)."""
    if hotspots is not None:
        dets, src = hotspots_from_geojson(hotspots), "rpas_hotspots"
    elif firms_csv:
        dets, src = detections_from_firms_csv(firms_csv), "viirs_firms"
    else:
        raise ValueError("no detections given (hotspots or a FIRMS CSV)")
    return classify(perimeter, dets, observed_at, src, radius_m, window_h, segment_m)


def static_share_from_arrival(minutes: np.ndarray, lat_min: float, lat_max: float,
                              lng_min: float, lng_max: float) -> dict:
    """Modelled run diagnostics from a restart's arrival raster (minutes, -1 = unburned; the
    starting fire has 0): static share of the start perimeter ([R53] Fig. 4 / [R58] method:
    500 m boundary samples, static = advanced <= 1 km) and growth beyond the start (ha)."""
    from firesim.validation.start_strata import StartPerimeterDiagnostic

    m = np.asarray(minutes)
    rows, cols = m.shape
    dy = (lat_max - lat_min) / rows * 111320.0
    dx = (lng_max - lng_min) / cols * 111320.0 * math.cos(math.radians((lat_min + lat_max) / 2))
    start = m == 0
    growth = m > 0
    if not start.any():
        return {"static_share_model": None, "growth_ha": float(growth.sum() * dx * dy / 1e4),
                "n_boundary_samples": 0}
    d = StartPerimeterDiagnostic(start, growth, dx, dy)
    s = d.summary(growth)
    share = s["static_share"]
    return {"static_share_model": None if share != share else round(share, 4),
            "growth_ha": round(float(growth.sum() * dx * dy / 1e4), 2),
            "n_boundary_samples": d.n_samples}
