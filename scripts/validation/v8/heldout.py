"""Held-out draw for PREREG_LR8C, PREREG_LR8L and PREREG_LR8S (pools A and E); stops before any scoring.

Purpose
    One draw serves the three studies (PREREG_LR8S §2). For every fire of pool A (v8 decision ranks 201-477 CAN and
    201-303 US of the sealed ``decision_order.csv``) and pool E (the sealed ``extension_order.csv`` of PREREG_LR8L),
    in order and with no sequential stop:
      1. ``inputs``  eligibility and inputs, blind to hotspots (``inputs.build(blind=True)``: IEM station within 50 km
                     with >= 50 % hourly coverage over the record window; fuels; curing), as v8 A2.1;
      2. ``detect``  FIRMS VIIRS detections inside the final perimeter + 2 km, cached as model inputs (``draw``'s
                     extractor);
      3. ``seal``    the registered jump rule (A1 ``catalogue_fire``); the fire's whole jump table, **including its
                     counts**, goes to ``heldout/sealed/<fire>.csv`` with its sha256 in ``heldout/sealed_manifest.csv``.
                     Nothing derived from the table (no count, no location) is written anywhere else or printed: LR8C
                     scores counts, so even per-fire counts stay sealed until ``unseal``;
      4. ``intervals`` the registered v8 interval engine (``intervals.run_fire``, sha256 checked), which holds what
                     M8 (A2 beta), the LR8L beta_L variants and B1 need, per candidate cell.
    ``status`` prints progress only (fires done per step, eligibility). There is deliberately **no** unseal or score
    command here: the main session gives the go for each study before that code is written and run.

Usage (from ``scripts/validation``, ``PYTHONPATH=<repo>/engine/src``; under ``capjob``, <= 5 GB)
    python3 -m v8.heldout run [--pool A|E] [--limit N]
    python3 -m v8.heldout status
Data: ``$FIRESIM_V8_DATA/heldout/`` (state, logs, sealed tables) and the per-fire folders under ``fires/``.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

from . import common as C

OUT = C.DATA / "heldout"
SEALED = OUT / "sealed"
EXT_ORDER = Path.home() / "dev/wildfire/reports/data/lr8-followups-prereg-2026-10-29/results/extension_order.csv"
EXT_SHA = "033e5ab82d384271863111e6cdf2d7a4ba4260cd738f27844d0cebd748c033b6"
INTERVALS_SHA = "4303e544736968f29f28dd827f662b0f6dfed9178109154ed309d7a5b4724dbe"
MIN_FREE_GB = 20.0


def pool() -> pd.DataFrame:
    """Pool A then pool E, each in its sealed order: columns pool, region, rank, fire_key."""
    o = pd.read_csv(C.PREFREEZE / "results" / "decision_order.csv")
    if C.sha256(C.PREFREEZE / "results" / "decision_order.csv") != C.ORDER_SHA256:
        raise RuntimeError("decision_order.csv does not match the PREREG_LR8 sha256")
    drawn = set(pd.read_csv(C.DATA / "draw" / "counts.csv").fire_key)
    a = o[(o["rank"] > 200)].copy()
    if a.fire_key.isin(drawn).any():
        raise RuntimeError("a pool A fire was drawn in v8")
    a["pool"] = "A"
    if C.sha256(EXT_ORDER) != EXT_SHA:
        raise RuntimeError("extension_order.csv does not match the PREREG_LR8L sha256")
    e = pd.read_csv(EXT_ORDER)[["region", "rank", "fire_key"]].assign(pool="E")
    e["region"] = "CAN"  # PREREG_LR8S section 2: pool E fires are region CAN (reported as CAN-east)
    a = pd.concat([a[a.region == "CAN"].sort_values("rank"), a[a.region == "US"].sort_values("rank")])
    return pd.concat([a, e], ignore_index=True)[["pool", "region", "rank", "fire_key"]]


def _state() -> dict:
    p = OUT / "state.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save(st: dict) -> None:
    (OUT / "state.json").write_text(json.dumps(st, indent=1))


def _free_gb() -> float:
    return shutil.disk_usage(C.DATA).free / 1024 ** 3


def seal(fire_key: str, rec: dict) -> None:
    """Catalogue the fire with the registered rule and seal the table (no count leaves this function)."""
    from types import SimpleNamespace
    from . import draw as D
    V = C.frozen_jumps()
    det = pd.read_pickle(D.DEC_DET / f"{fire_key.replace(':', '_')}.pkl.gz")
    if D._SEP is None:
        D._SEP = V.separate_starts(set())
    SEALED.mkdir(parents=True, exist_ok=True)
    p = SEALED / f"{fire_key.replace(':', '_')}.csv"
    if len(det):
        fr = SimpleNamespace(fire_key=fire_key, region=rec["region"], year=rec["year"], assignment="decision",
                             geometry=rec["geometry"], t0=rec["t0"], t1=rec["t1"])
        _, js = V.catalogue_fire(fr, det, D._SEP)
        pd.DataFrame(js).to_csv(p, index=False)
    else:
        p.write_text("")
    man = OUT / "sealed_manifest.csv"
    new = not man.exists()
    with open(man, "a") as f:
        if new:
            f.write("fire_key,file,sha256\n")
        f.write(f"{fire_key},{p.name},{C.sha256(p)}\n")


def run(only_pool: str | None = None, limit: int | None = None) -> None:
    from . import draw as D
    from . import inputs as I
    from . import intervals as IV
    if C.sha256(Path(IV.__file__)) != INTERVALS_SHA:
        raise RuntimeError("intervals.py differs from the registered sha256")
    OUT.mkdir(parents=True, exist_ok=True)
    st = _state()
    P = pool()
    if only_pool:
        P = P[P.pool == only_pool]
    n = 0
    for row in P.itertuples():
        k = row.fire_key
        s = st.setdefault(k, {"pool": row.pool, "region": row.region, "rank": int(row.rank)})
        if s.get("done"):
            continue
        if limit is not None and n >= limit:
            break
        if _free_gb() < MIN_FREE_GB:
            print(f"paused: {_free_gb():.1f} GB free < {MIN_FREE_GB}", flush=True)
            break
        t = time.time()
        try:
            m = I.build(k, blind=True)
            s["eligible"] = bool(m["eligible_station"])
            if s["eligible"]:
                rec = I.fire_record(k)
                D.extract_detections([rec])
                if not s.get("sealed"):
                    seal(k, rec)
                    s["sealed"] = True
                r = IV.run_fire(k, "heldout")
                s["intervals"] = r.get("status")
            s["done"] = True
            s["seconds"] = round(time.time() - t, 1)
            C.log_run("heldout", k, "ok", time.time() - t, row.pool)
        except Exception as e:  # recorded, retried on the next run (inputs are cached per fire)
            s["error"] = repr(e)[:300]
            C.log_run("heldout", k, f"error {e!r}"[:200], time.time() - t, row.pool)
        _save(st)
        n += 1
        print(row.pool, row.region, row.rank, "done" if s.get("done") else "ERROR",
              "eligible" if s.get("eligible") else "ineligible", flush=True)


def status() -> None:
    st = _state()
    if not st:
        print("nothing drawn")
        return
    d = pd.DataFrame.from_dict(st, orient="index")
    for c in ("done", "eligible", "sealed", "error"):
        if c not in d:
            d[c] = None
    g = d.groupby(["pool", "region"]).agg(fires=("rank", "size"), done=("done", lambda x: int((x == True).sum())),
                                          eligible=("eligible", lambda x: int((x == True).sum())),
                                          errors=("error", lambda x: int(x.notna().sum())))
    print(g.to_string())


if __name__ == "__main__":
    C.guard()
    args = sys.argv[1:]
    if args and args[0] == "run":
        pool_ = args[args.index("--pool") + 1] if "--pool" in args else None
        lim = int(args[args.index("--limit") + 1]) if "--limit" in args else None
        run(pool_, lim)
    else:
        status()
