#!/usr/bin/env python3
"""Build the dated FireSim validation scorecard from existing scored results.

Writes ``docs/scorecard/<date>.json`` (schema version 1) and regenerates
``docs/scorecard/README.md`` (one row per dated scorecard). It **never runs a validation**: every
number is read from the result files the numbered reports left behind (``~/dev/wildfire/reports/data``
and ``~/dev/wildfire/validation-data``) or, where no file holds it, copied from the report text and
marked ``"kind": "report text"``. Each metric names its report number, the file and the JSON path
it came from, and the file's SHA-256, so every published number is traceable.

The one computed quantity is ``ci95_derived`` on the held-out F1 level: [R45] reports the paired
difference's CI, not the level's, so the script bootstraps the level from the per-fire-day CSV
(whole-fire resampling, 2,000 draws, seed 1, the same scheme as [R45]) and labels it as derived.

Usage (from the repository root, no third-party packages needed)::

    python scripts/make_scorecard.py --date 2026-10-25 [--commit <sha>] [--report R69]
        [--reports-data ~/dev/wildfire/reports/data] [--validation-data ~/dev/wildfire/validation-data]
        [--out-dir docs/scorecard] [--no-derived]

Release rule (docs/validation-scorecard.md): every tagged release gets a dated scorecard; the API
(``GET /api/v1/scorecard``) and the app show the latest file in ``docs/scorecard/``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()

FRAMING = (
    "FireSim is a preparedness and training tool for EOC Planning sections (exercises, tabletop "
    "scenarios, what-if planning). It is not an operational fire-growth forecast and is not for "
    "real-time operational prediction; the lead agency's prediction is authoritative."
)

# Reference points the metrics are compared against (literature; verified in the record §4.2)
WISE_DEFAULT_F1 = 0.26  # Bennett et al. (2026), W.I.S.E. default settings, national
WISE_TUNED_F1 = 0.54  # Bennett et al. (2026), tuned W.I.S.E.


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────
def tilde(path: Path) -> str:
    """Home-relative path for the public artifact (never a /home/<user> path)."""
    try:
        return "~/" + str(path.resolve().relative_to(HOME))
    except ValueError:
        return str(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dig(obj: Any, path: str) -> Any:
    """``a.b[0].c`` style lookup; raises KeyError / IndexError on a miss."""
    cur = obj
    for part in path.replace("]", "").replace("[", ".").split("."):
        if part == "":
            continue
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


def pct(x: float, nd: int = 0) -> str:
    return f"{x * 100:.{nd}f} %"


class Source:
    """A result file: loaded once, hashed once; ``ref`` records the JSON path used."""

    def __init__(self, report: str, path: Path, label: str):
        self.report = report
        self.path = path
        self.label = label
        self.ok = path.is_file()
        self.data: Any = json.load(open(path)) if self.ok else None
        self.digest = sha256(path) if self.ok else None

    def ref(self, json_path: str | None = None) -> dict[str, Any]:
        if not self.ok:
            return {"report": self.report, "kind": "report text", "file": tilde(self.path),
                    "note": f"{self.label}: file not found when the scorecard was built; value copied from the report text"}
        d: dict[str, Any] = {"report": self.report, "kind": "file", "file": tilde(self.path), "sha256": self.digest}
        if json_path:
            d["path"] = json_path
        return d

    def get(self, json_path: str, fallback: Any) -> Any:
        if not self.ok:
            return fallback
        return dig(self.data, json_path)


def report_text(report: str, where: str) -> dict[str, Any]:
    return {"report": report, "kind": "report text", "file": where}


def doc_source(report: str, doc: str, anchor: str, tests: list[str]) -> dict[str, Any]:
    return {"report": report, "kind": "doc", "file": doc, "section": anchor, "tests": tests}


def git_commit(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    except Exception:  # noqa: BLE001 - no git, say so
        return "unknown"


def engine_version() -> str:
    init = REPO / "engine" / "src" / "firesim" / "__init__.py"
    for line in init.read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "unknown"


def bootstrap_level_ci(csv_path: Path, config: str, split: str, column: str,
                       draws: int = 2000, seed: int = 1) -> dict[str, Any] | None:
    """Whole-fire bootstrap of a fire-day mean (the [R45] scheme), stdlib only."""
    if not csv_path.is_file():
        return None
    by_fire: dict[str, list[float]] = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if row["config"] != config or row["set"] != split:
                continue
            by_fire.setdefault(row["fire_id"], []).append(float(row[column]))
    fires = sorted(by_fire)
    if not fires:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = [v for fid in rng.choices(fires, k=len(fires)) for v in by_fire[fid]]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int(0.025 * draws)]
    hi = means[min(draws - 1, int(0.975 * draws))]
    return {
        "lo": r(lo), "hi": r(hi), "n_fires": len(fires), "n_fire_days": sum(len(v) for v in by_fire.values()),
        "method": f"whole-fire bootstrap of the fire-day mean, {draws:,} draws, seed {seed}, percentile 95 % CI; "
                  f"computed by scripts/make_scorecard.py from {tilde(csv_path)} (column {column}, {config} {split}). "
                  "Not a number reported in [R45], which gives the paired difference CI only.",
        "file": tilde(csv_path), "sha256": sha256(csv_path),
    }


# ── metrics ──────────────────────────────────────────────────────────────────────────────────────
def build_metrics(reports_data: Path, validation_data: Path, derived: bool) -> list[dict[str, Any]]:
    r45 = Source("[R45]", reports_data / "validation-rescore-backcast-2026-10-15" / "results_rescore_backcast.json",
                 "Validation rescored on pre-fire fuel grid 2026-10-15")
    r45_csv = reports_data / "validation-rescore-backcast-2026-10-15" / "per_fireday.csv"
    r49 = Source("[R49]", reports_data / "validation-rescore-backcast-e1-2026-10-17" / "results_e1.json",
                 "Validation ensemble rescored on pre-fire fuel grid 2026-10-17")
    r65 = Source("[R65]", reports_data / "b2-ensemble-calibration-2026-10-24" / "results.json",
                 "Ensemble re-calibration with a bias term B2 2026-10-24")
    r10 = Source("[R10]", validation_data / "jasper2024" / "results-2026-10-10" / "results.json",
                 "Jasper 2024 structure validation 2026-10-10")
    v4 = Source("[R39] (v4)", reports_data / "long-range-embers-v4-2026-10-12" / "prereg_lr4_pooled.json",
                "Long-range embers v4 2026-10-12")
    v5 = Source("[R-v5] (unnumbered, PR #81)", reports_data / "long-range-embers-v5-two-mode-kernel-2026-10-12" / "results_lr5_pooled.json",
                "Long-range embers v5 two-mode kernel 2026-10-12")
    v6 = Source("[R47]", reports_data / "long-range-embers-v6-reframed-validation-2026-10-13" / "scores_PREREG_LR6.json",
                "Long-range embers v6 reframed validation 2026-10-13")
    v7 = Source("[R48]", reports_data / "long-range-embers-v7-calibrated-front-2026-10-15" / "scores_PREREG_LR7_fresh.json",
                "Long-range embers v7 calibrated front 2026-10-15")

    metrics: list[dict[str, Any]] = []

    # 1. FBP equations vs cffdrs (verification, not validation)
    verification = REPO / "docs" / "verification.md"
    ver_text = verification.read_text() if verification.is_file() else ""
    fbp_ok = "3e-10" in ver_text and "12,960" in ver_text
    src = doc_source("verification", "docs/verification.md", "§1 and §1b",
                     ["engine/tests/fbp/test_cffdrs_reference.py", "engine/tests/fwi/test_cffdrs_fwi_reference.py"])
    if not fbp_ok:
        src["note"] = "docs/verification.md did not contain the quoted figures when built"
    metrics.append({
        "id": "fbp_cffdrs_conformance",
        "group": "equations",
        "label": "FBP and FWI equations vs CFS cffdrs",
        "status": "validated",
        "kind": "verification",
        "value": 3e-10,
        "display": "relative difference ≤ 3e-10 (FBP, 12,960 cases); FFMC/ISI/DC ≤ 1e-13",
        "unit": "relative difference",
        "ci95": None,
        "n": 12960,
        "delta": None,
        "comparators": [],
        "reference": {"label": "CFS cffdrs reference implementation (Python port), ST-X-3 FFMC coefficient 147.2",
                      "value": 0.0, "note": "float-precision match is the pass criterion"},
        "gloss": "The fire-behaviour equations match the Canadian reference implementation to floating-point "
                 "precision. This shows the equations are right, not that predictions match real fires.",
        "source": src,
    })

    # 2. Held-out one-day F1, operational set-up, back-cast grid
    f1 = r45.get("levels.C1.held_out.backcast.f1_17h", 0.246)
    f1_2014b = r45.get("levels.C1.held_out.nat2014.f1_17h", 0.212)
    n79 = r45.get("levels.C1.held_out.backcast.n", 79)
    d = r45.get("paired.C1.held_out.f1_17h", {"mean": 0.034, "lo": 0.007, "hi": 0.058, "verdict": "better"})
    wise9 = r45.get("wise.means", {"wise_f1_17h_backcast": 0.232, "firesim_f1_17h_backcast": 0.252})
    m: dict[str, Any] = {
        "id": "heldout_f1_17h",
        "group": "spread",
        "label": "Held-out one-day F1 (operational set-up: active edges 2 d, FFMC spin-up, 10-20 h)",
        "status": "partly_validated",
        "kind": "validation",
        "value": r(f1),
        "display": f"{f1:.3f}",
        "unit": "F1 (0-1)",
        "ci95": None,
        "ci95_note": "[R45] reports no CI on the level; its pre-registered test is the paired difference below",
        "n": n79,
        "n_note": f"{n79} held-out fire-days of 16 Alberta fires never used to choose a setting (CFSDS 2014-2024)",
        "delta": {"vs": "CFS 2014b grid (former headline)", "mean": r(d["mean"]), "ci95": [r(d["lo"]), r(d["hi"])],
                  "verdict": d.get("verdict", ""), "source": r45.ref("paired.C1.held_out.f1_17h")},
        "comparators": [
            {"label": "Same set-up on CFS 2014b", "value": r(f1_2014b), "display": f"{f1_2014b:.3f}",
             "source": r45.ref("levels.C1.held_out.nat2014.f1_17h")},
            {"label": "W.I.S.E. default settings, national (Bennett et al. 2026)", "value": WISE_DEFAULT_F1, "display": "0.26",
             "source": report_text("Bennett et al. (2026); record §4.2", "docs/validation.md")},
            {"label": "W.I.S.E. tuned (Bennett et al. 2026)", "value": WISE_TUNED_F1, "display": "0.54",
             "source": report_text("Bennett et al. (2026); record §4.2", "docs/validation.md")},
            {"label": "W.I.S.E. on identical back-cast inputs, 9 record fire-days (descriptive)",
             "value": r(wise9["wise_f1_17h_backcast"]),
             "display": f"W.I.S.E. {wise9['wise_f1_17h_backcast']:.3f} vs FireSim {wise9['firesim_f1_17h_backcast']:.3f}",
             "source": r45.ref("wise.means")},
        ],
        "reference": {"label": "W.I.S.E. default / tuned one-day F1", "value": [WISE_DEFAULT_F1, WISE_TUNED_F1],
                      "note": "level with W.I.S.E. defaults, short of tuned W.I.S.E."},
        "gloss": "Front position skill vs observed fires: level with W.I.S.E. defaults, short of tuned W.I.S.E. "
                 "Applies to historical Alberta replays on the back-cast fuel grid; live runs on the 2025 grid are not scored.",
        "source": r45.ref("levels.C1.held_out.backcast.f1_17h"),
    }
    if derived:
        ci = bootstrap_level_ci(r45_csv, "C1", "held_out", "f1_17h_backcast")
        if ci:
            m["ci95_derived"] = ci
    metrics.append(m)

    # 3. Area bias (normalised area difference) and share of days over-predicted
    ad = r45.get("levels.C1.held_out.backcast.area_diff_17h", 0.26)
    ad_2014b = r45.get("levels.C1.held_out.nat2014.area_diff_17h", 0.25)
    op = r45.get("levels.C1.held_out.backcast.over_predicted_17h", 0.73)
    metrics.append({
        "id": "area_bias_17h",
        "group": "spread",
        "label": "Area bias, held-out (normalised (A_pred − A_obs) / (A_pred + A_obs), 17 h)",
        "status": "partly_validated",
        "kind": "validation",
        "value": r(ad),
        "display": f"+{ad:.2f}",
        "unit": "normalised area difference (−1 to 1; 0 = unbiased)",
        "ci95": None,
        "n": n79,
        "delta": None,
        "comparators": [
            {"label": "Same set-up on CFS 2014b", "value": r(ad_2014b), "display": f"+{ad_2014b:.2f}",
             "source": r45.ref("levels.C1.held_out.nat2014.area_diff_17h")},
            {"label": "Share of held-out days over-predicted", "value": r(op), "display": pct(op),
             "source": r45.ref("levels.C1.held_out.backcast.over_predicted_17h")},
            {"label": "Second round on 2014b (record, 2026-10-07)", "value": 0.27, "display": "+0.27",
             "source": report_text("docs/validation.md second round", "docs/validation.md")},
        ],
        "reference": {"label": "unbiased", "value": 0.0, "note": "positive = modelled growth larger than observed"},
        "gloss": "Growth is still over-predicted on most held-out days; the largest wind-driven runs are under-predicted.",
        "source": r45.ref("levels.C1.held_out.backcast.area_diff_17h"),
    })

    # 4-8. Ensemble (default settings, back-cast grid, 65 held-out fire-days)
    lv = "descriptive.levels.backcast."
    cov = r49.get(lv + "inside_p10_p90", {"value": 0.49, "lo": 0.38, "hi": 0.60})
    cov_d = r49.get("direction_aware_reading.inside_p10_p90", {"mean": 0.015, "lo": -0.071, "hi": 0.105, "reading": "no detectable difference"})
    n65 = r49.get("integrity.expected_fire_days", 65)
    rec = r49.get("reproduction_vs_record.published_record",
                  {"coverage_p10_p90": 0.49, "crps_log10_area": 0.471, "spread_skill_ratio": 1.58, "p10_recall_ge_0.9": 0.29})
    metrics.append({
        "id": "ensemble_coverage_p10_p90",
        "group": "ensemble",
        "label": "Ensemble: held-out fire-days with observed area inside the members' P10-P90",
        "status": "partly_validated",
        "kind": "validation",
        "value": r(cov["value"]),
        "display": pct(cov["value"]),
        "unit": "share of fire-days",
        "ci95": [r(cov["lo"]), r(cov["hi"])],
        "ci95_note": "descriptive whole-fire bootstrap (2,000 draws) in [R49]; the registered verdict is the difference vs 2014b",
        "n": n65,
        "n_note": f"{n65} held-out fire-days of 16 fires, 20 members, default perturbation sizes (A15)",
        "delta": {"vs": "CFS 2014b rerun", "mean": r(cov_d["mean"]), "ci95": [r(cov_d["lo"]), r(cov_d["hi"])],
                  "verdict": cov_d.get("reading", ""), "source": r49.ref("direction_aware_reading.inside_p10_p90")},
        "comparators": [{"label": "Record value on 2014b (2026-10-08)", "value": rec["coverage_p10_p90"], "display": pct(rec["coverage_p10_p90"]),
                         "source": r49.ref("reproduction_vs_record.published_record.coverage_p10_p90")}],
        "reference": {"label": "calibrated ensemble", "value": 0.80, "note": "about 80 % for a P10-P90 range"},
        "gloss": "The modelled range is too narrow: the real one-day area falls inside it on about half the days, "
                 "where a calibrated range would hold about 80 %.",
        "source": r49.ref(lv + "inside_p10_p90"),
    })

    crps = r49.get(lv + "crps_log10_area", {"value": 0.407, "lo": 0.329, "hi": 0.506})
    crps_2014b = r49.get("descriptive.pooled_summary_registered.nat2014.crps_log10_area", 0.465)
    crps_d = r49.get("direction_aware_reading.crps_log10_area", {"mean": -0.057, "lo": -0.113, "hi": -0.007, "reading": "back-cast better"})
    crpss = r49.get(lv + "crpss_vs_det", {"value": 0.238, "lo": 0.190, "hi": 0.284})
    metrics.append({
        "id": "ensemble_crps_skill",
        "group": "ensemble",
        "label": "Ensemble: CRPS skill of log10 burned area vs the single run",
        "status": "partly_validated",
        "kind": "validation",
        "value": r(crpss["value"]),
        "display": f"+{crpss['value']:.2f}",
        "unit": "CRPSS (> 0 = better than the single run)",
        "ci95": [r(crpss["lo"]), r(crpss["hi"])],
        "ci95_note": "descriptive whole-fire bootstrap in [R49]",
        "n": n65,
        "delta": {"vs": "CRPS of log10 area, back-cast − 2014b rerun (registered test; lower is better)",
                  "mean": r(crps_d["mean"]), "ci95": [r(crps_d["lo"]), r(crps_d["hi"])],
                  "verdict": crps_d.get("reading", ""), "source": r49.ref("direction_aware_reading.crps_log10_area")},
        "comparators": [
            {"label": "CRPS of log10 area, back-cast", "value": r(crps["value"]),
             "display": f"{crps['value']:.3f} [{crps['lo']:.3f}, {crps['hi']:.3f}]", "source": r49.ref(lv + "crps_log10_area")},
            {"label": "CRPS of log10 area, 2014b rerun", "value": r(crps_2014b), "display": f"{crps_2014b:.3f}",
             "source": r49.ref("descriptive.pooled_summary_registered.nat2014.crps_log10_area")},
        ],
        "reference": {"label": "single deterministic run", "value": 0.0, "note": "CRPSS 0 = no better than one run"},
        "gloss": "The ensemble's area forecast beats a single run by about a quarter on the CRPS; "
                 "the gain follows the single run's better area on the back-cast grid.",
        "source": r49.ref(lv + "crpss_vs_det"),
    })

    ss = r49.get(lv + "spread_skill_ratio", {"value": 1.34, "lo": 1.13, "hi": 1.59})
    metrics.append({
        "id": "ensemble_spread_skill",
        "group": "ensemble",
        "label": "Ensemble: spread / skill ratio (RMSE of the ensemble mean over the member spread)",
        "status": "partly_validated",
        "kind": "validation",
        "value": r(ss["value"]),
        "display": f"{ss['value']:.2f}",
        "unit": "ratio (1 = well calibrated; > 1 = too narrow)",
        "ci95": [r(ss["lo"]), r(ss["hi"])],
        "ci95_note": "descriptive whole-fire bootstrap in [R49]",
        "n": n65,
        "delta": None,
        "comparators": [{"label": "Record value on 2014b (2026-10-08)", "value": rec["spread_skill_ratio"], "display": f"{rec['spread_skill_ratio']:.2f}",
                         "source": r49.ref("reproduction_vs_record.published_record.spread_skill_ratio")}],
        "reference": {"label": "calibrated", "value": 1.0, "note": "Fortin et al. (2014) eq. 15"},
        "gloss": "Members agree with each other more than they agree with the fires: the range is about a third too narrow.",
        "source": r49.ref(lv + "spread_skill_ratio"),
    })

    p10 = r49.get(lv + "p10_holds_90pct", {"value": 0.277, "lo": 0.170, "hi": 0.383})
    metrics.append({
        "id": "ensemble_p10_holds_90pct",
        "group": "ensemble",
        "label": "Ensemble: held-out fire-days where the P10 footprint held ≥ 90 % of the observed growth",
        "status": "partly_validated",
        "kind": "validation",
        "value": r(p10["value"]),
        "display": pct(p10["value"]),
        "unit": "share of fire-days",
        "ci95": [r(p10["lo"]), r(p10["hi"])],
        "ci95_note": "descriptive whole-fire bootstrap in [R49]",
        "n": n65,
        "delta": None,
        "comparators": [{"label": "Record value on 2014b (2026-10-08)", "value": rec["p10_recall_ge_0.9"], "display": pct(rec["p10_recall_ge_0.9"]),
                         "source": r49.ref("reproduction_vs_record.published_record.p10_recall_ge_0.9")}],
        "reference": {"label": "a worst case would hold ≥ 90 % of growth on about 90 % of days", "value": 0.90, "note": "P10 is not a worst case"},
        "gloss": "P10 is not a worst case: the early-arrival footprint contained nearly all of the real growth on only "
                 "about a quarter of days. Read it as the early end of the modelled range.",
        "source": r49.ref(lv + "p10_holds_90pct"),
    })

    top = r49.get("descriptive.pooled_summary_registered.backcast.top_bin_obs_freq", 0.435)
    top_2014b = r49.get("descriptive.pooled_summary_registered.nat2014.top_bin_obs_freq", 0.333)
    metrics.append({
        "id": "burn_probability_reliability_top_bin",
        "group": "ensemble",
        "label": "Burn probability reliability: share of cells at ~0.94 modelled probability that burned",
        "status": "partly_validated",
        "kind": "validation",
        "value": r(top),
        "display": pct(top),
        "unit": "observed burned share in the 0.8-1.0 probability bin (mean forecast ≈ 0.94)",
        "ci95": None,
        "ci95_note": "pooled over cells, no CI (descriptive in [R49])",
        "n": n65,
        "delta": None,
        "comparators": [{"label": "Same bin on the CFS 2014b rerun (the overview's 33 %)", "value": r(top_2014b), "display": pct(top_2014b),
                         "source": r49.ref("descriptive.pooled_summary_registered.nat2014.top_bin_obs_freq")}],
        "reference": {"label": "reliable forecast", "value": 0.94, "note": "cells forecast at 94 % should burn about 94 % of the time"},
        "gloss": "Burn probability is over-confident: cells the ensemble is almost sure will burn (about 95 %) "
                 "burned well under half the time, because every member shares the model's over-prediction.",
        "source": r49.ref("descriptive.pooled_summary_registered.backcast.top_bin_obs_freq"),
    })

    # 9. B2 log-area post-processing (opt-in, pre-registered PASS, over-dispersed)
    p1 = r65.get("arms.P1", {"coverage": [0.846, 0.741, 0.937], "crps": [0.376, 0.310, 0.448], "spread_skill": 0.553,
                             "d_coverage_vs_B": [0.354, 0.231, 0.483], "d_crps_vs_B": [-0.031, -0.092, 0.032],
                             "mapped_p10_footprint_recall_ge_0.9": 0.292, "shift": -0.25, "scale": 2.25})
    verdict = r65.get("decision.verdict", "PASS")
    metrics.append({
        "id": "ensemble_b2_postprocessing",
        "group": "ensemble",
        "label": "Ensemble log-area post-processing B2 (opt-in, not applied by default): P10-P90 coverage",
        "status": "pass_opt_in",
        "kind": "validation",
        "value": r(p1["coverage"][0]),
        "display": pct(p1["coverage"][0]),
        "unit": "share of fire-days",
        "ci95": [r(p1["coverage"][1]), r(p1["coverage"][2])],
        "n": r65.get("n_fire_days", 65),
        "delta": {"vs": "default ensemble (arm B)", "mean": r(p1["d_coverage_vs_B"][0]),
                  "ci95": [r(p1["d_coverage_vs_B"][1]), r(p1["d_coverage_vs_B"][2])],
                  "verdict": verdict, "source": r65.ref("arms.P1.d_coverage_vs_B")},
        "comparators": [
            {"label": "CRPS of log10 area (B2 P1)", "value": r(p1["crps"][0]), "display": f"{p1['crps'][0]:.3f}", "source": r65.ref("arms.P1.crps")},
            {"label": "Spread / skill (B2 P1; < 1 = too wide)", "value": r(p1["spread_skill"]), "display": f"{p1['spread_skill']:.2f}",
             "source": r65.ref("arms.P1.spread_skill")},
            {"label": "P10 footprint holds ≥ 90 % of growth (unchanged by B2)", "value": r(p1["mapped_p10_footprint_recall_ge_0.9"]),
             "display": pct(p1["mapped_p10_footprint_recall_ge_0.9"]), "source": r65.ref("arms.P1.mapped_p10_footprint_recall_ge_0.9")},
        ],
        "reference": {"label": "pre-registered rule PREREG_ENSEMBLE_B2", "value": 0.70,
                      "note": r65.get("decision.rule", "coverage ≥ 0.70, gain CI > 0, CRPS non-inferior")},
        "gloss": f"A shift and stretch of the members' areas (shift {p1['shift']} log10, stretch {p1['scale']}) passed its "
                 "pre-registered test but over-corrects (too wide) and moves no footprint. It stays opt-in; the app's range is the default ensemble above.",
        "source": r65.ref("arms.P1.coverage"),
    })

    # 10. Structure spread (Hamada) at Jasper 2024
    runs = r10.get("runs", [])
    primary = next((x for x in runs if x.get("config", {}).get("name") == "primary"), None)
    if primary:
        fs = primary["scores"]["destroyed"]["firesim"]
        band = primary["scores"]["destroyed"]["a_distance_band_count_matched"]
        kd = primary["kappa_diff_vs_a"]
        k, kb, kp, kr = fs["kappa"], band["kappa"], fs["precision"], fs["recall"]
        kd_pt, kd_lo, kd_hi = kd["point"], kd["p2.5"], kd["p97.5"]
        n_units = r10.get("units_scored", 1119)
    else:
        k, kb, kp, kr, kd_pt, kd_lo, kd_hi, n_units = 0.472, 0.516, 0.631, 0.676, -0.044, -0.157, 0.045, 1119
    metrics.append({
        "id": "structure_hamada_jasper_kappa",
        "group": "structures",
        "label": "House-to-house spread (Hamada, opt-in): Cohen's κ on Jasper 2024 destroyed buildings",
        "status": "failed_test",
        "kind": "validation",
        "value": r(k),
        "display": f"κ {k:.3f}",
        "unit": "Cohen's κ (0 = chance, 1 = perfect)",
        "ci95": None,
        "n": n_units,
        "n_note": f"{n_units} Jasper townsite buildings; pre-registered primary run (seeds imposed, 30 m cutoff)",
        "delta": {"vs": "count-matched distance band from the seeds (baseline a)", "mean": r(kd_pt), "ci95": [r(kd_lo), r(kd_hi)],
                  "verdict": "fail (Hamada does no better than distance)", "source": r10.ref("runs[0].kappa_diff_vs_a")},
        "comparators": [
            {"label": "Distance-band baseline κ", "value": r(kb), "display": f"κ {kb:.3f}",
             "source": r10.ref("runs[0].scores.destroyed.a_distance_band_count_matched.kappa")},
            {"label": "Precision / recall (destroyed)", "value": [r(kp), r(kr)], "display": f"{pct(kp)} / {pct(kr)}",
             "source": r10.ref("runs[0].scores.destroyed.firesim")},
        ],
        "reference": {"label": "decision rule: κ must exceed the distance-band and seeds-only baselines", "value": r(kb), "note": "not met"},
        "gloss": "Given where the embers entered the townsite, the house-to-house model predicts which buildings were lost no "
                 "better than distance from the first ignitions. Illustrative, for exercises only; never a loss forecast.",
        "source": r10.ref("runs[0].scores.destroyed.firesim.kappa"),
    })

    # 11. Long-range embers v4-v7: every pre-registered test failed; v8 design pending
    v4_dec = v4.get("decision", {"pass": False})
    v4_met = sum(1 for kk, vv in v4_dec.items() if kk != "pass" and vv is True)
    v5_dec = v5.get("decision", {"pass": False})
    v5_met = sum(1 for kk in ("bss_vs_c_positive_ci", "kappa_vs_b_positive_ci", "kappa_vs_d_positive_ci", "bss_vs_base_rate_positive_ci")
                 if v5_dec.get(kk) is True)
    v6_pooled = v6.get("pooled", {"diff": 0.53, "p2.5": 0.34, "p97.5": 0.75})
    v6_dec = v6.get("decision", {"embers_add_skill_A": False})
    v7_pooled = v7.get("pooled", {})
    v7_dec = v7.get("decision", {"A2_embers_add_skill": False})
    a2_key = next((kk for kk in v7_pooled if kk.startswith("A2") and "mae" in kk.lower() and isinstance(v7_pooled[kk], dict)), None)
    a2 = v7_pooled[a2_key] if a2_key else {"diff": 0.31, "p2.5": 0.07, "p97.5": 0.67}
    versions = [
        {"version": "v4", "test": "PREREG_LR4 (Glass, CZU, North Complex 2020; building loss)", "verdict": "FAIL",
         "detail": f"{v4_met} of 4 pooled conditions met", "passed": bool(v4_dec.get("pass", False)), "source": v4.ref("decision")},
        {"version": "v5", "test": "PREREG_LR5 (same fires; two-mode kernel)", "verdict": "FAIL",
         "detail": f"{v5_met} of 4 pooled conditions met; held-out AUC 0.70", "passed": bool(v5_dec.get("pass", False)), "source": v5.ref("decision")},
        {"version": "v6", "test": "PREREG_LR6 target A (community arrival, Glass/CZU/North Complex)", "verdict": "FAIL",
         "detail": f"arrival error with embers − without: {v6_pooled['diff']:+.2f} h [{v6_pooled['p2.5']:+.2f}, {v6_pooled['p97.5']:+.2f}] (worse)",
         "passed": bool(v6_dec.get("embers_add_skill_A", False)), "source": v6.ref("pooled")},
        {"version": "v7", "test": "PREREG_LR7 target A (fresh fires Dixie, Caldor 2021)", "verdict": "FAIL",
         "detail": f"embers vs calibrated front, MAE: {a2['diff']:+.2f} h [{a2['p2.5']:+.2f}, {a2['p97.5']:+.2f}] (worse)",
         "passed": bool(v7_dec.get("A2_embers_add_skill", False)), "source": v7.ref(f"pooled.{a2_key}" if a2_key else "pooled")},
        {"version": "v8", "test": "study design [R54], draft for owner review", "verdict": "pending",
         "detail": "not pre-registered; no decision-fire data opened", "passed": None, "source": report_text("[R54]", "docs/PROJECT_RECORD.md §4.2")},
    ]
    metrics.append({
        "id": "long_range_embers",
        "group": "embers",
        "label": "Long-range embers (opt-in, PRs #59/#81/#83/#87, not merged): pre-registered tests v4-v7",
        "status": "failed_test",
        "kind": "validation",
        "value": 0,
        "display": "v4-v7 all FAILED their pre-registered tests; v8 design pending owner review",
        "unit": "pre-registered tests passed",
        "ci95": None,
        "n": 4,
        "delta": None,
        "comparators": [],
        "reference": {"label": "pass rule of each PREREG_LR*", "value": None, "note": "none met"},
        "gloss": "Every version of long-range ember spotting has failed its pre-registered held-out test (building loss and "
                 "community arrival). It is not in the app's default model; a v8 design awaits the owner's review.",
        "source": v7.ref("decision"),
        "versions": versions,
    })

    return metrics


# ── README ───────────────────────────────────────────────────────────────────────────────────────
def summary_row(card: dict[str, Any], fname: str) -> str:
    by_id = {m["id"]: m for m in card["metrics"]}

    def disp(mid: str) -> str:
        return by_id[mid]["display"] if mid in by_id else "—"

    return (f"| {card['date']} | `{card['git_commit'][:7]}` | {card['engine_version']} | {card.get('report') or '—'} | "
            f"{disp('heldout_f1_17h')} | {disp('area_bias_17h')} | {disp('ensemble_coverage_p10_p90')} | {disp('ensemble_spread_skill')} | "
            f"{disp('structure_hamada_jasper_kappa')} | {'FAILED v4-v7' if 'long_range_embers' in by_id else '—'} | [`{fname}`]({fname}) |")


def write_readme(out_dir: Path) -> None:
    rows = []
    for f in sorted(out_dir.glob("*.json")):
        try:
            rows.append(summary_row(json.load(open(f)), f.name))
        except Exception as exc:  # noqa: BLE001 - a bad file is listed, not hidden
            rows.append(f"| {f.stem} | — | — | — | — | — | — | — | — | — | [`{f.name}`]({f.name}) (unreadable: {exc}) |")
    table = "\n".join(rows) if rows else "| — | | | | | | | | | | |"
    text = f"""# FireSim validation scorecards

