"""Paths, sealed list, seeds and frozen hashes for the ML ember-cast data infrastructure.

Implements PREREG_ML_EMBER_DEV section 2 (data table; sealed and forbidden list), section 3 (seed S_NEG and
the negative-subsampling constants), section 14 (data folder) and addendum A1 (extra sealed paths).
Roots can be overridden by environment variable so that the loader is unit-testable on a synthetic tree:

    FIRESIM_V8_DATA        pool root (default ~/dev/wildfire/validation-data/v8-pool; same as v8 common.py)
    FIRESIM_V8_PREFREEZE   pre-freeze tables (default ~/dev/wildfire/reports/data/v8-prefreeze-checks-2026-10-18)
    FIRESIM_REPORTS        reports root (default ~/dev/wildfire/reports)
    ML_EMBER_DATA_FOLDER   output data folder (default <reports>/data/ml-ember-cast-2026-10-29)

Inputs: none (constants). Outputs: none.
"""
from __future__ import annotations

import os
from pathlib import Path

from scripts.validation.v8 import common as C

POOL: Path = C.DATA
VALIDATION_DATA: Path = POOL.parent
PREFREEZE: Path = C.PREFREEZE
REPORTS: Path = Path(os.environ.get("FIRESIM_REPORTS", Path.home() / "dev/wildfire/reports"))
DATA_FOLDER: Path = Path(os.environ.get("ML_EMBER_DATA_FOLDER", REPORTS / "data" / "ml-ember-cast-2026-10-29"))
TABLE_DIR: Path = DATA_FOLDER / "table"
REFUSALS_LOG: Path = DATA_FOLDER / "refusals.log"
V8_RESULT_FOLDER: Path = REPORTS / "data" / "long-range-embers-v8-2026-10-29"

# PREREG_ML_EMBER_DEV section 2 and A1: sealed and forbidden in development. Patterns are relative to the
# named root; a trailing "/" means the whole subtree; "sealed*" is a glob on the basename.
SEALED_POOL_SUBTREES = ("viirs_decision/", "final/", "elmfire_runs/")
SEALED_POOL_GLOBS = ("draw/sealed*",)
SEALED_POOL_FILES = ("draw/sealed_manifest.csv",)
SEALED_VALIDATION_SUBTREES = ("sealed2026/",)
SEALED_VALIDATION_EXCEPTIONS = ("sealed2026/README.md", "sealed2026/_about.json")
SEALED_V8_RESULT_FILES = ("results/jumps.csv", "results/ranks_per_jump.csv", "results/sealed_manifest.csv",
                          "results/score_out.json", "results/secondary.json", "results/sensitivities.json",
                          "results/unseal_exclusions.json")
ALLOWED_COUNT_FILES = ("draw/counts.csv", "draw/state.json", "draw/draw_log.txt")

# section 3: negative subsampling
S_NEG = 2026102501
N_NEG_POSITIVE_INTERVAL = 200
P_EMPTY_INTERVAL = 0.25
N_NEG_EMPTY_INTERVAL = 50

# frozen hashes (section 2, section 16, A1)
SPLIT_SHA256 = C.SPLIT_SHA256
JUMPS_TRAINING_SHA256 = "5da778ae194c09d3d6d05f03cd5c15b3e8334786c159f846453bfbc7b23ff54e"
FIT_BETA_SHA256 = "fda5706c400cd74b07b7243c110031d09aa9160445cdcd2996a63ac8d18ec425"
FIRES_TRAINING_SHA256 = "5749f1f36eab6817e53f954e443ac5bea08e1ca2412d047823b1fe8bb42620a3"
