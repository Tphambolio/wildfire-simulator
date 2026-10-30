"""Guarded loader for the v8 ember pool: the single file-open through which every pool read passes.

Implements PREREG_ML_EMBER_DEV section 2 ("sealed and forbidden in development"), section 14 (loader guard,
amendment 16) and addendum A1 (extra sealed paths). The guard REFUSES, with ``SealedPathError`` and a line in
the data folder's ``refusals.log``:

- ``<pool>/viirs_decision/``, ``<pool>/final/``, ``<pool>/elmfire_runs/`` (whole subtrees);
- ``<pool>/draw/sealed*`` and ``<pool>/draw/sealed_manifest.csv``;
- ``<validation-data>/sealed2026/`` (every file except that folder's own ``README.md`` and ``_about.json``);
- ``<reports>/data/long-range-embers-v8-2026-10-29/results/{jumps.csv, ranks_per_jump.csv, sealed_manifest.csv,
  score_out.json, secondary.json, sensitivities.json, unseal_exclusions.json}``;
- ``<pool>/fires/<key>/intervals/`` for every key whose ``assignment`` in the frozen ``split.csv`` is not exactly
  ``training`` (decision, ``training_legacy`` and unknown keys alike).

Allowed count-only files ``draw/{counts.csv, state.json, draw_log.txt}`` pass. The only override is a
confirmation commit (``--confirmation-commit <sha>``) that exists on ``origin`` (``git ls-remote`` + ancestry);
an override that passes is still logged. Tests inject ``remote_check``.

Inputs: paths under the pool, the validation-data root and the reports root. Outputs: file handles, arrays,
frames; ``refusals.log``. Nothing is written to the pool.
"""
from __future__ import annotations

import fnmatch
import io
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Mapping

import numpy as np

from . import ROOT, paths as P

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class SealedPathError(PermissionError):
    """A read of a sealed or forbidden path was refused (PREREG_ML_EMBER_DEV section 2, A1)."""


def remote_has_commit(sha: str, repo: Path = ROOT, remote: str = "origin") -> bool:
    """True when ``sha`` is a commit reachable from a ref on ``remote`` (``git ls-remote`` lists the tips;
    ancestry is checked after a fetch). Mocked in tests."""
    if not SHA_RE.match(sha):
        return False
    try:
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", remote], check=False, timeout=120,
                       capture_output=True)
        out = subprocess.run(["git", "-C", str(repo), "ls-remote", "--heads", "--tags", remote], check=True,
                             timeout=120, capture_output=True, text=True).stdout
    except (subprocess.SubprocessError, OSError):
        return False
    tips = [ln.split()[0] for ln in out.splitlines() if ln.strip()]
    for tip in tips:
        r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, tip], capture_output=True)
        if r.returncode == 0:
            return True
    return False


