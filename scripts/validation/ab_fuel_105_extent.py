#!/usr/bin/env python3
"""Where is Alberta Wildfire code 105 "Vegetated Non-Fuel", and what is it? (descriptive, [R35])

Reads the provincial 100 m COG in row windows and keeps every second row and column (a
systematic 1-in-4 sample of real 100 m cells at a 200 m pitch; no overviews, which are mode
resampled), then:

1. class shares of the sample against the service's attribute-table counts;
2. share of 105 inside a community wildland-urban-interface proxy: cells within 2.4 km of a
   2021 Statistics Canada population centre (outside it), after the 2.4 km maximum buffer of
   Johnston & Flannigan (2018);
3. share of 105 by time since the last mapped fire (Alberta Historical Wildfire Perimeters,
   fire years up to **2024 only**: 2025 fires are kept out for the pre-registered test in
   ``ab_fuel_105_burn_test.py``);
4. land cover of 105 cells in the NTEMS VLCE2 2022 map (random points, forested ecozones only).

    python scripts/validation/ab_fuel_105_extent.py AB_COG.tif PERIMETERS.shp POPCTR.geojson \
        NTEMS_VLCE2.tif OUT_DIR

Writes ``extent_105.json`` and figures to OUT_DIR. Peak memory about 1 GB.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pyogrio
import rasterio
from rasterio import features
from rasterio.transform import Affine
from rasterio.windows import Window
from scipy import ndimage

STEP = 2  # keep every 2nd 100 m cell -> 200 m pitch
OUTSIDE = (0, -1, 32767)
NAMES = {1: "C-1", 2: "C-2", 3: "C-3", 4: "C-4", 5: "C-5", 6: "C-6", 7: "C-7", 13: "D-1/D-2",
         21: "S-1", 22: "S-2", 23: "S-3", 31: "O-1", 101: "Non-fuel", 102: "Water",
         104: "Unclassified", 105: "Vegetated non-fuel"}
VLCE = {20: "Water", 31: "Snow/ice", 32: "Rock/rubble", 33: "Exposed/barren", 40: "Bryoids",
        50: "Shrubs", 80: "Wetland", 81: "Wetland-treed", 100: "Herbs", 210: "Coniferous",
        220: "Broadleaf", 230: "Mixedwood"}
WUI_M = 2400.0


def group(code: np.ndarray) -> np.ndarray:
    """Collapse 6xx to 600 (M-1/M-2)."""
    g = code.copy()
    g[(code >= 600) & (code < 700)] = 600
    return g


def read_sample(path: str):
    with rasterio.open(path) as ds:
        h, w = ds.height, ds.width
        rows = []
        for r0 in range(0, h, 512):
            n = min(512, h - r0)
            a = ds.read(1, window=Window(0, r0, w, n))
            rows.append(a[(-r0) % STEP::STEP, ::STEP] if r0 % STEP else a[::STEP, ::STEP])
        t = ds.transform
    arr = np.concatenate(rows, axis=0)
    # cell centres of the kept 100 m cells: (col*STEP + 0.5)*100
    tr = Affine(t.a * STEP, 0, t.c, 0, t.e * STEP, t.f)  # pixel (i,j) covers the kept cell's 200 m block
    return arr, tr, t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cog"); ap.add_argument("perimeters"); ap.add_argument("popctr")
    ap.add_argument("ntems"); ap.add_argument("out")
    ap.add_argument("--n-ntems", type=int, default=20000)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    codes, tr, tr100 = read_sample(a.cog)
    g = group(codes)
    mapped = ~np.isin(codes, OUTSIDE)
    n_mapped = int(mapped.sum())
    res: dict = {"sample_pitch_m": 200, "n_sample_mapped": n_mapped}

    # 1. shares vs attribute table
    cls, cnt = np.unique(g[mapped], return_counts=True)
    res["sample_share"] = {int(c): float(n) / n_mapped for c, n in zip(cls, cnt)}

    # 2. WUI proxy
    pc = pyogrio.read_dataframe(a.popctr).to_crs(3400)
    inside_pc = features.rasterize(((geom, 1) for geom in pc.geometry), out_shape=g.shape,
                                   transform=tr, fill=0, dtype="uint8").astype(bool)
    dist = ndimage.distance_transform_edt(~inside_pc).astype(np.float32) * 200.0
    wui = (dist > 0) & (dist <= WUI_M) & mapped
    res["wui_proxy"] = {
        "definition": "mapped cells outside, and within 2.4 km of, a 2021 StatCan population centre",
        "n_cells": int(wui.sum()), "area_km2": float(wui.sum()) * 0.04,
        "share_105": float((g[wui] == 105).mean()),
        "share_105_province": float((g[mapped] == 105).mean()),
        "composition": {int(c): float(n) / wui.sum() for c, n in
                        zip(*np.unique(g[wui], return_counts=True))},
        "share_of_all_105_in_wui": float((g[wui] == 105).sum() / (g[mapped] == 105).sum()),
        "share_105_within_1km": float((g[(dist > 0) & (dist <= 1000) & mapped] == 105).mean()),
    }
    del dist

    # 3. time since fire (fires up to 2024)
    per = pyogrio.read_dataframe(a.perimeters, columns=["YEAR"], where="YEAR <= 2024")
    per = per[per.geometry.notna()].sort_values("YEAR")
    last = features.rasterize(((geom, int(y)) for geom, y in zip(per.geometry, per.YEAR)),
                              out_shape=g.shape, transform=tr, fill=0, dtype="uint16",
                              merge_alg=features.MergeAlg.replace)
    bins = [("2024", 2024, 2024), ("2019-2023", 2019, 2023), ("2014-2018", 2014, 2018),
            ("2004-2013", 2004, 2013), ("1984-2003", 1984, 2003), ("before 1984", 1, 1983),
            ("no mapped fire", 0, 0)]
    tsf = {}
    for name, lo, hi in bins:
        m = mapped & (last >= lo) & (last <= hi)
        n = int(m.sum())
        comp = {int(c): float(k) / n for c, k in zip(*np.unique(g[m], return_counts=True))} if n else {}
        tsf[name] = {"n": n, "share_105": comp.get(105, 0.0), "share_101": comp.get(101, 0.0),
                     "share_O1": comp.get(31, 0.0), "composition": comp,
                     "share_of_all_105": float(((g == 105) & m).sum() / (g[mapped] == 105).sum())}
    res["time_since_fire"] = tsf
    res["share_of_105_in_fires_since_1984"] = float(
        ((g == 105) & mapped & (last >= 1984)).sum() / (g[mapped] == 105).sum())

    # 4. NTEMS land cover at random 105 points (and at random mapped points for reference)
    rng = np.random.default_rng(20261012)
    from pyproj import Transformer
    tf = Transformer.from_crs(3400, 3978, always_xy=True)

    def ntems_at(mask, n):
        idx = np.flatnonzero(mask.ravel())
        pick = np.sort(rng.choice(idx, size=min(n, idx.size), replace=False))
        r, c = np.divmod(pick, g.shape[1])
        # centre of the kept 100 m cell: row*STEP, col*STEP in the 100 m grid
        x = tr100.c + (c * STEP + 0.5) * tr100.a
        y = tr100.f + (r * STEP + 0.5) * tr100.e
        X, Y = tf.transform(x, y)
        with rasterio.open(a.ntems) as ds:
            # sort by row so blocks are read in order
            vals = np.array([v[0] for v in ds.sample(zip(X, Y))])
        return vals

    v105 = ntems_at(mapped & (g == 105), a.n_ntems)
    vall = ntems_at(mapped, a.n_ntems)
    def comp(v):
        v = v[(v != 0) & (v != 255)]
        return {"n_in_ntems": int(v.size),
                "share": {VLCE.get(int(c), str(c)): float(k) / v.size
                          for c, k in zip(*np.unique(v, return_counts=True))}}
    res["ntems_105"] = comp(v105) | {"n_drawn": int(v105.size),
                                     "outside_ntems": float(np.isin(v105, (0, 255)).mean())}
    res["ntems_all"] = comp(vall) | {"n_drawn": int(vall.size),
                                     "outside_ntems": float(np.isin(vall, (0, 255)).mean())}

    # 105 by latitude band (northing) for the map text
    rows_n = tr.f + (np.arange(g.shape[0]) + 0.5) * tr.e
    band = {}
    for lo, hi in [(5.3e6, 5.8e6), (5.8e6, 6.1e6), (6.1e6, 6.4e6), (6.4e6, 6.9e6)]:
        rs = (rows_n >= lo) & (rows_n < hi)
        sub_m = mapped[rs]; sub = g[rs]
        band[f"{lo/1e6:.1f}-{hi/1e6:.1f} Mm N"] = float((sub[sub_m] == 105).mean())
    res["share_105_by_northing"] = band

    (out / "extent_105.json").write_text(json.dumps(res, indent=1))
    np.savez_compressed(Path(os.environ.get("AB105_CACHE", "/tmp")) / "ab105_sample_cache.npz",
                        g=g, mapped=mapped, last=last, inside_pc=inside_pc)
    make_figures(g, mapped, last, inside_pc, tr, res, out)
    print(json.dumps({k: res[k] for k in ("wui_proxy", "share_of_105_in_fires_since_1984")}, indent=1))


def make_figures(g, mapped, last, inside_pc, tr, res, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Fig 1: share of 105 per 2 km block
    k = 10
    H, W = (g.shape[0] // k) * k, (g.shape[1] // k) * k
    is105 = (g[:H, :W] == 105).reshape(H // k, k, W // k, k).sum(axis=(1, 3)).astype(float)
    nm = mapped[:H, :W].reshape(H // k, k, W // k, k).sum(axis=(1, 3)).astype(float)
    frac = np.where(nm > 0, is105 / np.maximum(nm, 1), np.nan)
    pcb = inside_pc[:H, :W].reshape(H // k, k, W // k, k).any(axis=(1, 3))
    ext = (tr.c, tr.c + W * tr.a, tr.f + H * tr.e, tr.f)
    fig, ax = plt.subplots(figsize=(6.2, 9.5))
    im = ax.imshow(frac, cmap="viridis", vmin=0, vmax=1, extent=ext, interpolation="nearest")
    pcm = np.ma.masked_where(~pcb, pcb.astype(float))
    ax.imshow(pcm, cmap="autumn", extent=ext, interpolation="nearest", alpha=1.0)
    ax.set_title("Alberta Wildfire FBP grid 2025: share of cells coded 105\n"
                 "'Vegetated Non-Fuel' per 2 km block (red: 2021 population centres)", fontsize=9)
    ax.set_xlabel("Easting, EPSG:3400 (m)"); ax.set_ylabel("Northing (m)")
    ax.tick_params(labelsize=7)
    cb = fig.colorbar(im, ax=ax, shrink=0.6); cb.set_label("share of mapped cells coded 105")
    fig.tight_layout(); fig.savefig(out / "fig1_class105_extent.png", dpi=150); plt.close(fig)

    # Fig 2: 105 share by time since fire
    tsf = res["time_since_fire"]
    names = list(tsf)
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    x = np.arange(len(names)); wdt = 0.27
    ax.bar(x - wdt, [tsf[n]["share_105"] for n in names], wdt, label="105 vegetated non-fuel", color="#1b7837")
    ax.bar(x, [tsf[n]["share_101"] for n in names], wdt, label="101 non-fuel", color="#777777")
    ax.bar(x + wdt, [tsf[n]["share_O1"] for n in names], wdt, label="31 O-1 grass", color="#dfc27d")
    ax.set_xticks(x); ax.set_xticklabels([f"{n}\n(n={tsf[n]['n']/1e3:.0f}k)" for n in names], fontsize=7)
    ax.set_ylabel("share of mapped cells"); ax.set_xlabel("year of the last mapped fire (AB perimeters, to 2024)")
    ax.set_title("Class shares by time since fire (200 m systematic sample)", fontsize=9)
    ax.legend(fontsize=7); fig.tight_layout(); fig.savefig(out / "fig2_class105_time_since_fire.png", dpi=150)
    plt.close(fig)

    # Fig 3: NTEMS composition
    s105 = res["ntems_105"]["share"]; sall = res["ntems_all"]["share"]
    cats = [c for c in VLCE.values() if c in s105 or c in sall]
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    x = np.arange(len(cats))
    ax.bar(x - 0.2, [s105.get(c, 0) for c in cats], 0.4, label=f"code 105 cells (n={res['ntems_105']['n_in_ntems']})", color="#1b7837")
    ax.bar(x + 0.2, [sall.get(c, 0) for c in cats], 0.4, label=f"all mapped cells (n={res['ntems_all']['n_in_ntems']})", color="#999999")
    ax.set_xticks(x); ax.set_xticklabels(cats, rotation=35, ha="right", fontsize=7)
    ax.set_ylabel("share of points"); ax.legend(fontsize=7)
    ax.set_title("NTEMS VLCE2 2022 land cover at random points (forested ecozones only)", fontsize=9)
    fig.tight_layout(); fig.savefig(out / "fig3_class105_ntems.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()
