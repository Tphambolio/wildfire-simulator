"""Ensemble concurrency cap (FIRESIM_MAX_CONCURRENT_ENSEMBLES): later ensembles wait, pending."""

import threading
import time

from firesim_api.services import runner as runner_mod
from firesim_api.services.runner import SimulationRunner


class _Run:
    def __init__(self) -> None:
        self._ensemble_cancel = threading.Event()
        self.ensemble_status = None


def _start(run, ran: list) -> threading.Thread:
    t = threading.Thread(target=SimulationRunner._run_ensemble,
                         args=(run, None, None, None, None, None), daemon=True)
    t.start()
    return t


def test_second_ensemble_waits_pending_until_a_slot_frees(monkeypatch):
    ran: list = []
    monkeypatch.setattr(runner_mod, "_ENSEMBLE_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(SimulationRunner, "_run_ensemble_slot",
                        staticmethod(lambda run, *a: (ran.append(run), setattr(run, "ensemble_status", "completed"))))
    runner_mod._ENSEMBLE_SLOTS.acquire()  # another ensemble is computing
    run = _Run()
    t = _start(run, ran)
    time.sleep(0.8)
    assert run.ensemble_status is None and not ran  # waiting: the API reports "pending"
    runner_mod._ENSEMBLE_SLOTS.release()
    t.join(5)
    assert ran == [run] and run.ensemble_status == "completed"
    assert runner_mod._ENSEMBLE_SLOTS.acquire(blocking=False)  # slot released again


def test_waiting_ensemble_can_be_cancelled(monkeypatch):
    ran: list = []
    monkeypatch.setattr(runner_mod, "_ENSEMBLE_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(SimulationRunner, "_run_ensemble_slot",
                        staticmethod(lambda run, *a: ran.append(run)))
    runner_mod._ENSEMBLE_SLOTS.acquire()
    run = _Run()
    t = _start(run, ran)
    run._ensemble_cancel.set()
    t.join(5)
    assert run.ensemble_status == "cancelled" and not ran
    runner_mod._ENSEMBLE_SLOTS.release()


def test_slot_released_when_the_ensemble_raises(monkeypatch):
    monkeypatch.setattr(runner_mod, "_ENSEMBLE_SLOTS", threading.BoundedSemaphore(1))

    def boom(run, *a):
        raise RuntimeError("x")

    monkeypatch.setattr(SimulationRunner, "_run_ensemble_slot", staticmethod(boom))
    try:
        SimulationRunner._run_ensemble(_Run(), None, None, None, None, None)
    except RuntimeError:
        pass
    assert runner_mod._ENSEMBLE_SLOTS.acquire(blocking=False)