def _rel(path: Path, root: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


class Guard:
    """Path guard + guarded readers. ``assignment`` maps fire_key -> split assignment (frozen split.csv)."""

    def __init__(self, pool: Path = P.POOL, validation_data: Path | None = None, v8_result_folder: Path = P.V8_RESULT_FOLDER,
                 assignment: Mapping[str, str] | None = None, refusals_log: Path = P.REFUSALS_LOG,
                 confirmation_commit: str | None = None, remote_check: Callable[[str], bool] = remote_has_commit):
        self.pool = Path(pool).resolve()
        self.validation_data = Path(validation_data).resolve() if validation_data else self.pool.parent
        self.v8_result_folder = Path(v8_result_folder).resolve()
        self._assignment = assignment
        self.refusals_log = Path(refusals_log)
        self.confirmation_commit = confirmation_commit
        self._remote_check = remote_check
        self._override_ok: bool | None = None
        self.opened: list[str] = []

    # ── split ────────────────────────────────────────────────────────────────────────────────
    @property
    def assignment(self) -> Mapping[str, str]:
        if self._assignment is None:
            from scripts.validation.v8 import common as C
            s = C.split_table()  # sha256-checked against the frozen split.csv
            self._assignment = dict(zip(s.fire_key, s.assignment))
        return self._assignment

    def training_keys(self) -> list[str]:
        return sorted(k for k, a in self.assignment.items() if a == "training")

    def legacy_keys(self) -> list[str]:
        return sorted(k for k, a in self.assignment.items() if a == "training_legacy")

    def assert_training(self, fire_key: str) -> None:
        if self.assignment.get(fire_key) != "training":
            self._refuse(Path(fire_key), f"fire {fire_key!r} is not in the training split ({self.assignment.get(fire_key)})")

    # ── the guard ────────────────────────────────────────────────────────────────────────────
    def sealed_reason(self, path: Path) -> str | None:
        """None when the path may be read; otherwise why it is sealed."""
        p = Path(path).resolve()
        rel = _rel(p, self.pool)
        if rel is not None:
            for sub in P.SEALED_POOL_SUBTREES:
                if rel == sub.rstrip("/") or rel.startswith(sub):
                    return f"sealed subtree v8-pool/{sub}"
            for g in P.SEALED_POOL_GLOBS:
                if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g + "/*") or fnmatch.fnmatch(rel, g + "/**"):
                    return f"sealed glob v8-pool/{g}"
            if rel in P.SEALED_POOL_FILES:
                return f"sealed file v8-pool/{rel}"
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] == "fires" and parts[2] == "intervals":
                by_dir = {k.replace(":", "_"): a for k, a in self.assignment.items()}
                a = by_dir.get(parts[1])
                if a != "training":
                    return f"intervals of a non-training fire {parts[1]} (assignment {a})"
            return None
        rel = _rel(p, self.validation_data)
        if rel is not None:
            for sub in P.SEALED_VALIDATION_SUBTREES:
                if (rel == sub.rstrip("/") or rel.startswith(sub)) and rel not in P.SEALED_VALIDATION_EXCEPTIONS:
                    return f"sealed subtree validation-data/{sub}"
            return None
        rel = _rel(p, self.v8_result_folder)
        if rel is not None and rel in P.SEALED_V8_RESULT_FILES:
            return f"sealed v8 per-jump result file {rel}"
        rel = _rel(p, ROOT)
        if rel is not None:
            for sub in P.SEALED_REPO_SUBTREES:
                if rel == sub.rstrip("/") or rel.startswith(sub):
                    return f"sealed repository subtree {sub} (derived from decision fires)"
        return None

    def _override(self) -> bool:
        if self.confirmation_commit is None:
            return False
        if self._override_ok is None:
            self._override_ok = bool(self._remote_check(self.confirmation_commit))
        return self._override_ok

    def _log(self, path: Path, reason: str, action: str) -> None:
        try:
            self.refusals_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.refusals_log, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\t{action}\t{path}\t{reason}\n")
        except OSError:
            pass

    def _refuse(self, path: Path, reason: str) -> None:
        self._log(path, reason, "REFUSED")
        raise SealedPathError(f"refused {path}: {reason}")

    def check(self, path) -> Path:
        """Return the path if it may be read; raise SealedPathError (and log) otherwise."""
        p = Path(path)
        reason = self.sealed_reason(p)
        if reason is None:
            return p
        if self._override():
            self._log(p, reason, f"OVERRIDE confirmation_commit={self.confirmation_commit}")
            return p
        if self.confirmation_commit is not None:
            reason += f"; confirmation commit {self.confirmation_commit} not found on origin"
        self._refuse(p, reason)
        return p  # unreachable

    # ── guarded readers (the only file opens of pool data) ───────────────────────────────────
    def open(self, path, mode: str = "rb"):
        if any(c in mode for c in "wax+"):
            raise ValueError("the loader never writes to the pool")
        p = self.check(path)
        self.opened.append(str(p))
        return open(p, mode)

    def read_bytes(self, path) -> bytes:
        with self.open(path, "rb") as f:
            return f.read()

    def read_text(self, path) -> str:
        with self.open(path, "r") as f:
            return f.read()

    def read_json(self, path):
        return json.loads(self.read_text(path))

    def load_npz(self, path) -> dict[str, np.ndarray]:
        with self.open(path, "rb") as f:
            z = np.load(io.BytesIO(f.read()), allow_pickle=False)
            return {k: z[k] for k in z.files}

    def read_csv(self, path, **kw):
        import pandas as pd
        with self.open(path, "rb") as f:
            return pd.read_csv(f, **kw)

    def read_pickle(self, path):
        import pandas as pd
        p = self.check(path)
        self.opened.append(str(p))
        return pd.read_pickle(p)  # pandas handles the gzip layer

    def exists(self, path) -> bool:
        """Existence test of a path that may be read (a sealed path is refused even for ``exists``)."""
        return self.check(path).exists()

    def listdir(self, path) -> list[Path]:
        p = self.check(path)
        return sorted(q for q in p.iterdir() if self.sealed_reason(q) is None)


_DEFAULT: Guard | None = None


def default_guard(confirmation_commit: str | None = None) -> Guard:
    global _DEFAULT
    if _DEFAULT is None or confirmation_commit is not None:
        _DEFAULT = Guard(confirmation_commit=confirmation_commit)
    return _DEFAULT
