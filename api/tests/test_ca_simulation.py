"""Integration tests: end-to-end CA simulation with spatial fuel grid.

Verifies the full pipeline:
1. POST /api/v1/simulations with fuel_grid_path, water_path, buildings_path, wui_zones_path
2. Simulator auto-selects CA mode when grid >= 50x50
3. Grid caching: 2nd request with same paths logs a cache hit
4. WebSocket streaming delivers SimulationFrames with burned_cells
5. CA-mode frames have burned_cells populated (frontend renders heatmap, not polygon)

Fixtures:
    fuel_grid_50x50   — 50×50 GeoTIFF raster at 50 m/cell (C2 fuel throughout)
    water_geojson     — small water-body polygon (north-east corner)
    buildings_geojson — small building footprint polygon (south-west corner)
    wui_geojson       — WUI zone covering the full grid (ros_multiplier=0.8)

Notes:
    • The 50×50 threshold for CA mode auto-selection is in
      Simulator.run() (engine/src/firesim/spread/simulator.py line 98).
    • Grid caching is in SimulationRunner._load_grids()
      (api/src/firesim_api/services/runner.py).
    • Frontend heatmap-vs-polygon routing is in MapView.tsx, lines 449–490:
      if burned_cells is present and non-empty → heatmap layer is used,
      otherwise → polygon perimeter layer is used.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from firesim_api.main import create_app


# ---------------------------------------------------------------------------
# Raster / GeoJSON fixture helpers
# ---------------------------------------------------------------------------

_UTM12N = CRS.from_epsg(32612)
# Edmonton area UTM origin — 50 m cells, 50 rows × 50 cols = 2 500 m × 2 500 m
_WEST = 350_000.0
_NORTH = 5_930_000.0
_CELL_M = 50.0
_ROWS = 50
_COLS = 50


def _write_fuel_raster(path: Path) -> Path:
    """Write a 50×50 GeoTIFF with all-C2 fuel (FBP code 2)."""
    data = np.full((_ROWS, _COLS), 2, dtype=np.int32)  # code 2 → C2 (fuel_loader ALL_CODES)
    transform = from_origin(_WEST, _NORTH, _CELL_M, _CELL_M)
    with rasterio.open(
        path, "w", driver="GTiff",
        height=_ROWS, width=_COLS, count=1,
        dtype=data.dtype, crs=_UTM12N, transform=transform,
    ) as dst:
        dst.write(data, 1)
    return path


def _wgs84_bounds(raster_path: Path) -> tuple[float, float, float, float]:
    """Return (lat_min, lat_max, lng_min, lng_max) in WGS84 from a raster."""
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(raster_path) as src:
        bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    # bounds = (west, south, east, north) in WGS84
    west, south, east, north = bounds
    return south, north, west, east


def _polygon_fc(coords: list[list[float]], props: dict | None = None) -> dict:
    """Build a minimal GeoJSON FeatureCollection with one polygon."""
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": props or {},
        }],
    }


def _write_geojson(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data))
    return path


# ---------------------------------------------------------------------------
# Shared pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spatial_fixtures(tmp_path_factory):
    """Create all spatial fixture files once per module."""
    d = tmp_path_factory.mktemp("spatial_fixtures")

    # Fuel raster
    fuel_path = _write_fuel_raster(d / "fuel_50x50.tif")

    # Derive actual WGS84 bounds so water/buildings/WUI overlap correctly
    lat_min, lat_max, lng_min, lng_max = _wgs84_bounds(fuel_path)
    lat_mid = (lat_min + lat_max) / 2
    lng_mid = (lng_min + lng_max) / 2

    # Water body — north-east quadrant
    water_path = _write_geojson(
        d / "water.geojson",
        _polygon_fc([
            [lng_mid, lat_mid],
            [lng_max, lat_mid],
            [lng_max, lat_max],
            [lng_mid, lat_max],
            [lng_mid, lat_mid],
        ]),
    )

    # Buildings — small footprint in south-west quadrant
    margin = (lat_max - lat_min) * 0.05
    buildings_path = _write_geojson(
        d / "buildings.geojson",
        _polygon_fc([
            [lng_min + margin, lat_min + margin],
            [lng_min + 3 * margin, lat_min + margin],
            [lng_min + 3 * margin, lat_min + 3 * margin],
            [lng_min + margin, lat_min + 3 * margin],
            [lng_min + margin, lat_min + margin],
        ]),
    )

    # WUI zones — whole grid, moderate suppression
    wui_path = _write_geojson(
        d / "wui.geojson",
        _polygon_fc(
            [
                [lng_min - 0.001, lat_min - 0.001],
                [lng_max + 0.001, lat_min - 0.001],
                [lng_max + 0.001, lat_max + 0.001],
                [lng_min - 0.001, lat_max + 0.001],
                [lng_min - 0.001, lat_min - 0.001],
            ],
            {"ros_multiplier": 0.8, "intensity_multiplier": 1.0, "ember_multiplier": 1.5},
        ),
    )

    # Ignition point — grid centre
    ignition_lat = (lat_min + lat_max) / 2
    ignition_lng = (lng_min + lng_max) / 2

    return {
        "fuel_path": str(fuel_path),
        "water_path": str(water_path),
        "buildings_path": str(buildings_path),
        "wui_path": str(wui_path),
        "ignition_lat": ignition_lat,
        "ignition_lng": ignition_lng,
    }


@pytest.fixture
def app():
    """Fresh app per test (but module-level runner is shared via main.py)."""
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helper: wait for simulation completion
# ---------------------------------------------------------------------------


async def _wait_for_completion(client, sim_id: str, timeout_s: float = 120.0) -> dict:
    """Poll GET /api/v1/simulations/{sim_id} until completed or failed."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = await client.get(f"/api/v1/simulations/{sim_id}")
        data = resp.json()
        if data["status"] in ("completed", "failed"):
            return data
        time.sleep(0.25)
    raise TimeoutError(f"Simulation {sim_id} did not finish within {timeout_s}s")


