"""Shared-passphrase gate with per-IP backoff (saved runs, exercise facilitator).

One ``PassphraseGate`` per secret. The saved-runs list (``routers/runs.py``,
``FIRESIM_RUNS_PASSPHRASE``) and the exercise facilitator endpoints (``routers/exercise.py``,
``FIRESIM_EXERCISE_PASSPHRASE``) each have their own gate, so neither passphrase opens the
other's endpoints and failures against one do not lock out the other (each keeps its own count).

Behaviour (unchanged from the saved-runs gate of 2026-10-12):

* The secret is read from the environment on every request; unset or empty → 403 (the
  endpoints are disabled until the owner sets it).
* The header value is compared by ``hmac.compare_digest`` of the SHA-256 digests of both sides
  (constant time, independent of length). The passphrase is never logged.
* Failed attempts are counted per client IP in memory. After ``FREE_FAILURES`` failures each
  further one locks the IP out for 2, 4, 8 ... seconds (at most ``MAX_LOCK_S``); while locked
  every attempt, right or wrong, is answered 429 with ``Retry-After``. A success clears the
  count. At most ``MAX_TRACKED`` IPs are remembered (least recently failed dropped first).
* Client IP: ``Fly-Client-IP`` (Fly's proxy overwrites any client value), else the socket peer.
  ``X-Forwarded-For`` is not used: a client can set it freely.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

FREE_FAILURES = 5       # failures allowed before the backoff starts
MAX_LOCK_S = 300.0      # longest lock-out (s)
MAX_TRACKED = 10_000    # IPs remembered (oldest dropped beyond it)


def client_ip(request: Request) -> str:
    return request.headers.get("fly-client-ip") or (request.client.host if request.client else "?")


class PassphraseGate:
    """A shared passphrase in ``env`` checked against the request header ``header``."""

    def __init__(self, env: str, header: str, disabled_detail: str, log_name: str) -> None:
        self.env = env
        self.header = header
        self.disabled_detail = disabled_detail
        self.log_name = log_name
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}  # ip -> (failed attempts, locked until)

    def reset(self) -> None:
        """Forget all failed attempts (tests)."""
        with self._lock:
            self._failures.clear()

    def _expected_digest(self) -> bytes | None:
        secret = os.environ.get(self.env) or ""
        return hashlib.sha256(secret.encode("utf-8")).digest() if secret else None

    def require(self, request: Request) -> None:
        """Raise 403 (disabled), 429 (locked out), 401 (missing or wrong passphrase)."""
        expected = self._expected_digest()
        if expected is None:
            raise HTTPException(status_code=403, detail=self.disabled_detail)
        ip = client_ip(request)
        now = time.time()
        with self._lock:
            _count, until = self._failures.get(ip, (0, 0.0))
        if until > now:
            wait = int(until - now) + 1
            raise HTTPException(status_code=429, headers={"Retry-After": str(wait)},
                                detail=f"Too many wrong passphrases; try again in {wait} s.")
        given = request.headers.get(self.header)
        if given and hmac.compare_digest(hashlib.sha256(given.encode("utf-8")).digest(), expected):
            with self._lock:
                self._failures.pop(ip, None)
            return
        with self._lock:
            count = self._failures.get(ip, (0, 0.0))[0] + 1
            extra = count - FREE_FAILURES
            lock = min(2.0 ** extra, MAX_LOCK_S) if extra > 0 else 0.0
            self._failures.pop(ip, None)  # re-insert: dict order = least recently failed first
            self._failures[ip] = (count, now + lock)
            while len(self._failures) > MAX_TRACKED:
                self._failures.pop(next(iter(self._failures)))
        logger.info("%s: %s passphrase from %s (attempt %d)", self.log_name,
                    "missing" if not given else "wrong", ip, count)
        raise HTTPException(status_code=401, detail="Passphrase required" if not given
                            else "Wrong passphrase")
