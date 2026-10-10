"""Run progress for display: phases and spread fraction (WebSocket ``simulation.status``,
GET ``phase``/``progress``), and cancelling a run while the grid model computes."""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from firesim_api.main import create_app
from firesim_api.routers.simulations import _status_event
from firesim_api.schemas.simulation import SimulationCreate, SimulationStatus
from firesim_api.services.runner import RUN_PHASES, RunCancelled, SimulationRun, SimulationRunner


def _params(**kw) -> SimulationCreate:
    base = dict(
        ignition_lat=53.5,
        ignition_lng=-113.5,
        weather={"wind_speed": 20.0, "wind_direction": 270.0, "temperature": 25.0,
                 "relative_humidity": 30.0, "precipitation_24h": 0.0},
        fwi_overrides={"ffmc": 90.0, "dmc": 45.0, "dc": 300.0},
        duration_hours=1.0,
        snapshot_interval_minutes=30.0,
        fuel_type="O1a",
        use_ca_mode=True,  # no fuel grid configured: synthetic demo grid, grid model
        fuel_modifiers={"grass_cure": 60.0},  # O-1a burns: curing has no default in summer (M1)
    )
    base.update(kw)
    return SimulationCreate(**base)


def _wait(runner: SimulationRunner, sim_id: str, timeout_s: float = 120.0) -> SimulationRun:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = runner.get(sim_id)
        if run.status not in (SimulationStatus.PENDING, SimulationStatus.RUNNING):
            return run
        time.sleep(0.1)
    raise TimeoutError(sim_id)


class TestSetPhase:
    def test_notifies_on_phase_change_and_throttles_progress(self):
        run = SimulationRun("abc", None)
        events = []
        run.on_status = lambda sid, phase, p: events.append((sid, phase, p))
        run.set_phase("loading")
        run.set_phase("loading")  # same phase, no progress: not repeated
        run.set_phase("spread", 0.0)
        run.set_phase("spread", 0.01)  # under 2 points: throttled
        run.set_phase("spread", 0.05)
        run.set_phase("finishing")
        assert events == [("abc", "loading", None), ("abc", "spread", 0.0), ("abc", "spread", 0.05),
                          ("abc", "finishing", None)]
        assert run.phase == "finishing" and run.progress == pytest.approx(0.05)

    def test_raises_when_cancelled(self):
        run = SimulationRun("abc", None)
        run.cancel()
        with pytest.raises(RunCancelled):
            run.set_phase("spread", 0.5)

    def test_a_failing_callback_never_fails_the_run(self):
        run = SimulationRun("abc", None)

        def boom(*_):
            raise RuntimeError("socket gone")

        run.on_status = boom
        run.set_phase("loading")  # no exception
        assert run.phase == "loading"


class TestRunnerPhases:
    def test_grid_run_reports_loading_spread_progress_and_finishing(self):
        runner = SimulationRunner()
        events: list[tuple[str, float | None]] = []
        sim_id = runner.create(_params(), on_status=lambda _sid, ph, p: events.append((ph, p)))
        run = _wait(runner, sim_id)
        assert run.status == SimulationStatus.COMPLETED, run.error
        phases = [ph for ph, _ in events]
        assert all(ph in RUN_PHASES for ph in phases)
        assert phases[0] == "loading"
        assert "spread" in phases and phases[-1] == "finishing"
        fractions = [p for ph, p in events if ph == "spread"]
        assert fractions == sorted(fractions) and all(0.0 <= f <= 1.0 for f in fractions)
        assert len(fractions) >= 2  # the level set reports as it advances
        # Status messages are small: phase and a rounded fraction only
        ev = _status_event(sim_id, "spread", 0.123456)
        assert ev == {"type": "simulation.status", "simulation_id": sim_id, "phase": "spread", "progress": 0.123}

    def test_progress_does_not_change_the_result(self):
        runner = SimulationRunner()
        a = _wait(runner, runner.create(_params()))
        b = _wait(runner, runner.create(_params(), on_status=lambda *_: None))
        fa, fb = a.get_frames(), b.get_frames()
        assert [f.area_ha for f in fa] == [f.area_ha for f in fb]

    def test_cancel_stops_a_grid_run_mid_computation(self):
        runner = SimulationRunner()
        cancelled = {"done": False}

        def on_status(sid, phase, p):
            if phase == "spread" and p and p > 0.1 and not cancelled["done"]:
                cancelled["done"] = True
                runner.get(sid).cancel()

        sim_id = runner.create(_params(duration_hours=3.0), on_status=on_status)
        run = _wait(runner, sim_id)
        assert cancelled["done"]
        assert run.status == SimulationStatus.CANCELLED
        assert run.get_frames() == []  # stopped before the frames were built


class TestEndpoints:
    def test_get_reports_phase_and_progress_and_ws_streams_status(self):
        app = create_app()
        with TestClient(app) as client:
            resp = client.post("/api/v1/simulations", json=_params().model_dump(mode="json"))
            sim_id = resp.json()["simulation_id"]
            assert resp.json()["phase"] is None  # backward compatible: optional fields
            statuses, completed = [], False
            with client.websocket_connect(f"/api/v1/simulations/ws/{sim_id}") as ws:
                for _ in range(500):
                    msg = ws.receive_json()
                    if msg["type"] == "simulation.status":
                        statuses.append(msg)
                        assert set(msg) == {"type", "simulation_id", "phase", "progress"}
                    elif msg["type"] == "simulation.completed":
                        completed = True
                        break
            assert completed
            data = client.get(f"/api/v1/simulations/{sim_id}").json()
            assert data["status"] == "completed"
            assert data["phase"] == "finishing"
            assert data["progress"] == pytest.approx(1.0)
            # Status messages may all arrive before the socket opens on a fast run; when
            # they do arrive they name a known phase
            assert all(m["phase"] in RUN_PHASES for m in statuses)