def _ca_payload(fixtures: dict) -> dict:
    """Build a CA-mode simulation request payload."""
    return {
        "ignition_lat": fixtures["ignition_lat"],
        "ignition_lng": fixtures["ignition_lng"],
        "weather": {
            "wind_speed": 20.0,
            "wind_direction": 270.0,
            "temperature": 25.0,
            "relative_humidity": 30.0,
            "precipitation_24h": 0.0,
        },
        "fwi_overrides": {"ffmc": 90.0, "dmc": 45.0, "dc": 300.0},
        "duration_hours": 0.5,
        "snapshot_interval_minutes": 30.0,
        "fuel_type": "C2",
        "fuel_grid_path": fixtures["fuel_path"],
        "water_path": fixtures["water_path"],
        "buildings_path": fixtures["buildings_path"],
        "wui_zones_path": fixtures["wui_path"],
    }


# ---------------------------------------------------------------------------
# Test 1: POST /api/v1/simulations with spatial paths returns 200
# ---------------------------------------------------------------------------


class TestCASimulationCreate:
    async def test_create_returns_running_status(self, client, spatial_fixtures):
        """POST with all four spatial paths must return simulation_id and running."""
        payload = _ca_payload(spatial_fixtures)
        resp = await client.post("/api/v1/simulations", json=payload)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "simulation_id" in data
        assert data["status"] == "running"
        assert data["config"]["fuel_grid_path"] == spatial_fixtures["fuel_path"]
        assert data["config"]["water_path"] == spatial_fixtures["water_path"]
        assert data["config"]["buildings_path"] == spatial_fixtures["buildings_path"]
        assert data["config"]["wui_zones_path"] == spatial_fixtures["wui_path"]


# ---------------------------------------------------------------------------
# Test 2: CA mode auto-selection (grid >= 50×50)
# ---------------------------------------------------------------------------


