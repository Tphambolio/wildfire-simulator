"""Loader guard of the ML ember-cast data infrastructure (PREREG_ML_EMBER_DEV section 2, section 14 amendment 16,
addendum A1): every sealed path is refused and logged; the count-only files and training paths pass; the only
override is --confirmation-commit <sha> AND that sha existing on origin (remote check mocked here). Runs on a
synthetic tree; the data pool is not needed."""
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pandas")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
L = importlib.import_module("scripts.ml_ember.loader")
P = importlib.import_module("scripts.ml_ember.paths")

ASSIGN = {"NBAC:T1": "training", "NBAC:D1": "decision", "MTBS:L1": "training_legacy"}


@pytest.fixture
def tree(tmp_path):
    pool = tmp_path / "validation-data" / "v8-pool"
    files = [
        "viirs_decision/NBAC_D1.pkl.gz", "final/anything.csv", "elmfire_runs/NBAC_D1/out.nc",
        "draw/sealed_block_01.csv", "draw/sealed/manifest.csv", "draw/sealed_manifest.csv",
        "fires/NBAC_D1/intervals/00001.npz", "fires/MTBS_L1/intervals/00001.npz", "fires/NBAC_X9/intervals/00001.npz",
        "fires/NBAC_D1/meta.json", "fires/NBAC_T1/intervals/00001.npz", "fires/NBAC_T1/inputs.npz", "fires/NBAC_T1/meta.json",
        "draw/counts.csv", "draw/state.json", "draw/draw_log.txt", "viirs_training/viirs_training_2018.pkl.gz",
        "rule_A1/jumps_training.csv", "fit/fit_beta.json", "raw/mtbs/mtbs_perims_DD.shp",
    ]
    for f in files:
        p = pool / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
    sealed = tmp_path / "validation-data" / "sealed2026"
    for f in ("README.md", "_about.json", "us/sealed_us_2026.csv", "ca/README.md"):
        p = sealed / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
    res = tmp_path / "reports" / "data" / "long-range-embers-v8-2026-10-29" / "results"
    for f in P.SEALED_V8_RESULT_FILES + ("results/counts.csv", "results/gate.json"):
        p = res.parent / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
    return dict(pool=pool, vd=tmp_path / "validation-data", res=res.parent, log=tmp_path / "out" / "refusals.log")


def make_guard(t, **kw):
    return L.Guard(pool=t["pool"], validation_data=t["vd"], v8_result_folder=t["res"], assignment=ASSIGN,
                   refusals_log=t["log"], **kw)


SEALED = ["viirs_decision/NBAC_D1.pkl.gz", "viirs_decision", "final/anything.csv", "final", "elmfire_runs/NBAC_D1/out.nc",
          "draw/sealed_block_01.csv", "draw/sealed/manifest.csv", "draw/sealed_manifest.csv",
          "fires/NBAC_D1/intervals/00001.npz", "fires/NBAC_D1/intervals", "fires/MTBS_L1/intervals/00001.npz",
          "fires/NBAC_X9/intervals/00001.npz"]
ALLOWED = ["draw/counts.csv", "draw/state.json", "draw/draw_log.txt", "fires/NBAC_T1/intervals/00001.npz",
           "fires/NBAC_T1/inputs.npz", "fires/NBAC_T1/meta.json", "fires/NBAC_D1/meta.json",
           "viirs_training/viirs_training_2018.pkl.gz", "rule_A1/jumps_training.csv", "fit/fit_beta.json",
           "raw/mtbs/mtbs_perims_DD.shp"]


@pytest.mark.parametrize("rel", SEALED)
def test_refuses_each_sealed_pool_path(tree, rel):
    g = make_guard(tree)
    with pytest.raises(L.SealedPathError):
        g.check(tree["pool"] / rel)
    with pytest.raises(L.SealedPathError):
        g.read_bytes(tree["pool"] / rel) if (tree["pool"] / rel).is_file() else g.listdir(tree["pool"] / rel)
    assert tree["log"].exists() and rel.split("/")[-1] in tree["log"].read_text()
    assert "REFUSED" in tree["log"].read_text()


