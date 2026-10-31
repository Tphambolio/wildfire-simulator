"""Audit every cached per-fire SCANFI clip (raw/scanfi_v8) against the same window of the local national copy."""
import glob, json, sys, os
import numpy as np, rasterio
from rasterio.windows import from_bounds
R='/home/rpas/dev/wildfire/validation-data/v8-pool/raw'
out=[]
for p in sorted(glob.glob(f'{R}/scanfi_v8/*/*.tif')):
    ep=p.split('/')[-2]; base=os.path.basename(p)[:-4]; key,layer=base.split('__')
    nat=f'{R}/scanfi_v2_national/SCANFI_{layer}_{ep}_v2_20260119.tif'
    with rasterio.open(p) as A, rasterio.open(nat) as N:
        a=A.read(1)
        w=from_bounds(*A.bounds, transform=N.transform)
        w=rasterio.windows.Window(round(w.col_off),round(w.row_off),a.shape[1],a.shape[0])
        n=N.read(1, window=w, boundless=True, fill_value=N.nodata)
        nd=int((a!=n).sum())
        out.append(dict(clip=os.path.relpath(p,R), key=key, layer=layer, epoch=ep, cells=int(a.size), differing=nd,
                        server_all_nodata=bool((a==A.nodata).all()), local_all_nodata=bool((n==N.nodata).all()),
                        via=A.tags().get('via','NRCan server')))
bad=[o for o in out if o['differing']]
json.dump(dict(n=len(out), differing=bad), open('/home/rpas/dev/wildfire/validation-data/v8-pool/logs/scanfi_clip_audit_2026-10-31.json','w'), indent=1)
print('clips', len(out), 'differing', len(bad))
for b in bad: print(b)
