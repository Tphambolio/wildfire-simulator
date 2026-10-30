#!/usr/bin/env bash
# PREREG_SEALED_CONFIRM_H1H2 (docs/PREREG_SEALED_CONFIRM_H1H2.md): every run of the test, in the registered order.
# Purpose: integrity checks on Alberta data, then build the sealed confirm inputs (opens the sealed list),
# then H1 (B then A, mature fire-days, code of the R63 registration 12cf308) and H2 (deterministic C1, then the
# 20-member A15 ensemble on fire-days whose deterministic run took <= CAP_S, code of this branch = master engine).
# Each model run is launched under capjob (5G cap), one at a time, only when `free -g` shows >= 8 GB available.
# Resumable: validate.py skips fire-days already in a run file. Usage: run_confirm.sh <step> (0a 0b 1 2 3 4 | all)
set -euo pipefail
STEP=${1:-all}
W=/home/rpas/dev/wildfire
WT=$W/wt-sealed-confirm                 # this branch (master engine + scripts)
WT1=$W/wt-sealed-confirm-h1code         # detached checkout of 12cf3085 (R63 registered code)
H1SHA=12cf3085dd698a146a12fb4e1a2d2099235c2d43
VD=$W/validation-data
ROOT=$VD/sealed2026-confirm
RUNS=$ROOT/runs
CHK=$ROOT/integrity
OUT=$W/reports/data/sealed-confirm-h1h2-2026-10-25
CAP_S=120
A15='{"wind_dir_sd_deg":24,"wind_speed_log_sd":0.405,"ffmc_sd":7.2,"dmc_dc_log_sd":0.6,"curing_sd":20.25,"fmc_sd":15,"ros_log_sd":0.825}'
COMMON="--ignition active --ffmc-spinup --burning-period 10 20"
mkdir -p "$RUNS" "$CHK"

wait_mem() {
  local a; a=$(free -g | awk '/Mem:/{print $7}')
  while [ "$a" -lt 8 ]; do sleep 30; a=$(free -g | awk '/Mem:/{print $7}'); done
  echo "$(date -Is) start $1 (available ${a} G)"
}

vrun() {  # vrun <worktree> <data root> <domains dir> <runs dir> <log name> <validate.py run args...>
  local wt=$1 root=$2 dom=$3 rd=$4 name=$5; shift 5
  wait_mem "$name"
  (cd "$wt" && FIRESIM_VALIDATION_DATA="$root" PYTHONPATH="$wt/engine/src" CAPJOB_MEM=5G \
    /home/rpas/.local/bin/capjob python3 -W ignore scripts/validate.py --domains-dir "$dom" --runs-dir "$rd" \
    run "$@" > "$rd/$name.log" 2>&1)
  echo "$(date -Is) $name done"; tail -1 "$rd/$name.log"
}

one_fire_root() {  # one_fire_root <dir> <fire_id>: Alberta data root with a one-fire manifest
  local d=$1 fid=$2
  mkdir -p "$d"; ln -sfn "$VD/cfsds" "$d/cfsds"; ln -sfn "$VD/weather" "$d/weather"
  python3 -c "import json,sys; m=json.load(open('$VD/manifest.json')); \
json.dump([x for x in m if x['fire_id']=='$fid'], open('$d/manifest.json','w'), indent=1)"
}

