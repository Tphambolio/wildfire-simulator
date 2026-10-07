#!/usr/bin/env python3
"""Download the Canadian Fire Spread Dataset (CFSDS) daily summaries and day-of-burning rasters.

CFSDS v1.1 (Barber et al. 2024, Sci. Data 11:764; OSF https://osf.io/f48ry/, CC BY 4.0):
per-fire day-of-burning (DOB) rasters (``Fire growth rasters/<year>_fireDOYrasters.zip``,
90 m) and per-fire-day summaries with ERA5-derived weather / FWI
(``Fire growth daily summaries/Firegrowth_groups_v1_1_<year>.zip``). The per-pixel point files
(``Firegrowth_pts``, ~0.2-0.7 GB per year) are not needed and are not downloaded.

Data go to a cache outside the repository (default /home/rpas/dev/wildfire/validation-data/cfsds,
or $FIRESIM_VALIDATION_DATA/cfsds). Never commit them.

Usage:
    python scripts/validation/fetch_cfsds.py [--years 2016 2019] [--root DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import zipfile
from pathlib import Path

OSF_ROOT = "https://api.osf.io/v2/nodes/f48ry/files/osfstorage/"
DEFAULT_ROOT = Path(os.environ.get("FIRESIM_VALIDATION_DATA",
                                   "/home/rpas/dev/wildfire/validation-data")) / "cfsds"


def list_osf(url: str = OSF_ROOT, path: str = "") -> list[tuple[str, int, str]]:
    """(path, size, download url) of every file in the CFSDS OSF storage."""
    out = []
    while url:
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        for f in d["data"]:
            a = f["attributes"]
            if a["kind"] == "folder":
                out += list_osf(f["relationships"]["files"]["links"]["related"]["href"],
                                path + a["name"] + "/")
            else:
                out.append((path + a["name"], a["size"], f["links"]["download"]))
        url = d["links"].get("next")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--years", type=int, nargs="*", help="Years to fetch (default: all)")
    args = ap.parse_args()

    raw = args.root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    files = list_osf()
    (args.root / "osf_manifest.tsv").write_text(
        "".join(f"{p}\t{s}\t{u}\n" for p, s, u in sorted(files)))
    for path, size, url in files:
        name = Path(path).name
        if "Firegrowth_pts" in path or path.startswith("R Code"):
            continue
        if args.years and not any(str(y) in name for y in args.years) and name.endswith(".zip"):
            continue
        dest = raw / name
        if not dest.exists() or dest.stat().st_size != size:
            print(f"downloading {path} ({size / 1e6:.1f} MB)")
            urllib.request.urlretrieve(url, dest)
        if name.endswith(".zip"):
            sub = "doy" if "DOYrasters" in name else "groups"
            with zipfile.ZipFile(dest) as z:
                z.extractall(args.root / sub)
    print(f"CFSDS cache ready under {args.root}")


if __name__ == "__main__":
    main()
