#!/usr/bin/env python3
"""[R72] PREREG_ARRIVAL1 held-out set: seeded draw, harness domains and final set. Observations only.

Input: BIG/heldout/screen.json (heldout_pool.py screen: SK + NT CFSDS fire-days 2014-2024 with an
observed community arrival on the native DOB raster, v8 pool fires excluded).

Commands
    draw      fire order = numpy.random.default_rng(SEED).permutation of the screened fire IDs
              (sorted); within a fire, its screened days in random.Random(f"{SEED}:{fire}") order.
              Writes heldout/draw_order.json. Nothing about any model is used.
    domains   walks the fire order; per fire builds the harness domain exactly as validate.py
              prepare (choose_res: 90 m, coarser to stay <= 1.2 M cells; fire + 15 km; CFS 2014b
              national fuel; MRDEM-30) and fetches ERA5 hourly (America/Edmonton clock = UTC-6 in
              the fire season), then checks its days on the harness grid (finalise rule below) and
              keeps at most MAX_PER_FIRE qualifying days per fire, in the within-fire order, until
              N_TARGET fire-days are kept or the order is exhausted. Writes heldout/manifest.json,
              heldout/finalise.json and the set file sets/arrival1_heldout.txt (+ sha256).
    (finalise rule = PREREG_COMPARATOR_C1B §2.4: Bennett's filters on the domain and at least one
    reached community by communities.observed_arrivals on the fire-day's working area.)
Usage
    capjob python3 scripts/validation/arrival_r72/heldout_build.py draw|domains
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ho_env as H  # noqa: E402  (must come first: sets the data root and the community layer)
import common as K  # noqa: E402

SEED = 2026102901
N_TARGET = 40
MAX_PER_FIRE = 2
SET_FILE = HERE / "sets/arrival1_heldout.txt"


def cmd_draw(_a):
    scr = json.loads((H.HO / "screen.json").read_text())
    by = {}
    for o in scr["fire_days"]:
        by.setdefault(o["fire_id"], []).append(o["day"])
    fires = sorted(by)
    perm = np.random.default_rng(SEED).permutation(len(fires))
    order = []
    for i in perm:
        f = fires[int(i)]
        days = sorted(by[f])
        random.Random(f"{SEED}:{f}").shuffle(days)
        order.append({"fire_id": f, "days": days})
    (H.HO / "draw_order.json").write_text(json.dumps({"seed": SEED, "n_fires": len(fires), "order": order}, indent=1))
    print(len(fires), "fires;", sum(len(o["days"]) for o in order), "screened fire-days")


def cmd_domains(_a):
    import communities as C
    import validate as V
    from firesim.validation.cfsds import FireDomain, fire_day_pairs, prepare_domain
    from firesim.validation.harness import build_case
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly

    root = H.HO
    (root / "domains").mkdir(parents=True, exist_ok=True)
    if not (root / "cfsds").exists():
        (root / "cfsds").symlink_to(K.VD / "cfsds")
    (root / "weather").mkdir(exist_ok=True)
    order = json.loads((root / "draw_order.json").read_text())["order"]
    scr = {o["fire_id"]: o for o in json.loads((root / "screen.json").read_text())["fire_days"]}
    man_p, fin_p = root / "manifest.json", root / "finalise.json"
    manifest = json.loads(man_p.read_text()) if man_p.exists() else []
    fin = json.loads(fin_p.read_text()) if fin_p.exists() else []
    done = {m["fire_id"] for m in manifest}
    keep = [f"{r['fire_id']}:{r['day']}" for r in fin if r["kept"]]
    for o in order:
        if len(keep) >= N_TARGET:
            break
        fid = o["fire_id"]
        if fid in done:
            continue
        dpath = root / f"domains/{fid}.npz"
        dob_path = K.VD / f"cfsds/doy/{fid}_krig.tif"
        res = V.choose_res(dob_path, 15000.0)
        dom = prepare_domain(fid, dob_path, V.FUEL_2014B, V.MRDEM, res_m=res, buffer_m=15000.0,
                             dem_overview_level=0 if res < 120 else 1)
        dom.meta["fuel_source"] = "CFS 2014b national FBP grid (validate.FUEL_2014B)"
        dom.save(dpath)
        bd = dom.burn_days()
        lat = dom.lat_max - 0.5 * dom.rows * dom.cell_lat
        lng = dom.lng_min + 0.5 * dom.cols * dom.cell_lng
        fetch_era5_hourly(lat, lng, doy_to_date(dom.year, bd[0]) - timedelta(days=1),
                          doy_to_date(dom.year, bd[-1]) + timedelta(days=2), root / "weather")
        pairs = {p.day for p in fire_day_pairs(dom)}
        manifest.append({"fire_id": fid, "year": dom.year, "res_m": res, "grid": [dom.rows, dom.cols],
                         "days": sorted(d for d in o["days"] if d in pairs), "screen_days": o["days"],
                         "weather_point": [lat, lng], "weather_range": [bd[0], bd[-1]],
                         "pruid": scr[fid]["pruid"], "area_ha": scr[fid]["area_ha"]})
        man_p.write_text(json.dumps(manifest, indent=1))
        K.load_fire.cache_clear()
        dom2, groups, rec = V._load_fire(fid)
        n_kept = 0
        for d in o["days"]:
            ok = d in pairs
            reached = []
            if ok and n_kept < MAX_PER_FIRE and len(keep) < N_TARGET:
                try:
                    case = build_case(dom2, d, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
                    reached = [c["name"] for c in C.observed_arrivals(case, 17.0) if c["reached"]]
                except KeyError as exc:  # missing weather / CFSDS row: not usable, recorded
                    reached, ok = [], False
                    print(fid, d, "not usable:", exc)
            kept = bool(ok and reached and n_kept < MAX_PER_FIRE and len(keep) < N_TARGET)
            fin.append({"fire_id": fid, "day": d, "bennett_on_domain": ok, "reached": reached, "kept": kept})
            if kept:
                n_kept += 1
                keep.append(f"{fid}:{d}")
        fin_p.write_text(json.dumps(fin, indent=1))
        print(fid, dom.rows, dom.cols, "kept", n_kept, "total", len(keep), flush=True)
    SET_FILE.parent.mkdir(exist_ok=True)
    SET_FILE.write_text("".join(f"{k}\n" for k in sorted(keep)))
    h = hashlib.sha256(SET_FILE.read_bytes()).hexdigest()
    print(len(keep), "fire-days kept on", len({k.split(':')[0] for k in keep}), "fires;", h)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["draw", "domains"])
    a = ap.parse_args()
    {"draw": cmd_draw, "domains": cmd_domains}[a.cmd](a)


if __name__ == "__main__":
    main()