if [[ $STEP == 0a || $STEP == all ]]; then
  # H1 code integrity: 12cf308 reproduces the R63 arm-B records of Alberta fire 2019_148 (mature held-out days)
  [ "$(git -C "$WT1" rev-parse HEAD)" = "$H1SHA" ] || { echo "H1 worktree not at $H1SHA"; exit 1; }
  [ -z "$(git -C "$WT1" status --porcelain)" ] || { echo "H1 worktree not clean"; exit 1; }
  one_fire_root "$CHK/h1_root" 2019_148
  vrun "$WT1" "$CHK/h1_root" "$VD/backcast/roots/ab2025_backcast/domains" "$CHK" h1_B_2019_148 \
    --name h1_B_2019_148 $COMMON --start-types mature --hold-hours 24 --active-days 2 \
    --windows 4 6 8 10 12 14 17 24 --workers 3
  python3 "$W/reports/data/active-rule-1v2-2026-10-24/scripts/check_unchanged.py" \
    "$W/reports/data/active-rule-1v2-2026-10-24/runs/B_2d_h24.jsonl" "$CHK/h1_B_2019_148.jsonl" \
    "$CHK/integrity_h1.json" "H1 code 12cf308 vs R63 B, fire 2019_148"
  python3 -c "import json,sys; r=json.load(open('$CHK/integrity_h1.json')); \
sys.exit(0 if r['n_mismatches']==0 and r['n_common_fire_days']>=1 else 1)" || { echo "H1 integrity FAILED: void"; exit 1; }
fi

if [[ $STEP == 0b || $STEP == all ]]; then
  # H2 code integrity: this branch reproduces the B2 calibration ensemble rows of Alberta fire 2022_481
  one_fire_root "$CHK/h2_root" 2022_481
  vrun "$WT" "$CHK/h2_root" "$VD/backcast/roots/ab2025_backcast/domains" "$CHK" h2_ens_2022_481 \
    --name h2_ens_2022_481 $COMMON --active-days 2 --windows 8 17 --ensemble 20 --ens-seed 1 \
    --ens-params "$A15" --max-det-run-s 20 --det-run "$VD/skill/runs/active2_spin_bp1020.jsonl" --workers 3
  python3 "$WT/scripts/validation/sealed2026/check_ens_unchanged.py" \
    "$VD/b2-ensemble/backcast/runs/phys_a1.5_cal.jsonl" "$CHK/h2_ens_2022_481.jsonl" "$CHK/integrity_h2.json"
fi

if [[ $STEP == 1 || $STEP == all ]]; then
  # Build the sealed confirm inputs (opens the sealed list; checks its sha256 first)
  wait_mem build
  (cd "$WT" && PYTHONPATH="$WT/engine/src" CAPJOB_MEM=5G /home/rpas/.local/bin/capjob \
    python3 -W ignore scripts/validation/sealed2026/confirm_inputs.py confirm > "$ROOT/build.log" 2>&1)
  tail -2 "$ROOT/build.log"
fi

if [[ $STEP == 2 || $STEP == all ]]; then
  # H1: B (2 burn days) then A (1 burn day), H = 24 h, mature fire-days, R63 code
  for spec in "B_2d_h24:--active-days 2" "A_1d_h24:--active-days 1"; do
    name=${spec%%:*}; args=${spec#*:}
    # shellcheck disable=SC2086
    vrun "$WT1" "$ROOT" "$ROOT/domains" "$RUNS" "$name" --name "$name" $COMMON --start-types mature \
      --hold-hours 24 $args --windows 4 6 8 10 12 14 17 24 --workers 3
  done
fi

if [[ $STEP == 3 || $STEP == all ]]; then
  # H2: deterministic C1 on every selected fire-day (run time decides the ensemble set), then the ensemble
  vrun "$WT" "$ROOT" "$ROOT/domains" "$RUNS" det_C1 --name det_C1 $COMMON --active-days 2 --windows 8 17 \
    --workers 3
  vrun "$WT" "$ROOT" "$ROOT/domains" "$RUNS" ens20_A15 --name ens20_A15 $COMMON --active-days 2 \
    --windows 8 17 --ensemble 20 --ens-seed 1 --ens-params "$A15" --max-det-run-s "$CAP_S" \
    --det-run "$RUNS/det_C1.jsonl" --workers 3
fi

if [[ $STEP == 4 || $STEP == all ]]; then
  mkdir -p "$OUT"
  (cd "$WT" && PYTHONPATH="$WT/engine/src" python3 scripts/validation/sealed2026/score_confirm.py "$RUNS" "$OUT")
fi
echo "$(date -Is) step $STEP finished"