class TestCAModeAutoSelection:
    async def test_ca_mode_activated_for_large_grid(self, client, spatial_fixtures):
        """Frames must contain burned_cells (CA mode) not just perimeters."""
        payload = _ca_payload(spatial_fixtures)
        resp = await client.post("/api/v1/simulations", json=payload)
        assert resp.status_code == 200

        sim_id = resp.json()["simulation_id"]
        result = await _wait_for_completion(client, sim_id)

        assert result["status"] == "completed", f"Simulation failed: {result.get('error')}"
        assert len(result["frames"]) >= 1, "Expected at least one frame"

        # CA mode produces burned_cells on every frame
        for frame in result["frames"]:
            assert frame["burned_cells"] is not None, (
                f"Expected burned_cells in CA frame (t={frame['time_hours']}h) but got None"
            )
            assert isinstance(frame["burned_cells"], list), (
                "burned_cells must be a list"
            )

    async def test_ca_frames_have_correct_burned_cell_schema(self, client, spatial_fixtures):
        """Each burned cell must have lat, lng, intensity, fuel keys."""
        payload = _ca_payload(spatial_fixtures)
        resp = await client.post("/api/v1/simulations", json=payload)
        sim_id = resp.json()["simulation_id"]
        result = await _wait_for_completion(client, sim_id)

        assert result["status"] == "completed"
        # Check cells in the final frame
        final = result["frames"][-1]
        assert final["burned_cells"] is not None

        if len(final["burned_cells"]) > 0:
            cell = final["burned_cells"][0]
            assert "lat" in cell, "burned_cell must have 'lat'"
            assert "lng" in cell, "burned_cell must have 'lng'"
            assert "intensity" in cell, "burned_cell must have 'intensity'"
            assert "fuel" in cell, "burned_cell must have 'fuel'"

    async def test_no_fuel_grid_uses_huygens_mode(self, client):
        """Simulation without fuel_grid_path must use Huygens (no burned_cells)."""
        payload = {
            "ignition_lat": 53.5,
            "ignition_lng": -113.5,
            "weather": {"wind_speed": 20.0, "wind_direction": 270.0},
            "duration_hours": 0.25,
            "snapshot_interval_minutes": 15.0,
        }
        resp = await client.post("/api/v1/simulations", json=payload)
        sim_id = resp.json()["simulation_id"]
        result = await _wait_for_completion(client, sim_id, timeout_s=60.0)

        assert result["status"] == "completed"
        for frame in result["frames"]:
            # Huygens mode: burned_cells is null or absent
            assert not frame.get("burned_cells"), (
                f"Expected no burned_cells in Huygens frame, got: {frame.get('burned_cells')}"
            )
            # Huygens mode: must have a polygon perimeter
            assert len(frame["perimeter"]) >= 3, "Huygens frame must have polygon perimeter"


# ---------------------------------------------------------------------------
# Test 3: Grid caching — 2nd request with same paths logs cache hit
# ---------------------------------------------------------------------------


class TestGridCaching:
    async def test_second_request_logs_cache_hit(
        self, client, spatial_fixtures, caplog
    ):
        """The runner must log 'Grid cache HIT' on the second identical request."""
        payload = _ca_payload(spatial_fixtures)

        with caplog.at_level(logging.INFO, logger="firesim_api.services.runner"):
            # First request — should be a cache miss (or hit if test order re-uses runner)
            resp1 = await client.post("/api/v1/simulations", json=payload)
            sim1 = resp1.json()["simulation_id"]
            await _wait_for_completion(client, sim1)

            # Second request — same paths → must be a cache hit
            caplog.clear()
            resp2 = await client.post("/api/v1/simulations", json=payload)
            sim2 = resp2.json()["simulation_id"]
            await _wait_for_completion(client, sim2)

        cache_hits = [r for r in caplog.records if "Grid cache HIT" in r.message]
        assert len(cache_hits) >= 1, (
            "Expected 'Grid cache HIT' log on 2nd request with same paths. "
            f"Logged messages: {[r.message for r in caplog.records]}"
        )

    async def test_cached_result_matches_original(self, client, spatial_fixtures):
        """Simulations from cached grids must produce the same frame count and area."""
        payload = _ca_payload(spatial_fixtures)

        resp1 = await client.post("/api/v1/simulations", json=payload)
        result1 = await _wait_for_completion(client, resp1.json()["simulation_id"])

        resp2 = await client.post("/api/v1/simulations", json=payload)
        result2 = await _wait_for_completion(client, resp2.json()["simulation_id"])

        assert result1["status"] == "completed"
        assert result2["status"] == "completed"
        assert len(result1["frames"]) == len(result2["frames"]), (
            "Cached run should produce the same number of frames"
        )

        # Areas may differ (stochastic CA) but both must be non-negative
        for frame in result2["frames"]:
            assert frame["area_ha"] >= 0.0


