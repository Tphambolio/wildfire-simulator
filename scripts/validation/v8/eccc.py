"""PREREG_LR8C addendum A2: ECCC climate-hourly stations for LR8C eligibility (fires with no eligible IEM station).

Purpose
    Pool A Canadian fires that the shared draw (``heldout.py``) found ineligible with IEM get a second eligibility pass
    with ECCC MSC climate-hourly stations (open, ECCC Data Servers End-use Licence, attribution), under the frozen A2.1
    rule: a station within 50 km of the final perimeter with >= 50 % of hours observed over the record window. Eligible
    fires are built, catalogued (sealed, counts included) and run through the registered interval engine exactly as
    the IEM fires, in a separate namespace so that nothing of the registered IEM build changes:
        ``$FIRESIM_V8_DATA/fires_eccc/<fire>/``  inputs, weather, intervals
        ``$FIRESIM_V8_DATA/heldout/eccc_state.json``, ``heldout/sealed_eccc/``, ``heldout/sealed_eccc_manifest.csv``
    These fires enter LR8C only (A2 scope), never LR8L or LR8S.

How the ECCC data are used
    ``inputs.build`` runs unchanged with two substitutions: the station table is the ECCC climate-hourly station list
    (``validation-data/station-scoping-2026-10-29/eccc_hourly_*.json``, downloaded 2026-10-29), and the hourly fetch
    reads ``api.weather.gc.ca`` ``climate-hourly`` (TEMP, RELATIVE_HUMIDITY, WIND_SPEED km/h, WIND_DIRECTION tens of
    degrees) converted to the IEM columns the builder expects. Precipitation, FFMC and the daily codes then follow
    A2.1/A2.2 unchanged (ERA5 precipitation at the station). Stations whose ICAO id ("C" + TC id) is an IEM station are
    skipped (A2 rule 3).

Usage (``scripts/validation``; ``PYTHONPATH=<repo>:<repo>/engine/src``; under ``capjob``): python3 -m v8.eccc run
Nothing here opens a sealed file or computes a score; ``status`` prints eligibility counts only.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

from . import common as C

STN_DIR = Path.home() / "dev/wildfire/validation-data/station-scoping-2026-10-29"
API = "https://api.weather.gc.ca/collections/climate-hourly/items"
OUT = C.DATA / "heldout"
FIRES_ECCC = C.DATA / "fires_eccc"
SEALED = OUT / "sealed_eccc"


def dms(v: int) -> float:
    """ECCC climate-stations LATITUDE/LONGITUDE (DDMMSSsss as an integer, signed) to decimal degrees."""
    a = abs(int(v))
    d, m, s = a // 10_000_000, (a // 100_000) % 100, (a % 100_000) / 1000.0
    return (d + m / 60.0 + s / 3600.0) * (-1 if v < 0 else 1)


def eccc_stations(iem_ids: set) -> pd.DataFrame:
    rows = []
    for f in sorted(STN_DIR.glob("eccc_hourly_*.json")):
        for ft in json.load(open(f))["features"]:
            p = ft["properties"]
            tc = p.get("TC_IDENTIFIER")
            if tc and f"C{tc}" in iem_ids:
                continue  # same site as an IEM station already tried (A2 rule 3)
            rows.append(dict(sid=f"ECCC{p['CLIMATE_IDENTIFIER']}", climate_id=p["CLIMATE_IDENTIFIER"], network="ECCC",
                             lon=dms(p["LONGITUDE"]), lat=dms(p["LATITUDE"]), elev=p.get("ELEVATION"),
                             begin=p.get("FIRST_DATE"), end=p.get("LAST_DATE")))
    return pd.DataFrame(rows).drop_duplicates("sid")


def eccc_hourly(sid: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """ECCC climate-hourly as the IEM hourly frame (hour, tmpf, relh, sknt, drct), cached raw."""
    from . import inputs as I
    cid = sid[4:]
    p = I.RAW / "eccc_data" / f"{cid}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        feats, off = [], 0
        while True:
            q = {"CLIMATE_IDENTIFIER": cid, "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
                 "limit": 10000, "offset": off, "f": "json",
                 "properties": "UTC_DATE,TEMP,RELATIVE_HUMIDITY,WIND_SPEED,WIND_DIRECTION"}
            j = json.loads(I.http(API + "?" + urllib.parse.urlencode(q)))
            fs = j.get("features", [])
            feats += [f["properties"] for f in fs]
            if len(fs) < 10000:
                break
            off += 10000
        p.write_text(json.dumps(feats))
    d = pd.DataFrame(json.loads(p.read_text()))
    if not len(d):
        return d
    d["hour"] = pd.to_datetime(d.UTC_DATE).dt.tz_localize(None).dt.floor("h")
    out = pd.DataFrame({"hour": d.hour, "tmpf": d.TEMP * 9 / 5 + 32, "relh": d.RELATIVE_HUMIDITY,
                        "sknt": d.WIND_SPEED / 1.852, "drct": d.WIND_DIRECTION * 10.0})
    return out.groupby("hour").mean().reset_index()


def candidates() -> list[str]:
    st = json.loads((OUT / "state.json").read_text())
    return [k for k, s in st.items() if s.get("pool") == "A" and s.get("region") == "CAN" and s.get("done")
            and s.get("eligible") is False]


def run() -> None:
    from . import inputs as I
    from . import intervals as IV
    from . import heldout as H
    from . import draw as D
    if C.sha256(Path(IV.__file__)) != H.INTERVALS_SHA:
        raise RuntimeError("intervals.py differs from the registered sha256")
    iem_ids = set(I.stations_table().sid)
    est = eccc_stations(iem_ids)
    I.stations_table = lambda: est
    I.iem_hourly = eccc_hourly
    C.fire_dir = lambda k: (FIRES_ECCC / k.replace(":", "_")).mkdir(parents=True, exist_ok=True) or FIRES_ECCC / k.replace(":", "_")
    H.SEALED = SEALED
    H.OUT = OUT
    sp = OUT / "eccc_state.json"
    st = json.loads(sp.read_text()) if sp.exists() else {}
    for k in candidates():
        s = st.setdefault(k, {})
        if s.get("done"):
            continue
        t = time.time()
        try:
            m = I.build(k, blind=True)
            s["eligible"] = bool(m["eligible_station"])
            if s["eligible"]:
                rec = I.fire_record(k)
                D.extract_detections([rec])
                if not s.get("sealed"):
                    _seal(k, rec)
                    s["sealed"] = True
                s["intervals"] = IV.run_fire(k, "heldout").get("status")
            s.pop("error", None)
            s["done"] = True
            C.log_run("eccc", k, "ok", time.time() - t)
        except Exception as e:
            s["error"] = repr(e)[:300]
            C.log_run("eccc", k, f"error {e!r}"[:200], time.time() - t)
        sp.write_text(json.dumps(st, indent=1))
        print(k, "done" if s.get("done") else "ERROR", "eligible" if s.get("eligible") else "ineligible", flush=True)


def _seal(k, rec):
    """As heldout.seal, into the ECCC namespace (separate manifest)."""
    from types import SimpleNamespace
    from . import draw as D
    V = C.frozen_jumps()
    det = pd.read_pickle(D.DEC_DET / f"{k.replace(':', '_')}.pkl.gz")
    if D._SEP is None:
        D._SEP = V.separate_starts(set())
    SEALED.mkdir(parents=True, exist_ok=True)
    p = SEALED / f"{k.replace(':', '_')}.csv"
    if len(det):
        fr = SimpleNamespace(fire_key=k, region=rec["region"], year=rec["year"], assignment="decision",
                             geometry=rec["geometry"], t0=rec["t0"], t1=rec["t1"])
        _, js = V.catalogue_fire(fr, det, D._SEP)
        pd.DataFrame(js).to_csv(p, index=False)
    else:
        p.write_text("")
    man = OUT / "sealed_eccc_manifest.csv"
    new = not man.exists()
    with open(man, "a") as f:
        if new:
            f.write("fire_key,file,sha256\n")
        f.write(f"{k},{p.name},{C.sha256(p)}\n")


def status() -> None:
    sp = OUT / "eccc_state.json"
    st = json.loads(sp.read_text()) if sp.exists() else {}
    print(f"candidates {len(candidates())}, tried {len(st)}, eligible {sum(bool(s.get('eligible')) for s in st.values())}, "
          f"errors {sum('error' in s for s in st.values())}")


if __name__ == "__main__":
    C.guard()
    run() if sys.argv[1:] == ["run"] else status()
