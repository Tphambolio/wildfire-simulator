"""Equivalence of the local SCANFI copy with the server clips (PREREG_LR8C A4, before the draw switches).

For fires already built through the NRCan server, each cached per-fire clip (``raw/scanfi_v8``) is re-cut from the
local national file with the same window code, and the fuel array is rebuilt with ``canada_fuel`` reading only the
local copy. Both are compared with what the server run produced: clip values, transform, CRS and nodata, and the
``code`` array of the fire's blind ``inputs.npz``. Only blind inputs are read; no sealed file, detection or outcome.

``python3 -m v8.scanfi_equiv KEY [KEY ...]`` writes ``logs/scanfi_equiv_2026-10-30.json`` and prints one line per fire.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio

from . import common as C
from . import inputs as I


def check(k: str) -> dict:
    d = C.fire_dir(k)
    meta = json.loads((d / "meta.json").read_text())
    rec = I.fire_record(k)
    g = meta["grid"]
    srv = {lay: v for lay, v in meta.get("scanfi_cache", {}).items()}
    e = meta.get("scanfi_epoch")
    out = dict(fire_key=k, year=rec["year"], epoch=e, zones=len(set(meta.get("zone_station", []))),
               server_layers=sorted(l for l, v in srv.items() if v in ("nrcan_server_clip", "cache")))
    real_cache = I.SCANFI_CACHE
    with tempfile.TemporaryDirectory(dir=C.DATA / "raw") as tmp:
        I.SCANFI_CACHE = Path(tmp)
        try:
            m2 = dict(fire_key=k)
            code_local = I.canada_fuel(rec, g, m2)
        finally:
            I.SCANFI_CACHE = real_cache
        clips = []
        for lay in out["server_layers"]:
            a_p = real_cache / str(e) / f"{k.replace(':', '_')}__{lay}.tif"
            b_p = Path(tmp) / str(e) / f"{k.replace(':', '_')}__{lay}.tif"
            if not (a_p.exists() and b_p.exists()):
                clips.append(dict(layer=lay, status="missing", server=a_p.exists(), local=b_p.exists()))
                continue
            with rasterio.open(a_p) as A, rasterio.open(b_p) as B:
                a, b = A.read(1), B.read(1)
                same_geo = (A.transform == B.transform and A.crs == B.crs and A.nodata == B.nodata and a.shape == b.shape)
                ndiff = int((a != b).sum()) if a.shape == b.shape else -1
                clips.append(dict(layer=lay, same_geo=bool(same_geo), cells=int(a.size), cells_differing=ndiff,
                                  local_via=B.tags().get("via")))
        out["clips"] = clips
        out["local_sources"] = m2.get("scanfi_cache")
    code_srv = np.load(d / "inputs.npz")["code"]
    diff = np.argwhere(code_srv != code_local)
    out["fuel_cells"] = int(code_srv.size)
    out["fuel_cells_differing"] = int(len(diff))
    out["fuel_diff_cells"] = [dict(row=int(r), col=int(c), server=int(code_srv[r, c]), local=int(code_local[r, c]))
                              for r, c in diff[:200]]
    out["identical"] = bool(len(diff) == 0 and all(c.get("cells_differing") == 0 and c.get("same_geo") for c in clips))
    return out


if __name__ == "__main__":
    C.guard()
    res = []
    for k in sys.argv[1:]:
        r = check(k)
        res.append(r)
        print(k, r["year"], r["epoch"], "zones", r["zones"], "clips", len(r["clips"]),
              "clip_diff", sum(max(c.get("cells_differing", 0), 0) for c in r["clips"]),
              "fuel_diff", r["fuel_cells_differing"], "IDENTICAL" if r["identical"] else "DIFFERS", flush=True)
    p = C.DATA / "logs" / "scanfi_equiv_2026-10-30.json"
    p.write_text(json.dumps(res, indent=1))
    print("ALL_IDENTICAL" if all(r["identical"] for r in res) else "NOT_ALL_IDENTICAL", len(res), flush=True)