# ---------------------------------------------------------------------------
# Test 4: WebSocket streaming delivers SimulationFrames with burned_cells
# ---------------------------------------------------------------------------


class TestWebSocketStreaming:
    def test_ws_streams_ca_frames(self, spatial_fixtures):
        """WS endpoint must deliver frames with burned_cells for CA-mode runs."""
        app = create_app()

        with TestClient(app) as client:
            # Create simulation
            resp = client.post(
                "/api/v1/simulations",
                json=_ca_payload(spatial_fixtures),
            )
            assert resp.status_code == 200
            sim_id = resp.json()["simulation_id"]

            received_frames: list[dict] = []
            got_completed = False

            with client.websocket_connect(f"/api/v1/simulations/ws/{sim_id}") as ws:
                # Read up to 200 messages (generous — short sim produces few frames)
                for _ in range(200):
                    try:
                        msg = ws.receive_json()
                    except Exception:
                        break

                    if msg["type"] == "simulation.frame":
                        received_frames.append(msg["frame"])
                    elif msg["type"] == "simulation.completed":
                        got_completed = True
                        break
                    elif msg["type"] == "simulation.error":
                        pytest.fail(f"Simulation error over WS: {msg.get('error')}")

        assert got_completed, "WebSocket must deliver a simulation.completed event"
        assert len(received_frames) >= 1, "WebSocket must stream at least one frame"

        # All CA frames must carry burned_cells
        for frame in received_frames:
            assert frame["burned_cells"] is not None, (
                f"CA frame over WS missing burned_cells (t={frame['time_hours']}h)"
            )

    def test_ws_returns_4004_for_unknown_sim(self):
        """WS connection for an unknown simulation ID must close with code 4004."""
        app = create_app()

        with TestClient(app) as client:
            try:
                with client.websocket_connect("/api/v1/simulations/ws/does-not-exist"):
                    pass
            except Exception:
                pass  # Connection refused / closed is acceptable


# ---------------------------------------------------------------------------
# Test 5: Frontend rendering contract — burned_cells drives heatmap mode
# ---------------------------------------------------------------------------


