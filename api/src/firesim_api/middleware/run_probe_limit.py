"""Slow down guessing of run IDs: per-IP backoff on "Simulation not found".

``GET /api/v1/simulations/{id}`` (and ``/arrival``, ``/ensemble``, the WebSocket) need no
passphrase, so the run ID is the only key. New IDs are 128 random bits
(``services/run_ids.py``) and cannot practically be guessed, but IDs made before 2026-10-13
are 8 hex characters (about 4.3 x 10^9 values). This middleware counts, per client IP, the
answers under ``/api/v1/simulations/`` that say a run does not exist: an HTTP 404 or 422 whose
``detail`` names a simulation as not found, or a WebSocket closed with code 4004. After
``FREE_MISSES`` such misses each further one locks the IP out for 2, 4, 8 ... seconds (at most
``MAX_LOCK_S``); while locked every request to those paths from the IP gets 429 with
``Retry-After`` (WebSocket: closed with 4029 before it is accepted). The count is forgotten
after ``FORGET_S`` without a miss. Found runs neither count nor clear the count, so mixing in a
known ID does not reset it.

Normal use is not affected: the app polls runs it has just made (found), and reloading an
incident whose runs were lost in a restart gives one miss per operational period. The counts
are in memory and reset on restart. ``/api/v1/simulations`` itself (the passphrase-gated
saved-runs list) has its own backoff in ``routers/runs.py``.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

PREFIX = "/api/v1/simulations/"

FREE_MISSES = 20      # not-found answers allowed before the backoff starts
MAX_LOCK_S = 300.0    # longest lock-out (s)
FORGET_S = 900.0      # a count with no miss for this long is dropped
MAX_TRACKED = 10_000  # IPs remembered (least recently missed dropped beyond it)

_lock = threading.Lock()
_misses: dict[str, tuple[int, float, float]] = {}  # ip -> (misses, last miss, locked until)


def reset() -> None:
    """Forget all counts (tests)."""
    with _lock:
        _misses.clear()


def client_ip(scope: Scope) -> str:
    # As routers/runs.py: Fly's proxy sets Fly-Client-IP (overwriting any client value);
    # elsewhere the socket peer. X-Forwarded-For is not used: a client can set it freely.
    conn = HTTPConnection(scope)
    return conn.headers.get("fly-client-ip") or (conn.client.host if conn.client else "?")


def locked_for(ip: str, now: float | None = None) -> float:
    """Seconds the IP is still locked out (0 when not)."""
    now = time.time() if now is None else now
    with _lock:
        entry = _misses.get(ip)
    return max(0.0, entry[2] - now) if entry else 0.0


def record_miss(ip: str, now: float | None = None) -> int:
    """Count one not-found answer for the IP; returns its miss count."""
    now = time.time() if now is None else now
    with _lock:
        count, last, _until = _misses.pop(ip, (0, 0.0, 0.0))
        if now - last > FORGET_S:
            count = 0
        count += 1
        extra = count - FREE_MISSES
        until = now + min(2.0 ** extra, MAX_LOCK_S) if extra > 0 else 0.0
        _misses[ip] = (count, now, until)  # re-inserted: dict order = least recently missed first
        while len(_misses) > MAX_TRACKED:
            _misses.pop(next(iter(_misses)))
    if count == FREE_MISSES + 1:
        logger.warning("Run ID probing? %d not-found answers from %s; backing off", count, ip)
    return count


def _is_run_miss(status: int, body: bytes) -> bool:
    if status not in (404, 422):
        return False
    try:
        detail = json.loads(body or b"{}").get("detail")
    except (ValueError, AttributeError):
        return False
    if not isinstance(detail, str):
        return False
    text = detail.lower()
    return text.startswith("simulation") and "not found" in text


class RunProbeLimitMiddleware:
    """Pure ASGI middleware (passes streaming bodies through; buffers only 404/422 bodies)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not scope.get("path", "").startswith(PREFIX):
            await self.app(scope, receive, send)
            return
        ip = client_ip(scope)
        wait = locked_for(ip)
        if wait > 0:
            await self._refuse(scope, receive, send, int(wait) + 1)
            return
        if scope["type"] == "websocket":
            await self.app(scope, receive, self._ws_send(ip, send))
        else:
            await self._http(scope, receive, send, ip)

    def _ws_send(self, ip: str, send: Send) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "websocket.close" and message.get("code") == 4004:
                record_miss(ip)
            await send(message)
        return wrapped

    async def _http(self, scope: Scope, receive: Receive, send: Send, ip: str) -> None:
        start: Message | None = None
        held: list[Message] = []

        async def wrapped(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                if message["status"] in (404, 422):
                    start = message  # hold until the body is complete
                    return
                await send(message)
                return
            if start is None or message["type"] != "http.response.body":
                await send(message)
                return
            held.append(message)
            if message.get("more_body", False):
                return
            body = b"".join(m.get("body", b"") for m in held)
            if _is_run_miss(start["status"], body):
                record_miss(ip)
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, wrapped)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, wait: int) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4029,
                        "reason": "Too many unknown run IDs"})
            return
        body = json.dumps({"detail": f"Too many unknown run IDs; try again in {wait} s."}).encode()
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", str(wait).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
