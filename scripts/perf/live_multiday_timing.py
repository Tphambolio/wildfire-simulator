"""Submit ONE default multi-day run to the live API, record its progress over time, and cancel
it at a time limit (backlog E10). Logs (wall s, status, phase, progress, day, frames) every
POLL_S seconds to a CSV; the run is always cancelled (WebSocket "cancel") if still going.

Usage: python scripts/perf/live_multiday_timing.py OUT.csv [--limit-min 40] [--lat ..] [--lng ..]
       [--no-burning-period]
"""
import argparse
import asyncio
import csv
import json
import time
import urllib.request

import websockets

API = "https://firesim-api.fly.dev/api/v1"
POLL_S = 20


def payload(lat, lng, start, burning):
    return {
        "ignition_lat": lat, "ignition_lng": lng,
        "days": [
            {"wind_speed": 20, "wind_direction": 270, "temperature": 25, "relative_humidity": 30, "precipitation_24h": 0},
            {"wind_speed": 25, "wind_direction": 270, "temperature": 28, "relative_humidity": 25, "precipitation_24h": 0},
            {"wind_speed": 30, "wind_direction": 260, "temperature": 30, "relative_humidity": 20, "precipitation_24h": 0},
        ],
        "fwi_overrides": {"ffmc": 90, "dmc": 45, "dc": 300},
        "fuel_modifiers": {"grass_cure": 95, "percent_conifer": 50, "day_of_year": 297},
        "month": 10, "snapshot_interval_minutes": 30, "fuel_type": "C2",
        "fuel_grid_path": "/app/data/Edmonton_FBP_FuelLayer_20251105_10m.tif",
        "water_path": None, "buildings_path": "/app/data/edmonton_buildings.geojson.gz",
        "dem_path": "/app/data/edmonton_dem.tif", "start_time": start,
        "burning_period": {"start_hour": 10, "end_hour": 20} if burning else None,
        "cells_mode": "incremental",
    }


def get(sim_id):
    with urllib.request.urlopen(f"{API}/simulations/{sim_id}", timeout=60) as r:
        d = json.load(r)
    return d["status"], d.get("phase"), d.get("progress"), d.get("day"), len(d.get("frames") or [])


async def cancel(sim_id):
    async with websockets.connect(f"wss://firesim-api.fly.dev/api/v1/simulations/ws/{sim_id}") as ws:
        await ws.send(json.dumps({"action": "cancel"}))
        await asyncio.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--limit-min", type=float, default=40)
    ap.add_argument("--lat", type=float, default=53.4605)
    ap.add_argument("--lng", type=float, default=-113.6599)
    ap.add_argument("--start", default="2026-10-24T00:32:00-06:00")
    ap.add_argument("--no-burning-period", action="store_true")
    a = ap.parse_args()
    body = json.dumps(payload(a.lat, a.lng, a.start, not a.no_burning_period)).encode()
    req = urllib.request.Request(f"{API}/simulations/multiday", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        sim_id = json.load(r)["simulation_id"]
    print("simulation", sim_id, flush=True)
    status = "running"
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["wall_s", "status", "phase", "progress", "day", "frames"])
        try:
            while True:
                try:
                    status, phase, prog, day, n = get(sim_id)
                except Exception as e:  # transient network error: keep polling
                    print("poll error", e, flush=True)
                    time.sleep(POLL_S)
                    continue
                row = [round(time.time() - t0, 1), status, phase, prog, day, n]
                w.writerow(row)
                fh.flush()
                print(*row, flush=True)
                if status in ("completed", "failed", "cancelled"):
                    break
                if time.time() - t0 > a.limit_min * 60:
                    break
                time.sleep(POLL_S)
        finally:
            if status not in ("completed", "failed", "cancelled"):
                asyncio.run(cancel(sim_id))
                time.sleep(5)
                print("after cancel:", get(sim_id)[:3], flush=True)


if __name__ == "__main__":
    main()
