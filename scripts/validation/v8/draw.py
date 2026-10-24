"""v8 sequential decision draw (PREREG_LR8 section 3; addendum A2).

Fires are taken in the frozen decision order (``decision_order.csv``, sha256-checked), in blocks of 25 per
region. For each fire, in order:
1. eligibility, blind to hotspots: an IEM station within 50 km of the final perimeter with >= 50 % hourly
   coverage over the fire's RECORD window (NBAC start-end; MTBS ignition + 30 d; WFIGS discovery - out, max
   60 d); ineligible fires are skipped, listed, and do not count toward the cap;
2. its VIIRS detections (FIRMS yearly archives, final perimeter + 2 km) are cached as model inputs;
3. the registered jump rule (addendum-A1 ``v8_viirs_jumps.catalogue_fire``) runs; ONLY the per-fire count under
   the primary rule (1-15 km, line check, inside the final perimeter, separate-start mask, interval <= 12 h) is
   written to ``draw/counts.csv``; the fire's jump table goes to ``draw/sealed/<fire>.csv`` (every catalogue row,
   including never-reached clusters) and is not read by any step before every model has run on every drawn fire
   (``unseal`` in assemble.py). Each sealed file's sha256 goes to ``draw/sealed_manifest.csv`` when written.
After each pair of blocks the totals decide: stop when total >= 150 and each region >= 60 (a region already
holding >= 60 stops once the total is >= 150), or at the cap of 250 eligible fires per region (then: not
decidable). The draw log records every block and its counts only. Resumable: the position in the order and
the counts are saved after every fire, and a fire already in counts.csv is never drawn again.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely import contains_xy

from . import common as C
from . import inputs as I

K_MIN, K_REG, CAP, BLOCK = 150, 60, 250, 25
OUT = C.DATA / "draw"
SEALED = OUT / "sealed"
DEC_DET = C.DATA / "viirs_decision"


def order():
    p = C.PREFREEZE / "results" / "decision_order.csv"
    if C.sha256(p) != C.ORDER_SHA256:
        raise RuntimeError("decision_order.csv does not match the PREREG_LR8 sha256")
    return pd.read_csv(p)


def extract_detections(recs: list[dict]) -> None:
    """Cache FIRMS rows inside each fire's final perimeter + 2 km (decision fires being drawn)."""
    DEC_DET.mkdir(parents=True, exist_ok=True)
    cols = ["latitude", "longitude", "scan", "track", "acq_date", "acq_time", "satellite", "confidence", "daynight", "type", "frp"]
    todo = [r for r in recs if not (DEC_DET / f"{r['fire_key'].replace(':', '_')}.pkl.gz").exists()]
    for y in sorted({r["year"] for r in todo}):
        ry = [r for r in todo if r["year"] == y]
        bufs = []
        for r in ry:
            gs = gpd.GeoSeries([r["geometry"]], crs=4326)
            utm = gs.estimate_utm_crs()
            bufs.append((r["fire_key"], gs.to_crs(utm).simplify(50).buffer(2000, resolution=4).simplify(100).to_crs(4326).iloc[0]))
        keep = {k: [] for k, _ in bufs}
        for sat in ("viirs-snpp", "viirs-jpss1"):
            for c in ("Canada", "United_States"):
                for ch in pd.read_csv(I.RAW / f"firms/{sat}_{y}_{c}.csv", usecols=cols, chunksize=200_000, dtype={"acq_time": str}):
                    for k, buf in bufs:
                        bx = buf.bounds
                        sel = ((ch.longitude >= bx[0]) & (ch.longitude <= bx[2]) & (ch.latitude >= bx[1]) & (ch.latitude <= bx[3])).values
                        if sel.any():
                            sub = ch[sel]
                            ins = contains_xy(buf, sub.longitude.values, sub.latitude.values)
                            if ins.any():
                                keep[k].append(sub[ins])
        for k, parts in keep.items():
            d = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
            d["t"] = pd.to_datetime(d.acq_date + " " + d.acq_time.astype(str).str.zfill(4), format="%Y-%m-%d %H%M")
            d = d.drop(columns=["acq_date", "acq_time"]).assign(fire_key=k)
            d.to_pickle(DEC_DET / f"{k.replace(':', '_')}.pkl.gz")


_SEP = None


