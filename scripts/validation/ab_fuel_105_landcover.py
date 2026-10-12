#!/usr/bin/env python3
"""What land cover sits under Alberta Wildfire code 105 "Vegetated Non-Fuel"? ([R35], descriptive)

Uses the 200 m systematic sample written by ``ab_fuel_105_extent.py`` (``$AB105_CACHE``) and
rasterises the ABMI Wall-to-Wall Land Cover 2010 polygons (province-wide, includes the
agricultural White Area that NTEMS does not cover) onto the same 200 m cells (cell centre),
reading the 998,031 polygons in chunks. Cross-tabulates code 105 (and, for reference, 31 O-1)
against the ABMI classes: everywhere, in the community WUI proxy, and outside mapped fires
since 1984. Also splits the NTEMS VLCE2 2022 composition of 105 by fire history.

    python scripts/validation/ab_fuel_105_landcover.py ABMI.gdb NTEMS_VLCE2.tif OUT_DIR
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
from scipy import ndimage

ABMI = {20: "Water", 31: "Snow/ice", 32: "Rock/rubble", 33: "Exposed/barren", 34: "Developed",
        50: "Shrubland", 110: "Grassland", 120: "Agriculture", 210: "Coniferous",
        220: "Broadleaf", 230: "Mixedwood"}
VLCE = {20: "Water", 31: "Snow/ice", 32: "Rock/rubble", 33: "Exposed/barren", 40: "Bryoids",
        50: "Shrubs", 80: "Wetland", 81: "Wetland-treed", 100: "Herbs", 210: "Coniferous",
        220: "Broadleaf", 230: "Mixedwood"}
# 200 m sample grid of ab_fuel_105_extent.py (COG origin 115700, 6831800; every 2nd 100 m cell)
TR = Affine(200.0, 0, 115700.0, 0, -200.0, 6831800.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("abmi_gdb"); ap.add_argument("ntems"); ap.add_argument("out")
    ap.add_argument("--chunk", type=int, default=100000)
    a = ap.parse_args()
    out = Path(a.out)
    c = np.load(Path(os.environ.get("AB105_CACHE", "/tmp")) / "ab105_sample_cache.npz")
    g, mapped, last, inside_pc = c["g"], c["mapped"], c["last"], c["inside_pc"]

    lc = np.zeros(g.shape, dtype=np.uint8)
    layer = "Lancover_Polygons_2010"
    n = pyogrio.read_info(a.abmi_gdb, layer=layer)["features"]
    for s in range(0, n, a.chunk):
        df = pyogrio.read_dataframe(a.abmi_gdb, layer=layer, columns=["LC_class"],
                                    skip_features=s, max_features=a.chunk)
        df = df[df.geometry.notna()]
        # classes encoded 1..11 to fit uint8 (_enc), decoded by _dec
        codes = [(geom, int(v)) for geom, v in zip(df.geometry, df.LC_class)]
        enc = ((geom, _enc(v)) for geom, v in codes)
        features.rasterize(enc, out=lc, transform=TR, merge_alg=features.MergeAlg.replace)
        del df, codes
    lcv = _dec(lc)

    dist = ndimage.distance_transform_edt(~inside_pc).astype(np.float32) * 200.0
    wui = (dist > 0) & (dist <= 2400) & mapped
    del dist
    unburned = mapped & ((last == 0) | (last < 1984))
    recent = mapped & (last >= 2019)

    def tab(mask):
        v = lcv[mask]; v = v[v > 0]
        return {"n": int(v.size), "share": {ABMI.get(int(k), str(k)): float(m) / v.size
                                             for k, m in zip(*np.unique(v, return_counts=True))}}

    res = {
        "abmi_105_all": tab(mapped & (g == 105)),
        "abmi_105_wui": tab(wui & (g == 105)),
        "abmi_105_no_fire_since_1984": tab(unburned & (g == 105)),
        "abmi_105_burned_2019_2024": tab(recent & (g == 105)),
        "abmi_O1_all": tab(mapped & (g == 31)),
        "abmi_all": tab(mapped),
    }
    # how 105 splits by ABMI class into: agriculture, grass/shrub, wetland-ish, forest
    r = res["abmi_105_all"]["share"]
    res["share_105_in_agriculture"] = r.get("Agriculture", 0.0)

    # NTEMS split by fire history
    rng = np.random.default_rng(20261012)
    from pyproj import Transformer
    tf = Transformer.from_crs(3400, 3978, always_xy=True)

    def ntems(mask, k=8000):
        idx = np.flatnonzero(mask.ravel())
        pick = np.sort(rng.choice(idx, size=min(k, idx.size), replace=False))
        rr, cc = np.divmod(pick, g.shape[1])
        x = 115700.0 + (cc * 2 + 0.5) * 100.0
        y = 6831800.0 - (rr * 2 + 0.5) * 100.0
        X, Y = tf.transform(x, y)
        with rasterio.open(a.ntems) as ds:
            v = np.array([q[0] for q in ds.sample(zip(X, Y))])
        v = v[(v != 0) & (v != 255)]
        return {"n": int(v.size), "share": {VLCE.get(int(k2), str(k2)): float(m) / v.size
                                             for k2, m in zip(*np.unique(v, return_counts=True))}}

    res["ntems_105_no_fire_since_1984"] = ntems(unburned & (g == 105))
    res["ntems_105_burned_2019_2022"] = ntems(mapped & (g == 105) & (last >= 2019) & (last <= 2022))
    (out / "landcover_105.json").write_text(json.dumps(res, indent=1))
    figure(res, out)
    for k, v in res.items():
        if isinstance(v, dict):
            print(k, v["n"], {kk: round(vv, 3) for kk, vv in v["share"].items() if vv >= 0.02})


def _enc(v: int) -> int:
    return {20: 1, 31: 2, 32: 3, 33: 4, 34: 5, 50: 6, 110: 7, 120: 8, 210: 9, 220: 10, 230: 11}.get(int(v), 0)


def _dec(a: np.ndarray) -> np.ndarray:
    lut = np.array([0, 20, 31, 32, 33, 34, 50, 110, 120, 210, 220, 230], dtype=np.int16)
    return lut[a]


def figure(res, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = [("105, all", "abmi_105_all"), ("105, WUI proxy", "abmi_105_wui"),
            ("105, no fire since 1984", "abmi_105_no_fire_since_1984"),
            ("105, burned 2019-2024", "abmi_105_burned_2019_2024"),
            ("31 O-1, all", "abmi_O1_all"), ("all mapped cells", "abmi_all")]
    cats = ["Agriculture", "Grassland", "Shrubland", "Broadleaf", "Coniferous", "Mixedwood",
            "Exposed/barren", "Developed", "Water", "Rock/rubble", "Snow/ice"]
    cols = ["#e6ab02", "#dfc27d", "#a6761d", "#66a61e", "#1b7837", "#7fbc41", "#bbbbbb",
            "#e7298a", "#377eb8", "#666666", "#ffffff"]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for i, (lab, key) in enumerate(rows):
        left = 0.0
        for cat, col in zip(cats, cols):
            v = res[key]["share"].get(cat, 0.0)
            ax.barh(i, v, left=left, color=col, edgecolor="none", label=cat if i == 0 else None)
            left += v
    ax.set_yticks(range(len(rows))); ax.set_yticklabels([f"{l} (n={res[k]['n']/1e3:.0f}k)" for l, k in rows], fontsize=7)
    ax.invert_yaxis(); ax.set_xlim(0, 1); ax.set_xlabel("share of 200 m sample cells")
    ax.set_title("ABMI Wall-to-Wall Land Cover 2010 under Alberta Wildfire FBP 2025 classes", fontsize=9)
    ax.legend(fontsize=6, ncol=6, loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False)
    fig.tight_layout(); fig.savefig(out / "fig4_class105_abmi_landcover.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    main()
