"""Shared paths, crosswalks and helpers for the US transfer diagnosis ([R71]).

Purpose: one place for the data root, the LANDFIRE FBFM40 -> FBP crosswalk variants tested as cause (a),
and small helpers (sha256, HTTP with retry). No model code lives here.

Crosswalks (cause (a)). FBFM40 groups (Scott & Burgan 2005): GR 101-109 grass, GS 121-124 grass-shrub,
SH 141-149 shrub, TU 161-165 timber-understory, TL 181-189 timber litter, SB 201-204 slash-blowdown,
NB 91-99 non-burnable. ``BASE`` is the embers v7/v8 and R67 crosswalk (grass and GS -> O-1b, shrub -> C-4,
timber -> C-7, urban NB1 -> O-1b, water NB8 -> water, other -> non-fuel). Each variant changes one group's
FBP type only. Codes are the CIFFC national scheme ``cfs_national`` (firesim.data.fuel_loader).
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.request
from pathlib import Path

import numpy as np

W = Path.home() / "dev/wildfire"
DATA = Path(os.environ.get("FIRESIM_R71_DATA", W / "validation-data/us-transfer-r71"))
V8 = W / "validation-data/v8-pool"
SPLIT = W / "reports/data/v8-prefreeze-checks-2026-10-18/results/split.csv"
UA = {"User-Agent": "FireSim-R71-us-transfer (open data research)"}

# cfs_national codes
C1, C3, C4, C5, C7, D1, M1_50, O1A, O1B = 1, 3, 4, 5, 7, 13, 650, 31, 32
WATER, NONFUEL = 102, 101

GR = list(range(101, 110))
GS = list(range(121, 125))
SH = list(range(141, 150))
TU = list(range(161, 166))
TL = list(range(181, 190))
SB = list(range(201, 205))
GROUPS = {"GR": GR, "GS": GS, "SH": SH, "TU": TU, "TL": TL, "SB": SB, "NB1": [91], "NB8": [98]}
BASE = {"GR": O1B, "GS": O1B, "SH": C4, "TU": C7, "TL": C7, "SB": C7, "NB1": O1B, "NB8": WATER}

# One-group-at-a-time alternatives (cause (a)); names are the run variant names.
CROSSWALKS = {
    "base": {},
    "sh_o1b": {"SH": O1B},       # shrub as standing grass (curing applies)
    "sh_m1": {"SH": M1_50},      # shrub as boreal mixedwood 50 % conifer (M-2 in summer)
    "sh_d1": {"SH": D1},         # shrub as leafless aspen (D-2 green in summer)
    "sh_c1": {"SH": C1},         # shrub as spruce-lichen woodland (open, low-load conifer)
    "gs_c4": {"GS": C4},         # grass-shrub as shrub (C-4) instead of grass
    "gr_o1a": {"GR": O1A, "GS": O1A},  # grass as matted grass
    "t_c5": {"TU": C5, "TL": C5, "SB": C5},  # timber as red/white pine (slow surface spread)
    "tl_d1": {"TL": D1},         # timber litter as deciduous litter (D-1/D-2)
    "nb1_nf": {"NB1": NONFUEL},  # urban as non-fuel
}


def crosswalk(fbfm: np.ndarray, variant: str = "base") -> np.ndarray:
    """FBP cfs_national codes from FBFM40 codes for a crosswalk variant (non-FBFM cells -> non-fuel)."""
    m = dict(BASE, **CROSSWALKS[variant])
    out = np.full(fbfm.shape, NONFUEL, np.int32)
    for g, codes in GROUPS.items():
        out[np.isin(fbfm, codes)] = m[g]
    return out


def fbfm_group(fbfm: np.ndarray) -> np.ndarray:
    """Group label per cell ('GR', 'GS', 'SH', 'TU', 'TL', 'SB', 'NB1', 'NB8', 'NB')."""
    out = np.full(fbfm.shape, "NB", dtype="<U3")
    for g, codes in GROUPS.items():
        out[np.isin(fbfm, codes)] = g
    return out


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def http(url: str, data: bytes | None = None, tries: int = 8, timeout: int = 300) -> bytes:
    for k in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA),
                                          timeout=timeout).read()
        except Exception as e:  # network: retry with back-off
            if k == tries - 1:
                raise
            time.sleep((60 if getattr(e, "code", None) == 429 else 5) * (k + 1))
    return b""
