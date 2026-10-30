"""Post-hoc fuel-seam sensitivity of the v8 result [R70] (exploratory, disclosed; never deciding).

Purpose
    The ML ember-cast project's negative control 6 found that the Canadian fuel code predicts "inside the final
    perimeter" within a fire (AUC 0.72; hazard class alone 0.554; the US shows none). The Canadian pre-fire
    fuel map is a back-cast (FireSim #79: cells burned since the scenario date are re-typed from SCANFI), so the
    code itself may carry the final footprint. This script asks whether the registered v8 margin
    S3(M8 - B1) depends on that: it rescores the Canadian decision fires with the fuel layer restricted to the
    B1 hazard class only, for both M8 and B1, everything else frozen.

What "hazard class only" means here (fixed before any rescored number was seen)
    Every burnable 125 m cell keeps only its B1 hazard class (Canada: C and M types hazardous, PREREG_LR8 §5;
    ``inputs.npz`` ``hazard``). The code layer handed to the frozen interval engine is replaced by one code per
    class: hazardous -> C-2 (code 2, the FBP default type); burnable non-hazardous -> D-1/D-2 by FireSim's leaf
    rule (code 13). Non-fuel cells (codes 0, 100-106) are unchanged, so the candidate set (PREREG §6, the domain
    of the metric, shared by every model) is identical to the registered one; this is checked cell by cell.
    Variant ``o1b``: burnable non-hazardous -> O-1b (code 32, with the registered NDVI curing), to show the
    representative choice does not drive the result. B1 reads fuel only through the hazard share and the
    candidate set, so B1 under hazard-only equals the registered B1 (checked to the float).
    Frozen: perimeters, active segments, weather, beta (fit_beta.json, fitted on the full codes; not refit),
    kernels, candidate cells, ranks, S3 weights, sign-flip and bootstrap seeds (v8_score.py, sha256 checked).

How it runs
    The registered engine ``intervals.run_fire`` (sha256 4303e544...) is run unchanged on a shadow fire
    directory per fire (symlinked meta.json and weather, an ``inputs.npz`` with the substituted code), with
    ``common.fire_dir`` and ``common.RUNS_CSV`` pointed at the sensitivity folder; no registered file is
    written. Candidates for the rescored fires get score_M8 from the shadow run and score_B1 / score_B1all copied
    from ``final/candidates``; US fires keep their registered candidate files (pooled figure only).

Usage
    capjob python3 -m v8.fuel_seam run [variant ...]      # from scripts/validation, PYTHONPATH=engine/src
    python3 -m v8.fuel_seam score
Output
    ``$FIRESIM_V8_DATA/sensitivity/fuel_seam_2026-10-29/`` (shadow fires, ``<variant>/candidates``,
    ``fuel_seam.json``). Sealed for the ML project's confirmation set: never shared with another session.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import common as C

OUT = C.DATA / "sensitivity" / "fuel_seam_2026-10-29"
FINAL = C.DATA / "final"
NONFUEL = [0, 100, 101, 102, 103, 104, 105, 106]
VARIANTS = {"hazard_c2_d": (2, 13), "hazard_c2_o1b": (2, 32)}
REG_SHA = {"intervals.py": "4303e544736968f29f28dd827f662b0f6dfed9178109154ed309d7a5b4724dbe",
           "common.py": "8eef29c54a294136fda7aec231e4c3dd32a7082ce86f4361087c805c33608d92",
           "../v8_score.py": "623233f687eb97d83a5cd3d81806accb28f78a6ef699f941249247c38ebad880"}


def check_frozen():
    here = Path(__file__).resolve().parent
    for f, h in REG_SHA.items():
        if C.sha256(here / f) != h:
            raise RuntimeError(f"{f} differs from the registered sha256")


def surrogate_code(code: np.ndarray, hazard: np.ndarray, haz_code: int, other_code: int) -> np.ndarray:
    """Fuel code with only the hazard class kept on burnable cells; non-fuel cells unchanged."""
    burnable = ~np.isin(code, NONFUEL) & (code > 0)
    out = code.copy()
    out[burnable & (hazard > 0)] = haz_code
    out[burnable & (hazard == 0)] = other_code
    return out


def can_fires() -> list[str]:
    R = pd.read_csv(FINAL / "ranks_per_jump.csv")
    return sorted(R[R.region == "CAN"].fire_key.unique())


def run(variants):
    check_frozen()
    from . import intervals as IV
    split = C.split_table().set_index("fire_key").assignment
    C.RUNS_CSV = OUT / "runs.csv"
    for v in variants:
        hc, oc = VARIANTS[v]
        shadow_root = OUT / v / "fires"
        C.fire_dir = lambda k, _r=shadow_root: (_r / k.replace(":", "_"))
        for k in can_fires():
            src = C.FIRES / k.replace(":", "_")
            dst = shadow_root / k.replace(":", "_")
            dst.mkdir(parents=True, exist_ok=True)
            for p in [src / "meta.json", *src.glob("wx_*.csv")]:
                q = dst / p.name
                if not q.exists():
                    q.symlink_to(p)
            z = dict(np.load(src / "inputs.npz"))
            z["code"] = surrogate_code(z["code"], z["hazard"], hc, oc)
            np.savez_compressed(dst / "inputs.npz", **z)
            r = IV.run_fire(k, split[k])
            print(v, k, r["status"], r.get("n_intervals"), f"{r.get('seconds', 0):.0f}s", flush=True)
        # candidates: rescored M8, registered B1 / B1all; US fires as registered
        cd = OUT / v / "candidates"
        cd.mkdir(parents=True, exist_ok=True)
        beta = json.loads((C.DATA / "fit" / "fit_beta.json").read_text())["beta"]
        checks = {"cells_identical": True, "B1_identical": True, "n_files": 0}
        for p in sorted((FINAL / "candidates").glob("*.npz")):
            fire, ep = p.stem.split("__")
            reg = dict(np.load(p))
            if fire in can_fires():
                zz = np.load(shadow_root / fire.replace(":", "_") / "intervals" / f"{int(ep):05d}.npz")
                info = json.loads((shadow_root / fire.replace(":", "_") / "intervals" / "_done.json").read_text())
                cell = zz["bj"].astype(np.int64) * info["block_shape"][1] + zz["bi"]
                same = np.array_equal(cell, reg["cell_id"])
                checks["cells_identical"] &= bool(same)
                checks["B1_identical"] &= bool(same and np.array_equal(IV.b1_score(zz, True), reg["score_B1"])
                                               and np.array_equal(IV.b1_score(zz, False), reg["score_B1all"]))
                arr = dict(cell_id=reg["cell_id"], score_M8=IV.m8_score(zz, beta), score_B1=reg["score_B1"],
                           score_B1all=reg["score_B1all"])
                checks["n_files"] += 1
            elif fire.startswith("NBAC"):
                continue  # Canadian fires without ranked jumps do not enter S3 or the AUCs
            else:
                arr = {k_: reg[k_] for k_ in ("cell_id", "score_M8", "score_B1", "score_B1all")}
            np.savez(cd / p.name, **arr)
        for f in ("jumps.csv", "gate.json", "counts.csv"):
            shutil.copy(FINAL / f, OUT / v / f)
        (OUT / v / "checks.json").write_text(json.dumps(checks, indent=1))
        print(v, checks, flush=True)


def _score_module():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "v8_score.py"
    spec = importlib.util.spec_from_file_location("v8_score", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def summarise(S, d: Path, region: str | None):
    cands, jumps, gate, counts = S.load_dir(d)
    if region:
        jumps = [j for j in jumps if j[1] == region]
    pf = S.per_fire(cands, jumps, ["M8", "B1"])
    res = S.decide(pf, None, None)
    fires = sorted(pf)
    a = np.array([r["a_f"] for r in res["per_fire"]])
    w = S.weights(np.array([r["K"] for r in res["per_fire"]]), "S3")
    reg = np.array([r["region"] for r in res["per_fire"]])
    out = {"n_fires": len(fires), "n_jumps": res["K_total"], "S3": res["S3"]["est"], "boot95": res["S3"]["boot95"],
           "p_signflip": res["S3"]["p_signflip"], "auc_M8": res["region_auc_M8"], "auc_B1": res["region_auc_B1"]}
    if region:
        out["boot95_region"] = S.boot_ci(a, w, reg)
    return out


def score():
    check_frozen()
    S = _score_module()
    res = {"registered": {"CAN": summarise(S, FINAL, "CAN"), "pooled": summarise(S, FINAL, None)}}
    for v in VARIANTS:
        if (OUT / v / "checks.json").exists():
            res[v] = {"checks": json.loads((OUT / v / "checks.json").read_text()),
                      "CAN": summarise(S, OUT / v, "CAN"), "pooled": summarise(S, OUT / v, None)}
    (OUT / "fuel_seam.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    C.guard()
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "run":
        run(sys.argv[2:] or list(VARIANTS))
    else:
        score()