def count_jumps(rec: dict) -> tuple[int, dict]:
    """Registered jump rule on one decision fire; returns the primary count and the fire's interval info (no
    locations); seals the fire's jump table."""
    from types import SimpleNamespace
    V = C.frozen_jumps()
    det = pd.read_pickle(DEC_DET / f"{rec['fire_key'].replace(':', '_')}.pkl.gz")
    fr = SimpleNamespace(fire_key=rec["fire_key"], region=rec["region"], year=rec["year"], assignment="decision",
                         geometry=rec["geometry"], t0=rec["t0"], t1=rec["t1"])
    global _SEP
    if _SEP is None:
        _SEP = V.separate_starts(set())  # start points of every other recorded fire (addendum A2)
    if not len(det):
        return 0, {}
    info, js = V.catalogue_fire(fr, det, _SEP)
    J = pd.DataFrame(js)
    SEALED.mkdir(parents=True, exist_ok=True)
    p = SEALED / f"{rec['fire_key'].replace(':', '_')}.csv"
    J.to_csv(p, index=False)
    man = OUT / "sealed_manifest.csv"
    new = not man.exists()
    with open(man, "a") as f:
        if new:
            f.write("fire_key,file,sha256\n")
        f.write(f"{rec['fire_key']},{p.name},{C.sha256(p)}\n")
    info = {k: info[k] for k in ("n_det", "n_epochs", "n_int_12h", "n_int_14h", "n_int_24h")} if info else {}
    if len(J):
        m = J.line_ok & J.inside_final & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 1000) & (J.dist_m <= 15000)
        return int(m.sum()), info
    return 0, info


COLS = ["region", "rank", "fire_key", "eligible", "K", "n_int_12h"]


def status(counts):
    tot = int(counts.K.sum()) if len(counts) else 0
    reg = {r: int(counts[counts.region == r].K.sum()) for r in ("CAN", "US")}
    nel = {r: int(counts[(counts.region == r) & counts.eligible.astype(bool)].shape[0]) for r in ("CAN", "US")}
    return tot, reg, nel


def main():
    C.guard()
    OUT.mkdir(parents=True, exist_ok=True)
    o = order()
    state_p = OUT / "state.json"
    st = json.loads(state_p.read_text()) if state_p.exists() else {"pos": {"CAN": 0, "US": 0}, "done": False}
    cnt_p = OUT / "counts.csv"
    counts = pd.read_csv(cnt_p) if cnt_p.exists() else pd.DataFrame(columns=COLS)
    seen = set(counts.fire_key)
    while not st["done"]:
        tot, reg, nel = status(counts)
        if tot >= K_MIN and all(v >= K_REG for v in reg.values()):
            st.update(done=True, outcome="K_min reached")
            break
        drew = False
        for r in ("CAN", "US"):
            if tot >= K_MIN and reg[r] >= K_REG:
                continue
            if nel[r] >= CAP:
                continue
            oo = o[o.region == r].sort_values("rank")
            # phase A: the block's fires in order, inputs built (cached per fire, blind to hotspots)
            block, e_block, pos = [], 0, st["pos"][r]
            while len(block) < BLOCK and pos < len(oo) and nel[r] + e_block < CAP:
                row = oo.iloc[pos]
                pos += 1
                k = row.fire_key
                if k in seen:  # resumed after a stop: never draw a fire twice
                    continue
                t = time.time()
                m = I.build(k, blind=True)
                x = dict(region=r, rank=int(row["rank"]), fire_key=k, eligible=bool(m["eligible_station"]), K=0, n_int_12h=0)
                C.log_run("draw_inputs", k, "eligible" if x["eligible"] else "ineligible", time.time() - t)
                block.append(x)
                e_block += int(x["eligible"])
            # phase B: detections of the block's eligible fires (one pass over the FIRMS files per year; cached)
            recs = {x["fire_key"]: I.fire_record(x["fire_key"]) for x in block if x["eligible"]}
            extract_detections(list(recs.values()))
            # phase C: counts only; position and counts saved after every fire
            n_block, k_block = 0, 0
            for x in block:
                if x["eligible"]:
                    t = time.time()
                    x["K"], info = count_jumps(recs[x["fire_key"]])
                    x["n_int_12h"] = int(info.get("n_int_12h", 0))
                    C.log_run("draw_count", x["fire_key"], "counted", time.time() - t)
                counts = pd.concat([counts, pd.DataFrame([x])], ignore_index=True)
                counts.to_csv(cnt_p, index=False)
                seen.add(x["fire_key"])
                st["pos"][r] = int(oo.index.get_loc(oo.index[oo.fire_key == x["fire_key"]][0])) + 1
                state_p.write_text(json.dumps(st))
                n_block += 1; k_block += x["K"]
            st["pos"][r] = max(st["pos"][r], pos)
            state_p.write_text(json.dumps(st))
            drew = drew or n_block > 0
            with (OUT / "draw_log.txt").open("a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {r} block of {n_block} "
                        f"(eligible {e_block}): jumps {k_block}\n")
        if not drew:
            st.update(done=True, outcome="NOT DECIDABLE (caps or pool exhausted before K_min)")
        state_p.write_text(json.dumps(st))
    man = OUT / "sealed_manifest.csv"
    st["sealed_manifest_sha256"] = C.sha256(man) if man.exists() else None
    state_p.write_text(json.dumps(st))
    print(st, counts.groupby("region").agg(fires=("fire_key", "size"), eligible=("eligible", "sum"), K=("K", "sum")))


if __name__ == "__main__":
    main()
