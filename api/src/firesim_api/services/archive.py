"""Server-side run archive (Phase 1): finished runs survive deploys and restarts.

A run that reaches a terminal state (completed, failed, cancelled; after its ensemble, if any)
is archived in the background:

* **Run index** (SQLite, ``FIRESIM_ARCHIVE_DB``): one row per run with its status, times,
  request, git SHA, fuel and buildings sources and a summary (area, duration, frame count).
* **Payloads** (object store): ``<id>/response.json.gz`` is the body ``GET /simulations/{id}``
  returned when the run finished, so an archived GET is shape-identical to the live one;
  ``arrival.json.gz`` and ``ensemble.json.gz`` the bodies of ``/arrival`` and ``/ensemble``
  when the run had them; ``meta.json`` a copy of the index row (lets the index be rebuilt from
  the store). Backends: ``local`` (filesystem, the default; tests and development) and ``s3``
  (any S3-compatible store, e.g. Cloudflare R2; needs ``boto3``).

Reads go memory first, then the archive (``routers/simulations.py``). Archiving never slows or
breaks a run: it runs on its own worker thread after the run ended, and a failure only logs.

Privacy: only model output and the request are stored (no user, no address). The response
body is the live GET body, unchanged: its per-building fields are the ones the live GET already
returns (building centroid exposure within 500 m and illustrative structure-spread polygons
from public footprints; map display only, never in an export or 209).

Retention (``run_retention``): runs older than ``FIRESIM_ARCHIVE_MAX_AGE_DAYS`` (365) are
deleted, then the oldest runs until the total is under ``FIRESIM_ARCHIVE_MAX_BYTES`` (0 = no
cap). Run at startup and every ``FIRESIM_ARCHIVE_RETENTION_HOURS`` (24) by the app lifespan.

Configuration (environment):

``FIRESIM_ARCHIVE``                 ``0``/``off`` disables the archive (default on)
``FIRESIM_ARCHIVE_DIR``             local root (payloads for the local backend; default DB
                                    location). Default ``/data/firesim-archive`` when ``/data``
                                    is a writable directory (a Fly volume), else a temporary
                                    directory (lost on deploy; a warning is logged)
``FIRESIM_ARCHIVE_DB``              SQLite index path (default ``<dir>/archive.db``)
``FIRESIM_ARCHIVE_BACKEND``         ``local`` (default) or ``s3``
``FIRESIM_ARCHIVE_S3_BUCKET`` / ``_ENDPOINT`` / ``_REGION`` (``auto``) / ``_PREFIX`` (``runs/``)
``FIRESIM_ARCHIVE_S3_ACCESS_KEY_ID`` / ``FIRESIM_ARCHIVE_S3_SECRET_ACCESS_KEY``
``FIRESIM_ARCHIVE_MAX_AGE_DAYS`` (365), ``FIRESIM_ARCHIVE_MAX_BYTES`` (0),
``FIRESIM_ARCHIVE_RETENTION_HOURS`` (24)

An ``s3`` backend without a bucket, credentials or ``boto3`` falls back to ``local`` with a
warning. See ``docs/run-archive.md``.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

#: Payload names (``<id>/<name>``)
RESPONSE = "response.json.gz"
ARRIVAL = "arrival.json.gz"
ENSEMBLE = "ensemble.json.gz"
META = "meta.json"
PAYLOADS = (RESPONSE, ARRIVAL, ENSEMBLE, META)

#: Run IDs the archive accepts (the API makes 8 hex characters; never a path)
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_TERMINAL = {"completed", "failed", "cancelled"}


def valid_id(sim_id: str) -> bool:
    return bool(_ID.match(sim_id or ""))


def _off(value: str | None) -> bool:
    return (value or "").strip().lower() in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Object stores
# ---------------------------------------------------------------------------


@dataclass
class StoredObject:
    key: str
    size: int
    mtime: float  # epoch seconds


class LocalBackend:
    """Payloads as files under ``root`` (``<root>/runs/<id>/<name>``)."""

    name = "local"

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root) / "runs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"bad archive key {key!r}")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        try:
            return p.read_bytes()
        except FileNotFoundError:
            return None

    def delete(self, key: str) -> None:
        p = self._path(key)
        p.unlink(missing_ok=True)
        try:
            p.parent.rmdir()  # the run's folder, once empty
        except OSError:
            pass

    def list(self) -> Iterator[StoredObject]:
        for p in self.root.glob("*/*"):
            if p.is_file() and not p.name.endswith(".tmp"):
                st = p.stat()
                yield StoredObject(f"{p.parent.name}/{p.name}", st.st_size, st.st_mtime)


class S3Backend:
    """Payloads in an S3-compatible bucket (Cloudflare R2: endpoint
    ``https://<account>.r2.cloudflarestorage.com``, region ``auto``) under ``prefix``."""

    name = "s3"

    def __init__(self, bucket: str, prefix: str = "runs/", client: Any = None, **client_kwargs) -> None:
        self.bucket = bucket
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        if client is None:
            import boto3  # optional dependency (pip install firesim-api[archive-s3])
            from botocore.config import Config

            client = boto3.client(
                "s3", config=Config(retries={"max_attempts": 3, "mode": "standard"},
                                    connect_timeout=5, read_timeout=30),
                **{k: v for k, v in client_kwargs.items() if v})
        self.client = client

    @staticmethod
    def _missing(exc: Exception) -> bool:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        return code in {"NoSuchKey", "404", "NotFound"} or type(exc).__name__ == "NoSuchKey"

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data)

    def get(self, key: str) -> bytes | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)
        except Exception as exc:  # noqa: BLE001 - botocore raises ClientError subclasses
            if self._missing(exc):
                return None
            raise
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)

    def list(self) -> Iterator[StoredObject]:
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                kw["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kw)
            for o in page.get("Contents", []) or []:
                lm = o.get("LastModified")
                yield StoredObject(o["Key"][len(self.prefix):], int(o.get("Size", 0)),
                                   lm.timestamp() if hasattr(lm, "timestamp") else float(lm or 0))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")


# ---------------------------------------------------------------------------
# Run index
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    kind TEXT,
    status TEXT NOT NULL,
    created_at REAL,
    finished_at REAL NOT NULL,
    archived_at REAL NOT NULL,
    request_json TEXT,
    git_sha TEXT,
    fuel_source_kind TEXT,
    fuel_source_label TEXT,
    buildings_source_kind TEXT,
    buildings_source_label TEXT,
    area_ha REAL,
    duration_hours REAL,
    frame_count INTEGER,
    days INTEGER,
    ensemble_status TEXT,
    objects TEXT NOT NULL,
    bytes_total INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_finished ON runs (finished_at);
"""

_COLUMNS = ("id", "kind", "status", "created_at", "finished_at", "archived_at", "request_json",
            "git_sha", "fuel_source_kind", "fuel_source_label", "buildings_source_kind",
            "buildings_source_label", "area_ha", "duration_hours", "frame_count", "days",
            "ensemble_status", "objects", "bytes_total")


class RunIndex:
    """SQLite index of archived runs (WAL mode, so Litestream can replicate it)."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def upsert(self, row: dict) -> None:
        vals = [row.get(c) for c in _COLUMNS]
        with self._lock, self._connect() as db:
            db.execute(
                f"INSERT OR REPLACE INTO runs ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})", vals)

    def get(self, sim_id: str) -> dict | None:
        with self._connect() as db:
            r = db.execute("SELECT * FROM runs WHERE id = ?", (sim_id,)).fetchone()
        return dict(r) if r else None

    def delete(self, sim_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM runs WHERE id = ?", (sim_id,))

    def older_than(self, cutoff: float) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT id, objects, bytes_total FROM runs WHERE finished_at < ? "
                              "ORDER BY finished_at", (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def oldest_first(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT id, objects, bytes_total FROM runs "
                              "ORDER BY finished_at").fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._connect() as db:
            n, b = db.execute("SELECT COUNT(*), COALESCE(SUM(bytes_total), 0) FROM runs").fetchone()
        return {"runs": int(n), "bytes": int(b)}

    def ids(self) -> set[str]:
        with self._connect() as db:
            return {r[0] for r in db.execute("SELECT id FROM runs")}


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


@dataclass
class ArchiveConfig:
    enabled: bool = True
    root: str = ""
    db_path: str = ""
    backend: str = "local"
    durable: bool = True  # False: a temporary directory (lost on deploy)
    s3_bucket: str | None = None
    s3_endpoint: str | None = None
    s3_region: str = "auto"
    s3_prefix: str = "runs/"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    max_age_days: float = 365.0
    max_bytes: int = 0
    retention_hours: float = 24.0
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, env: dict | None = None) -> "ArchiveConfig":
        e = os.environ if env is None else env
        cfg = cls(enabled=not _off(e.get("FIRESIM_ARCHIVE")))
        root = e.get("FIRESIM_ARCHIVE_DIR")
        if not root:
            if os.path.isdir("/data") and os.access("/data", os.W_OK):
                root = "/data/firesim-archive"
            else:
                root = os.path.join(tempfile.gettempdir(), "firesim-archive")
                cfg.durable = False
        cfg.root = root
        cfg.db_path = e.get("FIRESIM_ARCHIVE_DB") or os.path.join(root, "archive.db")
        cfg.backend = (e.get("FIRESIM_ARCHIVE_BACKEND") or "local").strip().lower()
        cfg.s3_bucket = e.get("FIRESIM_ARCHIVE_S3_BUCKET") or None
        cfg.s3_endpoint = e.get("FIRESIM_ARCHIVE_S3_ENDPOINT") or None
        cfg.s3_region = e.get("FIRESIM_ARCHIVE_S3_REGION") or "auto"
        cfg.s3_prefix = e.get("FIRESIM_ARCHIVE_S3_PREFIX", "runs/")
        cfg.s3_access_key_id = e.get("FIRESIM_ARCHIVE_S3_ACCESS_KEY_ID") or None
        cfg.s3_secret_access_key = e.get("FIRESIM_ARCHIVE_S3_SECRET_ACCESS_KEY") or None
        cfg.max_age_days = float(e.get("FIRESIM_ARCHIVE_MAX_AGE_DAYS") or 365)
        cfg.max_bytes = int(float(e.get("FIRESIM_ARCHIVE_MAX_BYTES") or 0))
        cfg.retention_hours = float(e.get("FIRESIM_ARCHIVE_RETENTION_HOURS") or 24)
        return cfg


def _gz_json(obj: Any) -> bytes:
    return gzip.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), mtime=0)


