"""Read-only fuel audit for fires built before the per-fire clip cache (server /vsicurl reads, 2026-10-18/24):
rebuild canada_fuel from the local SCANFI copy into a temp clip dir and compare with the fire's blind inputs.npz code."""
import json, sys
from collections import Counter
from v8 import scanfi_equiv as E
keys = json.load(open(sys.argv[1]))
res = {}
for g, ks in keys.items():
    for k in ks:
        try:
            r = E.check(k)
            d = Counter((x['server'], x['local']) for x in r['fuel_diff_cells'])
            res[k] = dict(group=g, cells=r['fuel_cells'], differing=r['fuel_cells_differing'], top=d.most_common(5))
        except Exception as e:
            res[k] = dict(group=g, error=repr(e)[:200])
        print(g, k, res[k].get('differing', res[k].get('error')), flush=True)
json.dump(res, open('/home/rpas/dev/wildfire/validation-data/v8-pool/logs/scanfi_fuel_audit_prefcache_2026-10-31.json', 'w'), indent=1, default=str)
print('FUEL_AUDIT_DONE', len(res), 'differing', sum(1 for v in res.values() if v.get('differing')), 'errors', sum(1 for v in res.values() if 'error' in v), flush=True)