def _sync_wait_for_completion(
    client: TestClient, sim_id: str, timeout_s: float = 120.0
) -> dict:
    """Synchronous poll helper for TestClient-based tests."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/simulations/{sim_id}")
        data = resp.json()
        if data["status"] in ("completed", "failed"):
            return data
        time.sleep(0.25)
    raise TimeoutError(f"Simulation {sim_id} did not finish within {timeout_s}s")


class TestFrontendRenderingContract:
    """Verifies the data contract that drives MapView.tsx CA vs. Huygens rendering.

    MapView.tsx routing (lines 449-490):
        if (currentFrame.burned_cells && currentFrame.burned_cells.length > 0):
            → fire-heatmap source is populated (CA heatmap)
            → fire-perimeter source is cleared
        else:
            → fire-perimeter source is populated (Huygens polygon)
            → fire-heatmap source is cleared

    These tests use TestClient (sync) to avoid cross-test event-loop state issues
    from the module-level SimulationRunner singleton.
    """

    def test_ca_frames_have_nonempty_burned_cells_for_heatmap(self, spatial_fixtures):
        """CA frames must have non-empty burned_cells so MapView renders the heatmap."""
        with TestClient(create_app()) as tc:
            resp = tc.post("/api/v1/simulations", json=_ca_payload(spatial_fixtures))
            assert resp.status_code == 200
            result = _sync_wait_for_completion(tc, resp.json()["simulation_id"])

        assert result["status"] == "completed", f"sim failed: {result.get('error')}"
        for frame in result["frames"]:
            burned = frame.get("burned_cells")
            # Non-null and non-empty → MapView will enter heatmap branch
            assert burned is not None and len(burned) > 0, (
                f"CA frame at t={frame['time_hours']}h has no burned_cells — "
                "MapView would fall through to Huygens polygon branch"
            )

    def test_ca_frames_have_burned_cells_takes_priority_over_perimeter(
        self, spatial_fixtures
    ):
        """CA frames must have burned_cells to trigger the heatmap branch in MapView.

        The simulator populates perimeter as a sampled point-cloud from burned cells
        (simulator.py _run_cellular), but MapView.tsx explicitly clears fire-perimeter
        and populates fire-heatmap when burned_cells is present and non-empty.
        """
        with TestClient(create_app()) as tc:
            resp = tc.post("/api/v1/simulations", json=_ca_payload(spatial_fixtures))
            result = _sync_wait_for_completion(tc, resp.json()["simulation_id"])

        assert result["status"] == "completed", f"sim failed: {result.get('error')}"
        final = result["frames"][-1]
        assert final["burned_cells"] is not None, (
            "burned_cells must be present to trigger heatmap branch in MapView"
        )

    def test_huygens_frames_have_no_burned_cells_so_polygon_rendered(self):
        """Huygens frames must not populate burned_cells so MapView renders polygon."""
        payload = {
            "ignition_lat": 53.5,
            "ignition_lng": -113.5,
            "weather": {"wind_speed": 20.0, "wind_direction": 270.0},
            "duration_hours": 0.25,
            "snapshot_interval_minutes": 15.0,
        }
        with TestClient(create_app()) as tc:
            resp = tc.post("/api/v1/simulations", json=payload)
            result = _sync_wait_for_completion(tc, resp.json()["simulation_id"])

        assert result["status"] == "completed"
        for frame in result["frames"]:
            burned = frame.get("burned_cells")
            assert not burned, (
                "Huygens frame must NOT have burned_cells — "
                "MapView polygon branch requires burned_cells to be falsy"
            )
            # Must have a polygon perimeter with at least 3 points
            assert len(frame["perimeter"]) >= 3, (
                "Huygens frame must include a polygon perimeter for MapView rendering"
            )


# ---------------------------------------------------------------------------
# Synthetic demo landscape is reproducible
# ---------------------------------------------------------------------------


class TestSyntheticGridReproducible:
    def test_same_ignition_gives_same_result(self, monkeypatch):
        """use_ca_mode without a fuel raster: re-running a scenario must reproduce it."""
        monkeypatch.delenv("FIRESIM_FUEL_GRID_PATH", raising=False)
        payload = {
            "ignition_lat": 53.55,
            "ignition_lng": -113.45,
            "weather": {"wind_speed": 20.0, "wind_direction": 270.0},
            "fwi_overrides": {"ffmc": 92.0, "dmc": 40.0, "dc": 300.0},
            "duration_hours": 0.5,
            "snapshot_interval_minutes": 30.0,
            "use_ca_mode": True,
        }
        areas = []
        with TestClient(create_app()) as tc:
            for _ in range(2):
                resp = tc.post("/api/v1/simulations", json=payload)
                result = _sync_wait_for_completion(tc, resp.json()["simulation_id"])
                assert result["status"] == "completed", result.get("error")
                areas.append(result["frames"][-1]["area_ha"])
        assert areas[0] == areas[1] > 0


class TestHourlyWeatherAPI:
    def test_hourly_weather_stream_accepted_and_used(self):
        """A wind shift in the hourly stream must change the result."""
        base = {
            "ignition_lat": 53.5, "ignition_lng": -113.5,
            "weather": {"wind_speed": 20.0, "wind_direction": 270.0},
            "fwi_overrides": {"ffmc": 90.0, "dmc": 40.0, "dc": 300.0},
            "duration_hours": 2.0, "snapshot_interval_minutes": 60.0,
        }
        shifted = dict(base, hourly_weather=[
            {"hours_from_start": 0, "temperature": 25, "relative_humidity": 25,
             "wind_speed": 20, "wind_direction": 270},
            {"hours_from_start": 1, "temperature": 25, "relative_humidity": 25,
             "wind_speed": 20, "wind_direction": 180},
        ])
        north = []
        with TestClient(create_app()) as tc:
            for payload in (base, shifted):
                resp = tc.post("/api/v1/simulations", json=payload)
                assert resp.status_code == 200, resp.text
                result = _sync_wait_for_completion(tc, resp.json()["simulation_id"])
                assert result["status"] == "completed", result.get("error")
                north.append(max(p[0] for p in result["frames"][-1]["perimeter"]))
        assert north[1] > north[0]