def _un_gz_json(data: bytes) -> Any:
    return json.loads(gzip.decompress(data).decode("utf-8"))


class RunArchive:
    """Index plus object store. ``submit`` archives on a worker thread; reads are synchronous
    (call them from a thread pool in async code)."""

    def __init__(self, index: RunIndex | None, backend: Any | None, config: ArchiveConfig) -> None:
        self.index = index
        self.backend = backend
        self.config = config
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run-archive")
        self._pending: set[Future] = set()
        self._plock = threading.Lock()
        self.failures = 0

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.index is not None and self.backend is not None

    def status(self) -> dict:
        out = {"enabled": self.enabled, "backend": getattr(self.backend, "name", None),
               "durable": self.config.durable if self.enabled else False,
               "max_age_days": self.config.max_age_days, "failures": self.failures}
        if self.enabled:
            try:
                out.update(self.index.stats())
            except Exception:  # noqa: BLE001 - status only
                pass
        return out

    # ── write ──────────────────────────────────────────────────────────────

    def submit(self, sim_id: str, build: Callable[[], dict]) -> Future | None:
        """Archive a finished run in the background. ``build()`` (called on the worker) returns
        ``{"meta": {...}, "response": {...}, "arrival": {...} | None, "ensemble": {...} | None}``.
        Never raises; a failure is logged."""
        if not self.enabled or not valid_id(sim_id):
            return None

        def job() -> None:
            try:
                self.write(sim_id, **build())
            except Exception:  # noqa: BLE001 - never break a run over the archive
                self.failures += 1
                logger.exception("Run archive: could not archive %s", sim_id)

        try:
            fut = self._pool.submit(job)
        except RuntimeError:  # pool shut down (interpreter exit)
            return None
        with self._plock:
            self._pending.add(fut)
        fut.add_done_callback(self._done)
        return fut

    def _done(self, fut: Future) -> None:
        with self._plock:
            self._pending.discard(fut)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Wait for archive jobs submitted so far (tests, shutdown)."""
        end = time.monotonic() + timeout
        while True:
            with self._plock:
                pending = list(self._pending)
            if not pending:
                return True
            for f in pending:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                try:
                    f.result(timeout=left)
                except Exception:  # noqa: BLE001
                    pass

    def write(self, sim_id: str, meta: dict, response: dict, arrival: dict | None = None,
              ensemble: dict | None = None) -> dict:
        """Archive one run synchronously; returns the index row."""
        if not self.enabled:
            raise RuntimeError("run archive is disabled")
        if not valid_id(sim_id):
            raise ValueError(f"bad run id {sim_id!r}")
        objects: dict[str, int] = {}
        for name, body in ((RESPONSE, response), (ARRIVAL, arrival), (ENSEMBLE, ensemble)):
            if body is None:
                continue
            data = _gz_json(body)
            self.backend.put(f"{sim_id}/{name}", data)
            objects[name] = len(data)
        now = time.time()
        row = {c: meta.get(c) for c in _COLUMNS}
        row.update(id=sim_id, archived_at=now, finished_at=meta.get("finished_at") or now)
        meta_doc = {k: v for k, v in row.items() if k != "objects"}
        meta_doc["objects"] = objects
        meta_bytes = json.dumps(meta_doc, separators=(",", ":")).encode("utf-8")
        self.backend.put(f"{sim_id}/{META}", meta_bytes)
        objects[META] = len(meta_bytes)
        row["objects"] = json.dumps(sorted(objects))
        row["bytes_total"] = sum(objects.values())
        self.index.upsert(row)
        logger.info("Run archive: %s archived (%s, %d bytes)", sim_id, row.get("status"),
                    row["bytes_total"])
        return row

    # ── read ───────────────────────────────────────────────────────────────

    def _load(self, sim_id: str, name: str) -> Any | None:
        if not self.enabled or not valid_id(sim_id):
            return None
        data = self.backend.get(f"{sim_id}/{name}")
        return _un_gz_json(data) if data is not None else None

    def load_response(self, sim_id: str) -> dict | None:
        """The GET body stored when the run finished, or None. Read from the object store even
        when the index has no row (an index lost with an ephemeral disk)."""
        return self._load(sim_id, RESPONSE)

    def has(self, sim_id: str) -> bool:
        """Whether the run is in the archive (index row, else its stored response)."""
        if not self.enabled or not valid_id(sim_id):
            return False
        if self.index.get(sim_id) is not None:
            return True
        return self.backend.get(f"{sim_id}/{META}") is not None

    def load_arrival(self, sim_id: str) -> dict | None:
        return self._load(sim_id, ARRIVAL)

    def load_ensemble(self, sim_id: str) -> dict | None:
        return self._load(sim_id, ENSEMBLE)

    # ── retention ──────────────────────────────────────────────────────────

    def _remove(self, sim_id: str, objects: list[str] | None = None) -> None:
        for name in objects or PAYLOADS:
            try:
                self.backend.delete(f"{sim_id}/{name}")
            except Exception:  # noqa: BLE001 - keep going; retried next pass
                logger.warning("Run archive: could not delete %s/%s", sim_id, name, exc_info=True)
        self.index.delete(sim_id)

    def run_retention(self, now: float | None = None, max_age_days: float | None = None,
                      max_bytes: int | None = None) -> dict:
        """Delete runs older than the maximum age, then the oldest runs while the archive is
        over the byte cap; also payloads with no index row older than the maximum age (left by
        a lost index). Returns counts."""
        if not self.enabled:
            return {"deleted_age": 0, "deleted_bytes": 0, "deleted_orphans": 0}
        now = time.time() if now is None else now
        age = self.config.max_age_days if max_age_days is None else max_age_days
        cap = self.config.max_bytes if max_bytes is None else max_bytes
        cutoff = now - age * 86400.0
        n_age = n_bytes = n_orphans = 0
        for r in self.index.older_than(cutoff):
            self._remove(r["id"], json.loads(r["objects"] or "[]"))
            n_age += 1
        if cap and cap > 0:
            total = self.index.stats()["bytes"]
            for r in self.index.oldest_first():
                if total <= cap:
                    break
                self._remove(r["id"], json.loads(r["objects"] or "[]"))
                total -= int(r["bytes_total"] or 0)
                n_bytes += 1
        try:
            known = self.index.ids()
            for o in list(self.backend.list()):
                run_id = o.key.split("/", 1)[0]
                if run_id not in known and o.mtime < cutoff:
                    self.backend.delete(o.key)
                    n_orphans += 1
        except Exception:  # noqa: BLE001 - listing is best effort
            logger.warning("Run archive: orphan sweep failed", exc_info=True)
        out = {"deleted_age": n_age, "deleted_bytes": n_bytes, "deleted_orphans": n_orphans}
        if n_age or n_bytes or n_orphans:
            logger.info("Run archive retention: %s", out)
        return out

    def shutdown(self) -> None:
        self.wait_idle(10.0)


def build_archive(cfg: ArchiveConfig | None = None, s3_client: Any = None) -> RunArchive:
    """An archive from the configuration; disabled (with a warning) when it cannot be set up."""
    cfg = cfg or ArchiveConfig.from_env()
    if not cfg.enabled:
        logger.info("Run archive: off (FIRESIM_ARCHIVE=0); finished runs are kept in memory only")
        return RunArchive(None, None, cfg)
    backend: Any = None
    if cfg.backend == "s3":
        missing = [n for n, v in (("FIRESIM_ARCHIVE_S3_BUCKET", cfg.s3_bucket),
                                  ("FIRESIM_ARCHIVE_S3_ACCESS_KEY_ID", cfg.s3_access_key_id),
                                  ("FIRESIM_ARCHIVE_S3_SECRET_ACCESS_KEY", cfg.s3_secret_access_key))
                   if not v]
        if s3_client is not None:
            missing = [m for m in missing if m == "FIRESIM_ARCHIVE_S3_BUCKET"]
        if missing:
            cfg.warnings.append("s3 backend without " + ", ".join(missing) + ": using local")
        else:
            try:
                backend = S3Backend(cfg.s3_bucket, cfg.s3_prefix, client=s3_client,
                                    endpoint_url=cfg.s3_endpoint, region_name=cfg.s3_region,
                                    aws_access_key_id=cfg.s3_access_key_id,
                                    aws_secret_access_key=cfg.s3_secret_access_key)
            except Exception as exc:  # noqa: BLE001 - e.g. boto3 not installed
                cfg.warnings.append(f"s3 backend unavailable ({exc}): using local")
        if backend is None:
            cfg.backend = "local"
    elif cfg.backend != "local":
        cfg.warnings.append(f"unknown FIRESIM_ARCHIVE_BACKEND {cfg.backend!r}: using local")
        cfg.backend = "local"
    try:
        if backend is None:
            backend = LocalBackend(cfg.root)
        index = RunIndex(cfg.db_path)
    except Exception as exc:  # noqa: BLE001 - never stop the API over the archive
        logger.warning("Run archive: disabled, could not open %s (%s)", cfg.root, exc)
        cfg.enabled = False
        return RunArchive(None, None, cfg)
    if not cfg.durable:
        cfg.warnings.append(
            f"no FIRESIM_ARCHIVE_DIR and no /data volume: index at {cfg.db_path} is temporary "
            "(lost on deploy)" + ("" if backend.name == "s3" else "; so are the run payloads"))
    for w in cfg.warnings:
        logger.warning("Run archive: %s", w)
    logger.info("Run archive: on (%s backend, index %s)", backend.name, cfg.db_path)
    return RunArchive(index, backend, cfg)


_archive: RunArchive | None = None
_archive_lock = threading.Lock()


def get_archive() -> RunArchive:
    """The process's archive, built from the environment on first use."""
    global _archive
    with _archive_lock:
        if _archive is None:
            _archive = build_archive()
        return _archive


def set_archive(archive: RunArchive | None) -> None:
    """Replace the process's archive (tests); None rebuilds it from the environment on next use."""
    global _archive
    with _archive_lock:
        _archive = archive


def is_terminal(status: str) -> bool:
    return status in _TERMINAL