Dated, per-release snapshots of FireSim's validation numbers (schema version {SCHEMA_VERSION}). Each file
is built by `python scripts/make_scorecard.py --date YYYY-MM-DD` from the scored result files of the
numbered reports; nothing is re-run. Every metric carries its report number, source file, JSON path
and SHA-256. The API serves the latest file at `GET /api/v1/scorecard`; the app shows it under
**Validation** in the top bar. Rules and field definitions: [`docs/validation-scorecard.md`](../validation-scorecard.md).

{FRAMING}

Status words: **validated** = matches its reference (equations); **partly validated** = scored on observed
fires with real but limited skill; **failed test** = missed its pre-registered decision rule;
**pass (opt-in)** = passed its pre-registered rule but is not applied by default. Held-out F1, area
bias and the ensemble scores are on the pre-fire (back-cast) Alberta fuel grid, the fuel FireSim uses
for dated historical Alberta replays [R45], [R49].

| Date | Commit | Engine | Report | Held-out F1 (17 h) | Area bias | P10-P90 coverage | Spread / skill | Hamada κ (Jasper) | Long-range embers | File |
|---|---|---|---|---|---|---|---|---|---|---|
{table}

*Regenerated by `scripts/make_scorecard.py`; do not edit by hand.*
"""
    (out_dir / "README.md").write_text(text)


# ── main ─────────────────────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the dated FireSim validation scorecard from existing scored results.")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="scorecard date YYYY-MM-DD (file name)")
    ap.add_argument("--commit", default=None, help="git commit the scorecard describes (default: HEAD)")
    ap.add_argument("--report", default=None, help="report number of the scorecard report, e.g. R69")
    ap.add_argument("--reports-data", default=os.environ.get("FIRESIM_REPORTS_DATA", str(HOME / "dev/wildfire/reports/data")))
    ap.add_argument("--validation-data", default=os.environ.get("FIRESIM_VALIDATION_DATA", str(HOME / "dev/wildfire/validation-data")))
    ap.add_argument("--out-dir", default=str(REPO / "docs" / "scorecard"))
    ap.add_argument("--no-derived", action="store_true", help="skip the derived bootstrap CI of the F1 level")
    args = ap.parse_args(argv)

    dt.date.fromisoformat(args.date)  # validates
    reports_data, validation_data = Path(args.reports_data).expanduser(), Path(args.validation_data).expanduser()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics = build_metrics(reports_data, validation_data, derived=not args.no_derived)
    missing = [m["id"] for m in metrics if m["source"].get("kind") == "report text"]
    card = {
        "schema_version": SCHEMA_VERSION,
        "title": "FireSim validation scorecard",
        "date": args.date,
        "git_commit": git_commit(args.commit),
        "engine_version": engine_version(),
        "report": f"[{args.report}]" if args.report else None,
        "framing": FRAMING,
        "fuel_grid": {
            "spread_and_ensemble": "Alberta Wildfire FBP fuel types 2025 (100 m) back-cast to each fire's start (NBAC burn key + SCANFI v2 "
                                   "epoch; docs/fuel-grid-backcast.md): the fuel FireSim uses for dated historical Alberta replays",
            "comparator": "CFS national FBP fuel grid 2014b (the record's former headline grid; internal-use licence)",
            "scope": "historical Alberta replays (fires 2014-2024). Live, undated runs on the 2025 grid without a back-cast are not scored on observed fires",
            "structures_and_embers": "building footprints and observed damage (Jasper 2024 Parks Canada / Municipality status; CAL FIRE DINS), not a fuel grid",
        },
        "data": {
            "spread": "CFSDS v1.1 (Barber et al. 2024): 143 fire-days of 32 Alberta fires 2014-2024; held-out = 16 test fires / 79 fire-days (Bennett et al. 2026 protocol)",
            "ensemble": "65 held-out fire-days of 16 fires (the 14 largest excluded by run time), 20 members",
            "structures": "Jasper 2024 townsite, 1,119 buildings",
            "embers": "CAL FIRE DINS building outcomes and community-block arrival (Glass, CZU, North Complex 2020; Dixie, Caldor 2021)",
        },
        "release_rescore_set": {
            "doc": "docs/SEALED_2026.md",
            "set": "`scorecard` 16 of the sealed 2026 US block (never decides; descriptive per-release re-score once pre-registered)",
            "list_sha256": "b9c7a303c71e63ebc63e9fdf25cda18e8ebcaf61a5af36a3ac69cdd297748e80",
            "status": "sealed; no model has run on these fires; this scorecard uses no sealed fire",
        },
        "metrics": metrics,
        "unsourced_from_files": missing,
        "generated_by": {"script": "scripts/make_scorecard.py", "argv": sys.argv[1:],
                         "built": dt.datetime.now().astimezone().isoformat(timespec="seconds")},
    }
    out = out_dir / f"{args.date}.json"
    text = json.dumps(card, indent=2, ensure_ascii=False) + "\n"
    if "/home/" in text:
        raise SystemExit("refusing to write: a /home/ path leaked into the scorecard")
    out.write_text(text)
    write_readme(out_dir)
    print(f"wrote {out} ({len(metrics)} metrics; {len(missing)} from report text: {missing or 'none'})")
    print(f"wrote {out_dir / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
