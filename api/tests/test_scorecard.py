"""API: ``GET /api/v1/scorecard`` serves the latest dated validation scorecard.

* The committed ``docs/scorecard/<date>.json`` is served as-is, with ``file`` and ``available_dates``.
* With ``FIRESIM_SCORECARD_DIR`` pointing elsewhere, the newest date wins and all dates are listed;
  an empty directory gives 404. The file is read once (cached) until ``reset_cache()``.
* The committed scorecard itself is checked: schema, every metric traceable to a report number and a
  file or doc path, the preparedness/training framing, and no local ``/home/`` paths or SFOC numbers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from firesim_api.main import create_app
from firesim_api.routers import scorecard

REPO = Path(__file__).resolve().parents[2]
SCORECARD_DIR = REPO / "docs" / "scorecard"
REQUIRED_METRICS = {
    "fbp_cffdrs_conformance", "heldout_f1_17h", "area_bias_17h", "ensemble_coverage_p10_p90",
    "ensemble_crps_skill", "ensemble_spread_skill", "ensemble_p10_holds_90pct",
    "burn_probability_reliability_top_bin", "structure_hamada_jasper_kappa", "long_range_embers",
}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.delenv("FIRESIM_SCORECARD_DIR", raising=False)
    scorecard.reset_cache()
    yield
    scorecard.reset_cache()


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _write(d: Path, date: str, **extra) -> None:
    card = {"schema_version": 1, "date": date, "git_commit": "abc", "engine_version": "3.0.0", "metrics": [], **extra}
    (d / f"{date}.json").write_text(json.dumps(card))


class TestEndpoint:
    async def test_serves_committed_latest(self, client):
        resp = await client.get("/api/v1/scorecard")
        assert resp.status_code == 200
        data = resp.json()
        dates = sorted(p.stem for p in SCORECARD_DIR.glob("????-??-??.json"))
        assert dates, "docs/scorecard holds at least one dated scorecard"
        assert data["file"] == f"{dates[-1]}.json"
        assert data["date"] == dates[-1]
        assert data["available_dates"] == dates
        assert data["schema_version"] == 1
        assert {m["id"] for m in data["metrics"]} >= REQUIRED_METRICS

    async def test_latest_date_wins_and_is_cached(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("FIRESIM_SCORECARD_DIR", str(tmp_path))
        _write(tmp_path, "2026-10-25")
        _write(tmp_path, "2026-11-30", git_commit="later")
        (tmp_path / "README.md").write_text("not a scorecard")
        (tmp_path / "notes.json").write_text("{}")  # not a dated file: ignored
        scorecard.reset_cache()
        resp = await client.get("/api/v1/scorecard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file"] == "2026-11-30.json" and data["git_commit"] == "later"
        assert data["available_dates"] == ["2026-10-25", "2026-11-30"]
        # Cached: a file added after the first read is not seen until reset
        _write(tmp_path, "2026-12-31", git_commit="newest")
        assert (await client.get("/api/v1/scorecard")).json()["file"] == "2026-11-30.json"
        scorecard.reset_cache()
        assert (await client.get("/api/v1/scorecard")).json()["file"] == "2026-12-31.json"

    async def test_no_scorecard_is_404(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("FIRESIM_SCORECARD_DIR", str(tmp_path / "missing"))
        scorecard.reset_cache()
        resp = await client.get("/api/v1/scorecard")
        assert resp.status_code == 404
        assert "scorecard" in resp.json()["detail"].lower()

    async def test_unreadable_file_is_404_not_a_crash(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("FIRESIM_SCORECARD_DIR", str(tmp_path))
        (tmp_path / "2026-10-25.json").write_text("{not json")
        scorecard.reset_cache()
        assert (await client.get("/api/v1/scorecard")).status_code == 404


class TestCommittedScorecard:
    """The published artifact: traceable numbers, the framing, nothing private."""

    @pytest.fixture(scope="class")
    def cards(self):
        files = sorted(SCORECARD_DIR.glob("????-??-??.json"))
        assert files
        return [(f, json.loads(f.read_text(encoding="utf-8"))) for f in files]

    def test_schema_and_identity(self, cards):
        for f, c in cards:
            assert c["schema_version"] == 1
            assert c["date"] == f.stem
            assert re.fullmatch(r"[0-9a-f]{7,40}", c["git_commit"]), f"{f.name}: git_commit"
            assert c["engine_version"]
            assert c["fuel_grid"]["spread_and_ensemble"]
            assert c["report"] is None or re.fullmatch(r"\[R\d+[a-z-]*\]", c["report"])

    def test_every_metric_is_traceable(self, cards):
        for f, c in cards:
            ids = {m["id"] for m in c["metrics"]}
            assert ids >= REQUIRED_METRICS, f"{f.name}: missing {REQUIRED_METRICS - ids}"
            for m in c["metrics"]:
                src = m["source"]
                assert src["report"], f"{f.name}: {m['id']} has no report number"
                assert src["kind"] in {"file", "doc", "report text"}
                assert src["file"], f"{f.name}: {m['id']} has no file path"
                if src["kind"] == "file":
                    assert re.fullmatch(r"[0-9a-f]{64}", src["sha256"])
                    assert src["path"]
                assert m["display"] and m["gloss"] and m["label"]
                assert m["status"] in {"validated", "partly_validated", "not_validated", "failed_test", "pass_opt_in", "pending"}
                if m["ci95"] is not None:
                    lo, hi = m["ci95"]
                    assert lo <= m["value"] <= hi, f"{f.name}: {m['id']} value outside its CI"
                if m.get("delta"):
                    assert m["delta"]["source"]["report"]
                for comp in m.get("comparators", []):
                    assert comp["source"]["report"], f"{f.name}: {m['id']} comparator '{comp['label']}' unsourced"
            # Anything not read from a file is declared
            declared = set(c["unsourced_from_files"])
            assert declared == {m["id"] for m in c["metrics"] if m["source"]["kind"] == "report text"}

    def test_headline_numbers_match_the_record(self, cards):
        """The record's headline numbers (docs/PROJECT_RECORD.md §4.4, [R45] [R49] [R10])."""
        _, c = cards[-1]
        by_id = {m["id"]: m for m in c["metrics"]}
        assert by_id["heldout_f1_17h"]["value"] == pytest.approx(0.246, abs=0.0005)
        assert by_id["heldout_f1_17h"]["delta"]["ci95"] == pytest.approx([0.007, 0.058], abs=0.0005)
        assert by_id["area_bias_17h"]["value"] == pytest.approx(0.26, abs=0.005)
        assert by_id["ensemble_coverage_p10_p90"]["value"] == pytest.approx(0.49, abs=0.005)
        assert by_id["ensemble_spread_skill"]["value"] == pytest.approx(1.34, abs=0.005)
        assert by_id["ensemble_p10_holds_90pct"]["value"] == pytest.approx(0.28, abs=0.005)
        assert by_id["structure_hamada_jasper_kappa"]["value"] == pytest.approx(0.472, abs=0.0005)
        assert by_id["structure_hamada_jasper_kappa"]["status"] == "failed_test"
        embers = by_id["long_range_embers"]
        assert embers["status"] == "failed_test"
        assert [v["verdict"] for v in embers["versions"]] == ["FAIL", "FAIL", "FAIL", "FAIL", "pending"]
        assert by_id["fbp_cffdrs_conformance"]["status"] == "validated"

    def test_framing_and_privacy(self, cards):
        for f, c in cards:
            text = f.read_text(encoding="utf-8")
            assert "preparedness and training tool" in c["framing"]
            assert "not for real-time operational prediction" in c["framing"]
            assert "/home/" not in text, f"{f.name}: local path"
            assert not re.search(r"SFOC[- ]?\d|ATS-\d", text), f"{f.name}: SFOC reference"
            assert "address" not in text.lower() or "no addresses" in text.lower()
