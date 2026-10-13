"""WebSocket connection manager for streaming simulation frames.

Each connection has one writer: its WebSocket handler (routers/simulations.py). Simulation
threads never send on a socket; they only post small events (status messages, "new frame")
to each connection's queue, and the handler sends the run's frames in order from the run
itself. Before 2026-10-12 every frame was sent by its own task, concurrently with the handler:
on a slow link a large frame's send was still waiting when the handler sent
``simulation.completed`` and closed the socket, so the last frames of a run never arrived
(owner field test 2026-10-12: restarts "capped" at 1-2 h, runs ending one frame short).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

#: Queue item meaning "the run has new frames" (the handler reads them from the run)
FRAMES_ADDED: dict[str, Any] = {"type": "_frames_added"}


class ConnectionManager:
    """Connections per simulation ID, each with a queue of events for its handler.

    Thread-safe: simulation threads post events with ``broadcast_from_thread``; the queues
    are filled on the asyncio event loop.
    """

    def __init__(self) -> None:
        self._connections: dict[str, dict[WebSocket, asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, sim_id: str, websocket: WebSocket) -> asyncio.Queue:
        """Accept the socket; returns its event queue (read by the socket's handler only)."""
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        self._connections.setdefault(sim_id, {})[websocket] = queue
        logger.info("WebSocket connected for simulation %s", sim_id)
        return queue

    async def disconnect(self, sim_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(sim_id)
        if conns is not None:
            conns.pop(websocket, None)
            if not conns:
                del self._connections[sim_id]

    def _post(self, sim_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._connections.get(sim_id, {}).values()):
            queue.put_nowait(event)

    async def send_event(self, sim_id: str, event: dict[str, Any]) -> None:
        """Queue an event for every connection of a simulation (sent by its handler)."""
        self._post(sim_id, event)

    def broadcast_from_thread(self, sim_id: str, event: dict[str, Any]) -> None:
        """Thread-safe: queue ``event`` for the simulation's connections."""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._post, sim_id, event)
        except RuntimeError:  # loop closed meanwhile (shutdown)
            pass

    def frames_added_from_thread(self, sim_id: str) -> None:
        """Thread-safe: tell the simulation's handlers that the run has new frames."""
        self.broadcast_from_thread(sim_id, FRAMES_ADDED)
