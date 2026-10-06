#!/usr/bin/env python3
"""Record the deterministic simulation fixture used by the frontend e2e tests.

Runs the real FireSim API in-process (FastAPI TestClient, no server or network), POSTs a
Terwillegar-like grass fire on the Edmonton fuel grid, polls GET /api/v1/simulations/{id}
until it completes, and writes the API response (SimulationResponse with frames, exactly as
the API serialises it) to ``terwillegar_grass_4h.json`` next to this script. The request asks
for ``cells_mode: "incremental"`` as the app does, so frames carry only newly burned cells plus
``cells_offset``. It also records GET /simulations/fuel-grid-image (``fuel_grid_image.json``)
and GET /simulations/{id}/arrival (``arrival.json``).

Because it goes through the API's own routes and schemas, the fixture follows any change to
the frame format (e.g. PR 6 incremental frames): re-run this script and commit the result.

Usage (from the repo root, with engine + api installed or on PYTHONPATH):

    PYTHONPATH=engine/src:api/src python frontend/tests/fixtures/record_fixture.py

or ``npm run fixture:record`` from frontend/ (uses ``python3`` on PATH; set PYTHON to override).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path[:0] = [str(REPO / "engine" / "src"), str(REPO / "api" / "src")]
# Building masks and building exposure need the neighbourhood partition, as in deployment
os.environ.setdefault("FIRESIM_NEIGHBOURHOODS_PATH", str(REPO / "data" / "edmonton_neighbourhoods.geojson"))

from fastapi.testclient import TestClient  # noqa: E402

from firesim_api.main import app  # noqa: E402

OUT = HERE / "terwillegar_grass_4h.json"
FUEL_IMG_OUT = HERE / "fuel_grid_image.json"
ARRIVAL_OUT = HERE / "arrival.json"
DATA = REPO / "data"

# Paths the frontend sends by default (WeatherPanel), mapped to the repo's data/ folder.
# In the committed fixture they are rewritten back to the deployed /app/data/... paths so the
# file does not contain a local path.
LOCAL_TO_DEPLOYED = {str(DATA): "/app/data"}

# Terwillegar-like grass fire: open O-1a/O-1b grass west of Terwillegar/Windermere, the
# nearest continuous grass on the 50 m engine grid (the river-valley grass at Terwillegar Park
# itself is fragmented by water and non-fuel, so a run there stays under 15 ha). Fixed date
# (day_of_year) so the foliar moisture, and hence the run, does not depend on the day it is
# recorded.
REQUEST = {
    "ignition_lat": 53.4606,
    "ignition_lng": -113.6596,
    "cells_mode": "incremental",
    "weather": {
        "wind_speed": 20,
        "wind_direction": 270,
        "temperature": 25,
        "relative_humidity": 30,
        "precipitation_24h": 0,
    },
    "fwi_overrides": {"ffmc": 92, "dmc": 40, "dc": 300},
    "fuel_modifiers": {"grass_cure": 80, "percent_conifer": 50, "day_of_year": 135},
    "duration_hours": 4,
    "snapshot_interval_minutes": 15,
    "fuel_type": "O1a",
    "fuel_grid_path": str(DATA / "Edmonton_FBP_FuelLayer_20251105_10m.tif"),
    "water_path": str(DATA / "edmonton_water_bodies.geojson.gz"),
    "buildings_path": str(DATA / "edmonton_buildings.geojson.gz"),
    "wui_zones_path": None,
    "dem_path": str(DATA / "edmonton_dem.tif"),
    "use_ca_mode": False,
    "enable_spotting": False,
    "spotting_intensity": 1.0,
}

# Size budget: keep the committed JSON small. If it is over, burned_cells are thinned (every
# k-th cell) until it fits.
MAX_BYTES = 3_000_000
SIM_ID = "fixture-terwillegar-4h"


def _thin(frames: list[dict], k: int) -> None:
    """Keep every k-th cell. Incremental frames keep every k-th new cell and get their
    cells_offset recomputed, so the client's rebuild (useSimulation.withAllCells) stays valid."""
    if k <= 1:
        return
    incremental = any((f.get("cells_offset") or 0) > 0 for f in frames)
    total = 0
    for f in frames:
        cells = f.get("burned_cells")
        if cells:
            f["burned_cells"] = cells[::k]
        if incremental and cells is not None:
            f["cells_offset"] = total if (f.get("cells_offset") or 0) > 0 else 0
            total = f["cells_offset"] + len(f["burned_cells"])


def _round_floats(obj, nd: int = 6):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, list):
        return [_round_floats(x, nd) for x in obj]
    if isinstance(obj, dict):
        return {k: _round_floats(v, nd) for k, v in obj.items()}
    return obj


def main() -> int:
    t0 = time.time()
    with TestClient(app) as client:
        r = client.post("/api/v1/simulations", json=REQUEST)
        r.raise_for_status()
        sim_id = r.json()["simulation_id"]
        while True:
            data = client.get(f"/api/v1/simulations/{sim_id}").json()
            if data["status"] not in ("running", "pending", "paused"):
                break
            if time.time() - t0 > 900:
                print("timed out waiting for the simulation", file=sys.stderr)
                return 1
            time.sleep(0.5)
        # The fuel overlay the app requests on load (GET /simulations/fuel-grid-image)
        img = client.get(
            "/api/v1/simulations/fuel-grid-image", params={"fuel_grid_path": REQUEST["fuel_grid_path"]}
        )
        img.raise_for_status()
        FUEL_IMG_OUT.write_text(json.dumps(img.json(), separators=(",", ":")) + "\n")
        # Arrival-time raster of the finished run (404 if the API predates it)
        arr = client.get(f"/api/v1/simulations/{sim_id}/arrival")
        if arr.status_code == 200:
            ARRIVAL_OUT.write_text(json.dumps(arr.json(), separators=(",", ":")) + "\n")
    if data["status"] != "completed":
        print(f"simulation {data['status']}: {data.get('error')}", file=sys.stderr)
        return 1

    data["simulation_id"] = SIM_ID
    cfg = data.get("config") or {}
    for key in ("fuel_grid_path", "water_path", "buildings_path", "wui_zones_path", "dem_path"):
        v = cfg.get(key)
        if isinstance(v, str):
            for local, deployed in LOCAL_TO_DEPLOYED.items():
                v = v.replace(local, deployed)
            cfg[key] = v
    data = _round_floats(data)

    frames = data["frames"]
    full_cells = sum(len(f.get("burned_cells") or []) for f in frames)
    k = 1
    while True:
        trial = json.loads(json.dumps(frames))
        _thin(trial, k)
        size = len(json.dumps({**data, "frames": trial}, separators=(",", ":")))
        if size <= MAX_BYTES or k >= 64:
            break
        k *= 2
    data["frames"] = trial
    data["_fixture"] = {
        "description": "Terwillegar-like grass fire, 4 h, 15 min snapshots, W 20 km/h, FFMC 92 DMC 40 DC 300",
        "regenerate": "PYTHONPATH=engine/src:api/src python frontend/tests/fixtures/record_fixture.py",
        "burned_cells_thinning": k,
        "burned_cells_full_total": full_cells,
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n")

    last = frames[-1]
    print(
        f"wrote {OUT.relative_to(REPO)}: {len(frames)} frames, final area {last['area_ha']} ha, "
        f"fuel {last.get('fuel_breakdown')}, exposure {last.get('building_exposure')}, "
        f"{OUT.stat().st_size / 1e6:.2f} MB (thinning k={k}), {time.time() - t0:.0f} s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
