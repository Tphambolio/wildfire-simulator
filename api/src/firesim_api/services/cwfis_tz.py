"""Noon-LST offset of the nearest CWFIS station in provinces where CWFIS uses more than one.

CWFIS builds each station's daily (noon LST) values from the hour 12 - ``tz_correct`` UTC, a
fixed per-station "UTC-LST" correction "estimated from coordinate ranges and maps", not the
legal clock (``scripts/build_cwfis_tz.py``; report "Alberta permanent UTC-6 switch 2026-10-18",
addendum). Saskatchewan is -6 in the south and east and -7 in the north-west (Waskesiu,
Buffalo Narrows, La Loche, Meadow Lake Park, ...), British Columbia -8 except its eastern
Mountain-time stations (-7), and Ontario, Quebec, Newfoundland and Labrador and Nunavut are
mixed. The boundaries are irregular (Meadow Lake A is -6, Meadow Lake Park -7), so the value is
taken from the nearest station of the same province in the 2026 CWFIS table, not a longitude.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .cwfis_tz_data import STATIONS

# CWFIS province codes; FireSim also accepts the postal ones
_ALIASES = {"SK": "SA", "NL": "NF"}

_BY_PROV: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
for _p, _lat, _lon, _tz in STATIONS:
    _BY_PROV[_p].append((_lat, _lon, _tz))


def cwfis_prov(prov: str | None) -> str | None:
    """CWFIS province code ("SA", "NF", ...) for a CWFIS or postal code."""
    if not prov:
        return None
    p = prov.strip().upper()
    return _ALIASES.get(p, p)


def station_offset(prov: str | None, lat: float | None, lng: float | None) -> int | None:
    """``tz_correct`` (hours, e.g. -7) of the CWFIS station nearest (lat, lng) in ``prov``, if
    ``prov`` is a province with more than one value; else None (use the province table)."""
    rows = _BY_PROV.get(cwfis_prov(prov) or "")
    if not rows or lat is None or lng is None:
        return None
    k = math.cos(math.radians(lat))
    best = min(rows, key=lambda r: (r[0] - lat) ** 2 + ((r[1] - lng) * k) ** 2)
    return best[2]
