#!/usr/bin/env python3
"""Pre-registered test: do 2025 Alberta fires burn through code 105 "Vegetated Non-Fuel" like
fuel, or avoid it like non-fuel? ([R35])

The PREREG_105 block below was committed and pushed before this script was first run. Do not
edit it after scoring; any change to the design gets a new, dated block.

    python scripts/validation/ab_fuel_105_burn_test.py AB_COG.tif PERIMETERS.shp OUT_DIR
"""

from __future__ import annotations

PREREG_105 = """
PREREG_105 (2026-10-12, written before any 2025 fire was overlaid on code 105)

Question. FireSim maps Alberta Wildfire FBP 2025 code 105 "Vegetated Non-Fuel" to non-fuel, so
modelled fire stops at it. Do observed fires avoid 105 the way they avoid non-fuel, or burn into
it the way they burn into fuel?

Data. Fuel: Alberta Wildfire FBP fuel type raster 2025 (dataset date 2025-07-16), 100 m COG,
systematic 1-in-4 sample of real 100 m cells (every 2nd row and column, 200 m pitch), read in
row windows. Fires: Alberta Historical Wildfire Perimeters (file dated 2026-02-03), YEAR == 2025,
parts dissolved by FIRENUMBER, final mapped area >= 1,000 ha (sum of HECTARES_U). No 2025
perimeter has been overlaid on the fuel grid before this block was committed (the descriptive
analysis, ab_fuel_105_extent.py, used fires up to 2024 only).

Design (use-availability / selection ratio). For each fire: "burned" = sample cells whose
centre is inside the fire's perimeter; "available" = sample cells 200 m to 2,000 m outside it
(Euclidean distance on the 200 m grid) that are not inside any 2025 perimeter. Only mapped
Alberta cells count (codes 0, -1, 32767 dropped). Class groups: V = 105; F = fuel (1-7, 13,
21-23, 31, 6xx); N = 101 non-fuel; W = 102 water. Selection ratio SR_k = share of k among
burned / share of k among available. Primary statistic: R = SR_V / SR_F.

Gate (map timing). The map is dated mid-2025; if Alberta re-typed a 2025 burn before release,
its interior would be over-represented in 105 or 101 (as in 2024 scars). A fire is excluded as
"possibly re-typed" if its burned share of F is below half its available share of F
(SR_F < 0.5). The count of excluded fires is reported; if more than half are excluded the test
is reported as void.

Estimates. Pooled R over all retained fires (cells summed across fires), and the median of
per-fire R over retained fires with >= 25 available 105 sample cells. 95 % CI: percentile bootstrap over fires (2,000 resamples, seed
20261012) of the pooled R.

Decision rule (fixed now).
- R CI upper bound < 0.5: fires avoid 105 at least twice as strongly as fuel -> keep 105 as
  non-fuel (the barrier behaviour is supported).
- R CI lower bound > 0.8: fires burn 105 nearly as readily as fuel -> 105 as non-fuel is not
  supported; map it to a fuel type or make it configurable (follow-up decision, owner informed).
- Otherwise: inconclusive -> keep the current mapping, record the estimate and its width.
Controls (reported, not part of the rule): SR_W and SR_N are expected well below SR_F; if
SR_W >= SR_F the design is not discriminating and the result is reported as void.

Known biases, stated now. (1) Fires stop at barriers, so rings are enriched in whatever stopped
them (suppression lines, cropland, water): this biases SR of any barrier class low, which is
the effect measured, but also attributes suppression success at cropland edges to the class.
(2) Final perimeters include unburned islands, biasing every SR toward 1. (3) 105 is mostly
cropland and recent burns (descriptive analysis); the test does not separate them. (4) One
season, fires are spatially clustered; the bootstrap is over fires, not cells.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pyogrio
import rasterio
from rasterio import features
from rasterio.transform import Affine
from rasterio.windows import Window
from scipy import ndimage

STEP = 2
OUTSIDE = (0, -1, 32767)
MIN_HA = 1000.0
RING = (200.0, 2000.0)
SEED = 20261012


def read_sample(path):
    with rasterio.open(path) as ds:
        h, w = ds.height, ds.width
        parts = []
        for r0 in range(0, h, 512):  # 512 is even, so every block starts on a kept row
            a = ds.read(1, window=Window(0, r0, w, min(512, h - r0)))
            parts.append(a[::STEP, ::STEP])
        t = ds.transform
    return np.concatenate(parts, axis=0), Affine(t.a * STEP, 0, t.c, 0, t.e * STEP, t.f)


def groups(codes):
    gV = codes == 105
    gF = np.isin(codes, (1, 2, 3, 4, 5, 6, 7, 13, 21, 22, 23, 31)) | ((codes >= 600) & (codes < 700))
    gN = codes == 101
    gW = codes == 102
    return {"V": gV, "F": gF, "N": gN, "W": gW}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cog"); ap.add_argument("perimeters"); ap.add_argument("out")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    codes, tr = read_sample(a.cog)
    mapped = ~np.isin(codes, OUTSIDE)
    gr = groups(codes)

    per = pyogrio.read_dataframe(a.perimeters, columns=["FIRENUMBER", "HECTARES_U", "YEAR"],
                                 where="YEAR = 2025")
    per = per[per.geometry.notna()]
    fires = per.dissolve(by="FIRENUMBER", aggfunc={"HECTARES_U": "sum"})
    fires = fires[fires.HECTARES_U >= MIN_HA]
    any2025 = features.rasterize(((geom, 1) for geom in per.geometry), out_shape=codes.shape,
                                 transform=tr, fill=0, dtype="uint8").astype(bool)

    rows = []
    for fid, f in fires.iterrows():
        minx, miny, maxx, maxy = f.geometry.bounds
        pad = RING[1] + 400
        c0 = max(int((minx - pad - tr.c) // tr.a), 0); c1 = min(int((maxx + pad - tr.c) // tr.a) + 1, codes.shape[1])
        r0 = max(int((tr.f - maxy - pad) // -tr.e), 0); r1 = min(int((tr.f - miny + pad) // -tr.e) + 1, codes.shape[0])
        if c1 <= c0 or r1 <= r0:
            continue
        sub_tr = Affine(tr.a, 0, tr.c + c0 * tr.a, 0, tr.e, tr.f + r0 * tr.e)
        shape = (r1 - r0, c1 - c0)
        inside = features.rasterize([(f.geometry, 1)], out_shape=shape, transform=sub_tr,
                                    fill=0, dtype="uint8").astype(bool)
        d = ndimage.distance_transform_edt(~inside) * tr.a
        m = mapped[r0:r1, c0:c1]
        burned = inside & m
        avail = (d >= RING[0]) & (d <= RING[1]) & m & ~any2025[r0:r1, c0:c1]
        rec = {"fire": fid, "ha": float(f.HECTARES_U), "n_burned": int(burned.sum()),
               "n_avail": int(avail.sum())}
        for k, gm in gr.items():
            s = gm[r0:r1, c0:c1]
            rec[f"b_{k}"] = int((s & burned).sum()); rec[f"a_{k}"] = int((s & avail).sum())
        rows.append(rec)

    def sr(r, k):
        pb = r[f"b_{k}"] / max(r["n_burned"], 1); pa = r[f"a_{k}"] / max(r["n_avail"], 1)
        return pb / pa if pa > 0 else np.nan

    for r in rows:
        for k in "VFNW":
            r[f"SR_{k}"] = sr(r, k)
        r["gate_pass"] = bool(r["SR_F"] >= 0.5) if np.isfinite(r["SR_F"]) else False
        r["R"] = r["SR_V"] / r["SR_F"] if r["SR_F"] and np.isfinite(r["SR_V"]) else np.nan
    kept = [r for r in rows if r["gate_pass"]]

    def pooled(rs):
        tot = {k: sum(r[k] for r in rs) for k in rs[0] if k.startswith(("b_", "a_", "n_"))}
        out_ = {}
        for k in "VFNW":
            pb = tot[f"b_{k}"] / tot["n_burned"]; pa = tot[f"a_{k}"] / tot["n_avail"]
            out_[f"SR_{k}"] = pb / pa if pa else float("nan")
        out_["R"] = out_["SR_V"] / out_["SR_F"]
        return out_

    res = {"prereg": "PREREG_105", "n_fires": len(rows), "n_kept": len(kept),
           "excluded": [r["fire"] for r in rows if not r["gate_pass"]]}
    if not kept or len(kept) * 2 < len(rows):
        res["verdict"] = "void (gate: more than half the fires possibly re-typed)"
    else:
        P = pooled(kept)
        rng = np.random.default_rng(SEED)
        boots = []
        for _ in range(2000):
            idx = rng.integers(0, len(kept), len(kept))
            boots.append(pooled([kept[i] for i in idx])["R"])
        lo, hi = np.nanpercentile(boots, [2.5, 97.5])
        per_fire = [r["R"] for r in kept if r["a_V"] >= 25 and np.isfinite(r["R"])]
        res |= {"pooled": P, "R_ci95": [float(lo), float(hi)],
                "median_per_fire_R": float(np.median(per_fire)) if per_fire else None,
                "n_fires_per_fire_R": len(per_fire)}
        if P["SR_W"] >= P["SR_F"]:
            verdict = "void (control: water selected as much as fuel)"
        elif hi < 0.5:
            verdict = "keep 105 as non-fuel (fires avoid 105 at least twice as strongly as fuel)"
        elif lo > 0.8:
            verdict = "105 as non-fuel not supported (fires burn 105 nearly as readily as fuel)"
        else:
            verdict = "inconclusive (keep current mapping)"
        res["verdict"] = verdict
    res["fires"] = rows
    (out / "burn_test_105.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps({k: v for k, v in res.items() if k != "fires"}, indent=1, default=float))
    figure(res, out)


def figure(res, out):
    if "pooled" not in res:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    kept = [r for r in res["fires"] if r["gate_pass"]]
    labs = {"F": "fuel (FBP types)", "V": "105 vegetated non-fuel", "N": "101 non-fuel", "W": "102 water"}
    fig, ax = plt.subplots(figsize=(7, 3.6))
    for i, k in enumerate("FVNW"):
        vals = [r[f"SR_{k}"] for r in kept if np.isfinite(r[f"SR_{k}"])]
        ax.scatter(np.full(len(vals), i) + np.random.default_rng(i).uniform(-0.15, 0.15, len(vals)),
                   vals, s=12, alpha=0.6, color="#555555")
        ax.scatter([i], [res["pooled"][f"SR_{k}"]], s=90, marker="D", color="#d95f02", zorder=3)
    ax.axhline(1, color="k", lw=0.6, ls=":")
    ax.set_xticks(range(4)); ax.set_xticklabels([labs[k] for k in "FVNW"], fontsize=8)
    ax.set_yscale("symlog", linthresh=0.1); ax.set_ylabel("selection ratio (burned / available share)")
    ax.set_title(f"2025 Alberta fires >= 1,000 ha (n={len(kept)}): pooled (diamond) and per fire\n"
                 f"R = SR(105)/SR(fuel) = {res['pooled']['R']:.2f}, 95 % CI {res['R_ci95'][0]:.2f}-{res['R_ci95'][1]:.2f}",
                 fontsize=9)
    fig.tight_layout(); fig.savefig(out / "fig5_class105_burn_test_2025.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()
