#!/usr/bin/env python3
"""[R72] report figures from the stage-1 outputs (attribution.json, per_community.csv,
diagnose_rows.json, weather_obs.json). Captions are in the report and the data README.

    python3 scripts/validation/arrival_r72/make_figures.py
Writes OUT/fig1_attribution.png ... fig6_fuel_corridor.png (light surface, reference palette of
the dataviz method: blue #2a78d6, orange #eb6834, aqua #1baf7a, yellow #eda100, magenta #e87ba4,
green #008300, violet #6250d6, red #e34948; one axis per panel).
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as K  # noqa: E402

SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": INK2,
                     "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                     "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6})
ORDER = ["bp_off", "bp_06_24", "bp_10_24", "no_spinup", "perimeter", "active3", "ros1.5", "ros2", "ros3",
         "ros4", "ws1.5", "wdir_toward", "station_wind", "station_speed", "station_dir", "cured_leafless",
         "fuel2014b", "spotting", "bp_off+perimeter", "bp_off+ros1.5"]


def short(n: str) -> str:
    return n.split(" (")[0]


def fig1(att):
    p = att["populations"]["primary"]
    ks = p["ranked"]
    fig, ax = plt.subplots(figsize=(9.0, 0.32 * len(ks) + 1.6))
    y = np.arange(len(ks))[::-1]
    for yi, k in zip(y, ks):
        d = p["variants"][k]["delta_err_h"]
        col = S[0] if d["hi"] < 0 else (S[7] if d["lo"] > 0 else INK2)
        ax.plot([d["lo"], d["hi"]], [yi, yi], color=col, lw=2, solid_capstyle="round")
        ax.plot(d["mean"], yi, "o", color=col, ms=6, mec=SURF, mew=1.5)
        v = p["variants"][k]
        ax.text(1.01, yi, f"{v['hits']}/{v['n_communities']}", transform=ax.get_yaxis_transform(),
                va="center", fontsize=8, color=INK2)
    ax.axvline(0, color=INK2, lw=1)
    ax.set_yticks(y, [p["variants"][k]["label"] for k in ks])
    ax.set_xlabel("Change in mean arrival error vs the operational run (h)\nnegative = better; 95 % whole-fire bootstrap CI")
    b = p["base_mean_err_h"]
    ax.set_title(f"C1b primary: {p['n_fire_days']} fire-days, {p['n_communities']} communities\n"
                 f"operational error {b['mean']:.2f} h, hits {p['base_hits']}/{p['n_communities']} (right column: hits)",
                 fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(K.OUT / "fig1_attribution.png", dpi=160)


def fig2(pc):
    fams = [f for f in ORDER if any(f"err_{f}" in r for r in pc)]
    rows = sorted(pc, key=lambda r: (r["population"] != "primary", r["fire_day"], r["community"]))
    def val(r, k):
        v = r.get(k)
        return math.nan if v in (None, "") else float(v)

    M = np.array([[val(r, "base_err_h")] + [val(r, f"err_{f}") for f in fams] for r in rows])
    fig, ax = plt.subplots(figsize=(9.5, 0.27 * len(rows) + 2.2))
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq", ["#ffffff", "#9ec5f4", "#2a78d6", "#104281"])
    im = ax.imshow(M, aspect="auto", cmap=cmap, vmin=0, vmax=18)
    ax.set_xticks(range(len(fams) + 1), ["operational"] + fams, rotation=60, ha="right")
    ax.set_yticks(range(len(rows)), [f"{r['fire_day']} {short(r['community'])}" + (" *" if r["population"] != "primary" else "")
                                    for r in rows], fontsize=7.5)
    ax.grid(False)
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            if math.isnan(M[i, j]):
                ax.text(j, i, "-", ha="center", va="center", fontsize=6, color=INK2)
            else:
                ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center", fontsize=6,
                        color="#ffffff" if M[i, j] > 9 else INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.set_label("arrival error (h)")
    ax.set_title("Community-arrival error (h) by run variant; - = variant not run (no station); * = Horse River calibration",
                 fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(K.OUT / "fig2_error_by_variant.png", dpi=160)


def fig3(diag, sens):
    comm = [r for r in diag if not r.get("summary")]
    n = len(comm)
    nc = 5
    nr = int(math.ceil(n / nc))
    fig, axs = plt.subplots(nr, nc, figsize=(12, 2.1 * nr), sharex=True)
    for ax, r in zip(axs.ravel(), comm):
        t, d = np.array(r["dsim_curve"]).T
        ax.plot(t, d / 1000, color=S[0], lw=2, label="FireSim operational")
        bo = sens.get((r["fire_day"], "bp_off"))
        if bo is not None and r["name"] in bo:
            pass
        sq = np.array(r["viirs_seq"]) if r["viirs_seq"] else np.zeros((0, 2))
        if len(sq):
            ax.step(sq[:, 0], sq[:, 1] / 1000, where="post", color=S[1], lw=2, label="VIIRS (cumulative)")
            ax.plot(sq[:, 0], sq[:, 1] / 1000, "o", color=S[1], ms=3)
        ax.axvspan(4, 14, color="#f0efec", zorder=0)
        ax.axvspan(r["lo"], r["hi"], ymin=0, ymax=0.08, color=INK2, alpha=0.6)
        ax.set_title(f"{r['fire_day']} {short(r['name'])[:22]}", fontsize=7.5, loc="left")
        ax.set_ylim(bottom=0)
        ax.tick_params(labelsize=7)
    for ax in axs.ravel()[n:]:
        ax.axis("off")
    axs.ravel()[0].legend(fontsize=6.5, loc="upper right", frameon=False)
    fig.supxlabel("hours from 06:00 local on the burn day (shaded: burning period 10-20 h; bar: observed arrival interval)", fontsize=8)
    fig.supylabel("distance to the community's reach zone (km)", fontsize=8)
    fig.tight_layout()
    fig.savefig(K.OUT / "fig3_distance_curves.png", dpi=150)


def fig4(pc):
    rows = sorted(pc, key=lambda r: (r["population"] != "primary", r["fire_day"], r["community"]))
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(rows) + 1.4))
    y = np.arange(len(rows))[::-1]
    ax.axvspan(4, 14, color="#f0efec", zorder=0, label="burning period 10-20 h")
    for yi, r in zip(y, rows):
        ax.plot([r["lo"], r["hi"]], [yi, yi], color=INK2, lw=5, alpha=0.5, solid_capstyle="butt")
        ax.plot(min(r["base_model_h"], 24), yi, "o", color=S[0], ms=6, mec=SURF, mew=1)
    ax.axvline(24, color=INK2, ls="--", lw=1)
    ax.axvline(18, color=INK2, ls=":", lw=1)
    ax.set_yticks(y, [f"{r['fire_day']} {short(r['community'])} [{r['burning_class']}]" for r in rows], fontsize=7)
    ax.set_xlabel("hours from 06:00 local on the burn day\ngrey bar: observed interval; blue dot: FireSim operational arrival; dashed 24 h: not reached")
    ax.set_xlim(-7, 25)
    ax.legend(fontsize=7, loc="lower left", frameon=False)
    fig.tight_layout()
    fig.savefig(K.OUT / "fig4_timing_burning_period.png", dpi=160)


def fig5(wo, diag):
    keys = list(wo)
    fig, axs = plt.subplots(1, 2, figsize=(10, 0.3 * len(keys) + 1.5), sharey=True)
    y = np.arange(len(keys))[::-1]
    for yi, k in zip(y, keys):
        for j, s in enumerate(wo[k]["stations"][:3]):
            if s["ws_bias_station_minus_era5"] is None:
                continue
            axs[0].plot(s["ws_bias_station_minus_era5"], yi + 0.15 * (j - 1), "o", color=S[j], ms=5)
            if s["dir_err_mean_deg"] is not None:
                axs[1].plot(s["dir_err_mean_deg"], yi + 0.15 * (j - 1), "o", color=S[j], ms=5)
    axs[0].axvline(0, color=INK2, lw=1)
    axs[0].set_yticks(y, [f"{k} ({', '.join(s['sid'] for s in wo[k]['stations'][:3]) or 'no station'})" for k in keys], fontsize=7)
    axs[0].set_xlabel("10 m wind speed, station - ERA5 (km/h), hours 0-24")
    axs[1].set_xlabel("mean |direction difference| (deg), station speed >= 5 km/h")
    axs[0].plot([], [], "o", color=S[0], label="nearest station")
    axs[0].plot([], [], "o", color=S[1], label="second station")
    axs[0].plot([], [], "o", color=S[2], label="third station")
    axs[0].legend(fontsize=7, frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(K.OUT / "fig5_wind_era5_vs_station.png", dpi=160)


def fig6(diag):
    comm = [r for r in diag if not r.get("summary")]
    cls = ["conifer", "mixed", "deciduous", "grass", "slash", "nonfuel"]
    fig, ax = plt.subplots(figsize=(8, 0.34 * len(comm) + 1.3))
    y = np.arange(len(comm))[::-1]
    for yi, r in zip(y, comm):
        for off, key in ((0.18, "fuel_class"), (-0.18, "fuel_class_2014b")):
            left = 0.0
            for i, c in enumerate(cls):
                v = r.get(key, {}).get(c, 0.0)
                ax.barh(yi + off, v, left=left, height=0.33, color=S[i] if c != "nonfuel" else "#bdbcb7",
                        edgecolor=SURF, linewidth=1)
                left += v
    ax.set_yticks(y, [f"{r['fire_day']} {short(r['name'])[:20]}" for r in comm], fontsize=7)
    for i, c in enumerate(cls):
        ax.barh([], [], color=S[i] if c != "nonfuel" else "#bdbcb7", label=c)
    ax.legend(ncol=6, fontsize=7, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0))
    ax.set_xlabel("share of the 1 km path corridor (upper bar: back-cast grid used; lower bar: CFS 2014b)")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(K.OUT / "fig6_fuel_corridor.png", dpi=160)


def main():
    att = json.loads((K.OUT / "attribution.json").read_text())
    pc = list(csv.DictReader(open(K.OUT / "per_community.csv")))
    for r in pc:
        for k, v in list(r.items()):
            if k.startswith(("err_", "base_", "lo", "hi")) and v not in ("", None):
                try:
                    r[k] = float(v)
                except ValueError:
                    pass
    diag = json.loads((K.OUT / "diagnose_rows.json").read_text())
    wo = json.loads((K.OUT / "weather_obs.json").read_text())
    fig1(att)
    fig2(pc)
    fig3(diag, {})
    fig4(pc)
    fig5(wo, diag)
    fig6(diag)
    print("figures written")


if __name__ == "__main__":
    main()
