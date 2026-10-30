"""ML ember-cast Phase 2 data infrastructure (PREREG_ML_EMBER_DEV sections 2-4, 8, 14; addendum A1).

Purpose
    Build the frozen training-split feature tables for the ML ember-cast development ladder from the v8
    ember pool, through a loader that refuses every sealed path. Nothing here fits a model.
Modules
    paths        roots, data folder, sealed-path list, seeds, frozen sha256s
    loader       the single guarded file-open for pool data (section 14, amendment 16)
    features     deterministic per-fire feature builder on the frozen v8 primitives (section 4)
    build_table  CLI: labels, negative subsample, Parquet tables, descriptors (sections 3, 4, 8)
Imports
    The frozen v8 code (``scripts/validation/v8/{common,intervals,inputs,fit}.py``,
    ``scripts/validation/v8_score.py``) is imported, never copied or modified (addendum A1 "code
    dependency"). ``firesim`` comes from ``engine/src``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "engine" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
