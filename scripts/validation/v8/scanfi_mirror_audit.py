"""Compare the Alberta sparse-mirror pixels a fire read (its SCANFI window) with the local national copy."""
import json, glob
import numpy as np, rasterio
from v8 import inputs as I, common as C
NAT = C.DATA / "raw" / "scanfi_v2_national"
res = []
for m in glob.glob(str(C.DATA / "fires" / "*" / "meta.json")):
    d = json.loads(open(m).read()); sc = d.get("scanfi_cache") or {}
    lays = [l for l, v in sc.items() if v == "alberta_mirror"]
    if not lays: continue
    g = d["grid"]; r0, r1, c0, c1 = d["scanfi_window"]; e = d["scanfi_epoch"]
    gw = dict(g, x0=g["x0"] + c0 * g["cell"], y1=g["y1"] - r0 * g["cell"], nx=c1 - c0, ny=r1 - r0)
    for l in lays:
        with rasterio.open(I.SCANFI_MIRROR / str(e) / f"{l}.tif") as M, rasterio.open(NAT / f"SCANFI_{l}_{e}_v2_20260119.tif") as N:
            w = I._scanfi_window(M, gw); a = M.read(1, window=w, boundless=True, fill_value=M.nodata)
            bnd = rasterio.windows.bounds(w, M.transform)
            wn = rasterio.windows.from_bounds(*bnd, transform=N.transform)
            wn = rasterio.windows.Window(round(wn.col_off), round(wn.row_off), a.shape[1], a.shape[0])
            n = N.read(1, window=wn, boundless=True, fill_value=N.nodata)
            filled = a != M.nodata
            res.append(dict(fire=d["fire_key"], layer=l, epoch=e, filled=int(filled.sum()), differing_filled=int((a[filled] != n[filled]).sum())))
bad = [r for r in res if r["differing_filled"]]
print("mirror layer-reads", len(res), "fires", len({r["fire"] for r in res}), "differing", len(bad))
for b in bad: print(b)
json.dump(res, open(str(C.DATA / "logs" / "scanfi_mirror_audit_2026-10-31.json"), "w"), indent=1)
