"""For pre-cache fires: rebuild the fuel with the pre-cache read method (warp the national file straight to the run
grid, full grid or restored-cell window), from the LOCAL copy, and compare with inputs.npz code. A match means the
server reads of that build returned the national values; a residual difference is listed with its cells."""
import json, sys
import numpy as np
from rasterio.enums import Resampling
from v8 import inputs as I, common as C
from scripts.fuelgrid import backcast as B
NAT = C.DATA / "raw" / "scanfi_v2_national"

def fuel(k, window: bool):
    d = C.fire_dir(k); meta = json.loads((d / "meta.json").read_text()); rec = I.fire_record(k); g = meta["grid"]
    orig, orig_warp = I.scanfi_local, I.warp_to_grid
    I.scanfi_local = lambda layer, epoch, gw, fire_key, m, need: str(NAT / f"SCANFI_{layer}_{epoch}_v2_20260119.tif")
    if not window:  # full-grid read: warp the national file over the whole run grid, then take the window
        def warp_full(src, gw, rs=Resampling.nearest, *a, **kw):
            if "scanfi_v2_national" not in str(src):
                return orig_warp(src, gw, rs, *a, **kw)
            full = orig_warp(src, g, rs, *a, **kw)
            c0 = int(round((gw["x0"] - g["x0"]) / g["cell"])); r0 = int(round((g["y1"] - gw["y1"]) / g["cell"]))
            return full[r0:r0 + gw["ny"], c0:c0 + gw["nx"]]
        I.warp_to_grid = warp_full
    try:
        code = I.canada_fuel(rec, g, {})
    finally:
        I.scanfi_local, I.warp_to_grid = orig, orig_warp
    return code, np.load(d / "inputs.npz")["code"]


res = {}
for k in json.load(open(sys.argv[1])):
    out = {}
    for w in (True, False):
        loc, srv = fuel(k, w)
        diff = np.argwhere(loc != srv)
        out["window" if w else "full_grid"] = dict(differing=int(len(diff)),
            cells=[[int(r), int(c), int(srv[r, c]), int(loc[r, c])] for r, c in diff[:50]])
    res[k] = out
    print(k, {m: v["differing"] for m, v in out.items()}, flush=True)
json.dump(res, open(sys.argv[2], "w"), indent=1)
print("OLD_METHOD_DONE", len(res), flush=True)
