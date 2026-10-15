"""Multi-day progress messages and WebSocket heartbeat (round-3 field test R6, R12, Claude in
Chrome tester, 2026-10-13): a 3-day run sent no status for its whole half hour ("Starting…"
throughout) and the socket carried nothing between days."""

from __future__ import annotations

import time

from starlette.testclient import TestClient

from firesim_api.main import create_app
from firesim_api.routers import simulations as sim_router
from firesim_api.routers.simulations import _status_event
from firesim_api.schemas.simulation import MultiDaySimulationCreate, SimulationStatus
from firesim_api.services.runner import SimulationRunner

DAY = {"wind_speed": 15.0, "wind_direction": 270.0, "temperature": 25.0, "relative_humidity": 30.0}


def _params(n_days: int = 2) -> MultiDaySimulationCreate:
    return MultiDaySimulationCreate(
        ignition_lat=53.5, ignition_lng=-113.5, days=[DAY] * n_days,
        fwi_overrides={"ffmc": 90.0, "dmc": 45.0, "dc": 300.0},
        snapshot_interval_minutes=120.0, fuel_type="C2",
    )


def _wait(runner: SimulationRunner, sim_id: str, timeout_s: float = 180.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = runner.get(sim_id)
        if run.status not in (SimulationStatus.PENDING, SimulationStatus.RUNNING):
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_status_event_carries_the_day_for_multi_day_runs():
    assert "day" not in _status_event("x", "spread", 0.5)
    ev = _status_event("x", "spread", 0.4567, day=2, n_days=3)
    assert ev == {"type": "simulation.status", "simulation_id": "x", "phase": "spread",
                  "progress": 0.457, "day": 2, "n_days": 3}


def test_multiday_reports_each_day_and_progress_over_the_run():
    runner = SimulationRunner()
    seen: list[tuple[str, float | None, int | None]] = []
    sim_id = runner.create_multiday(
        _params(2), on_status=lambda sid, phase, p: seen.append((phase, p, runner.get(sid).day)))
    run = _wait(runner, sim_id)
    assert run.status == SimulationStatus.COMPLETED
    assert run.n_days == 2
    assert seen[0][0] == "loading"
    spread = [(p, d) for ph, p, d in seen if ph == "spread"]
    assert (0.0, 1) in spread and (0.5, 2) in spread  # day 2 starts half-way
    assert [p for p, _ in spread] == sorted(p for p, _ in spread)
    assert run.frames[-1].time_hours == 48.0


def test_get_reports_day_and_days():
    with TestClient(create_app()) as tc:
        sid = tc.post("/api/v1/simulations/multiday", json=_params(2).model_dump(mode="json")).json()["simulation_id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            data = tc.get(f"/api/v1/simulations/{sid}").json()
            if data["status"] != "running":
                break
            time.sleep(0.2)
        assert data["status"] == "completed"
        assert data["n_days"] == 2 and data["day"] == 2
        assert data["frames"][-1]["time_hours"] == 48.0


def test_ws_heartbeat_keeps_a_silent_socket_busy(monkeypatch):
    """With nothing new to send for HEARTBEAT_S, the handler re-sends the run's status."""
    monkeypatch.setattr(sim_router, "HEARTBEAT_S", 0.0)
    with TestClient(create_app()) as tc:
        sid = tc.post("/api/v1/simulations/multiday", json=_params(2).model_dump(mode="json")).json()["simulation_id"]
        kinds: list[str] = []
        with tc.websocket_connect(f"/api/v1/simulations/ws/{sid}") as ws:
            while True:
                msg = ws.receive_json()
                kinds.append(msg["type"])
                if msg["type"] in ("simulation.completed", "simulation.error"):
                    break
        assert kinds[-1] == "simulation.completed"
        assert kinds.count("simulation.frame") == len(_wait_frames(tc, sid))
        assert "simulation.status" in kinds


def _wait_frames(tc, sid):
    return tc.get(f"/api/v1/simulations/{sid}").json()["frames"]