@pytest.mark.parametrize("rel", ALLOWED)
def test_allows_training_and_count_files(tree, rel):
    g = make_guard(tree)
    assert g.read_bytes(tree["pool"] / rel) == b"x"
    assert not tree["log"].exists()


def test_sealed2026_folder_except_its_own_readme_and_about(tree):
    g = make_guard(tree)
    assert g.read_text(tree["vd"] / "sealed2026" / "README.md") == "x"
    assert g.read_text(tree["vd"] / "sealed2026" / "_about.json") == "x"
    for rel in ("us/sealed_us_2026.csv", "ca/README.md", "us"):
        with pytest.raises(L.SealedPathError):
            g.check(tree["vd"] / "sealed2026" / rel)
    with pytest.raises(L.SealedPathError):
        g.check(tree["vd"] / "sealed2026")


@pytest.mark.parametrize("rel", P.SEALED_V8_RESULT_FILES)
def test_v8_per_jump_result_files_sealed(tree, rel):
    g = make_guard(tree)
    with pytest.raises(L.SealedPathError):
        g.read_text(tree["res"] / rel)
    assert g.read_text(tree["res"] / "results" / "counts.csv") == "x"  # count-only result stays readable


def test_override_needs_flag_and_remote_sha(tree):
    sealed = tree["pool"] / "final" / "anything.csv"
    calls = []

    def remote_no(sha):
        calls.append(sha); return False

    def remote_yes(sha):
        calls.append(sha); return True

    with pytest.raises(L.SealedPathError):  # flag, sha not on origin
        make_guard(tree, confirmation_commit="abc1234", remote_check=remote_no).read_bytes(sealed)
    assert calls == ["abc1234"] and "not found on origin" in tree["log"].read_text()
    with pytest.raises(L.SealedPathError):  # no flag: the remote is never consulted
        make_guard(tree, remote_check=remote_yes).read_bytes(sealed)
    assert calls == ["abc1234"]
    g = make_guard(tree, confirmation_commit="abc1234", remote_check=remote_yes)  # both: allowed and logged
    assert g.read_bytes(sealed) == b"x"
    assert "OVERRIDE confirmation_commit=abc1234" in tree["log"].read_text()


def test_remote_check_rejects_non_sha_without_git():
    assert L.remote_has_commit("not a sha") is False
    assert L.remote_has_commit("") is False


def test_loader_never_writes(tree):
    g = make_guard(tree)
    for mode in ("w", "a", "r+", "wb"):
        with pytest.raises(ValueError):
            g.open(tree["pool"] / "fires/NBAC_T1/meta.json", mode)


def test_assert_training_and_key_lists(tree):
    g = make_guard(tree)
    g.assert_training("NBAC:T1")
    for k in ("NBAC:D1", "MTBS:L1", "NBAC:X9"):
        with pytest.raises(L.SealedPathError):
            g.assert_training(k)
    assert g.training_keys() == ["NBAC:T1"] and g.legacy_keys() == ["MTBS:L1"]


def test_load_npz_goes_through_guard(tree):
    g = make_guard(tree)
    p = tree["pool"] / "fires/NBAC_T1/intervals/00001.npz"
    np.savez(p, a=np.arange(3))
    assert list(g.load_npz(p)["a"]) == [0, 1, 2]
    bad = tree["pool"] / "fires/NBAC_D1/intervals/00001.npz"
    np.savez(bad, a=np.arange(3))
    with pytest.raises(L.SealedPathError):
        g.load_npz(bad)


def test_sealed_list_matches_prereg_section_2_and_a1():
    assert set(P.SEALED_POOL_SUBTREES) == {"viirs_decision/", "final/", "elmfire_runs/"}
    assert P.SEALED_POOL_GLOBS == ("draw/sealed*",) and "draw/sealed_manifest.csv" in P.SEALED_POOL_FILES
    assert set(P.SEALED_V8_RESULT_FILES) == {"results/jumps.csv", "results/ranks_per_jump.csv", "results/sealed_manifest.csv",
                                             "results/score_out.json", "results/secondary.json", "results/sensitivities.json",
                                             "results/unseal_exclusions.json"}
    assert set(P.ALLOWED_COUNT_FILES) == {"draw/counts.csv", "draw/state.json", "draw/draw_log.txt"}
