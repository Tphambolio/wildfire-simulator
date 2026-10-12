#!/usr/bin/env python3
"""Download the Alberta Wildfire FBP fuel grid COG at image build time (api/Dockerfile).

    python fetch_ab_fuel_grid.py URL SHA256 DEST [LABEL [IF_MISSING]]

Also fetches the Alberta buildings file (LABEL "Alberta buildings"; docs/buildings-alberta.md)
and the Alberta terrain, critical assets and major roads (docs/terrain-alberta.md,
docs/data-sources.md), and the Litestream release used to back up the run-archive index
(docs/run-archive.md). IF_MISSING is the consequence printed when the download fails.

Verifies the SHA-256. On any failure (no release asset yet, network, checksum) it prints a
warning, leaves no file and exits 0, so the image still builds: without the file the API keeps
the City grid only (``FIRESIM_AB_FUEL_GRID_PATH`` points at a missing file; see
docs/fuel-grid-alberta.md §6). Standard library only.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request


_CITY_ONLY = "the API will use the City (Edmonton) data only"


def main(url: str, sha256: str, dest: str, label: str = "Alberta fuel grid",
         if_missing: str = _CITY_ONLY) -> int:
    tmp = dest + ".part"
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        h = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            for block in iter(lambda: r.read(1 << 20), b""):
                h.update(block)
                f.write(block)
        if sha256 and h.hexdigest() != sha256.lower():
            raise ValueError(f"SHA-256 {h.hexdigest()} != expected {sha256}")
        os.replace(tmp, dest)
        print(f"{label}: {dest} ({os.path.getsize(dest):,} bytes, SHA-256 verified)")
    except Exception as exc:  # noqa: BLE001 - never fail the image build over optional data
        if os.path.exists(tmp):
            os.remove(tmp)
        print(f"WARNING: {label} not installed ({exc}); {if_missing}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5, 6):
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
