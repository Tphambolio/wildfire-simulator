# PREREG_US_TRANSFER1: a US-transfer correction (FIX1) tested on fresh western-US fires ([R71])

- **Registered:** 2026-10-29, branch `analysis/us-transfer-r71` (from `master` `ee6ab09`), draft PR #115. This
  file, the held-out draw script, the input builder, the run driver and the scorer are committed and pushed
  **before the draw is run, and before any held-out fire's hotspots, weather, growth or model output is
  opened**. The draw result is appended in §11 after the run. The text above §11 is not edited after
  registration; check with `git diff <registration commit> HEAD -- docs/PREREG_US_TRANSFER1.md`, which may only
  add lines.
- **Owner approval:** Travis Kennedy, 2026-10-29, research thread "US transfer over-prediction" (R71): stage 1
  diagnosis, then a registered fix test. **Held-out scoring starts only when the main session says go** (§10).
- **Report:** [R71] `~/dev/wildfire/reports/US transfer over-prediction diagnosis 2026-10-29.md`; data
  `~/dev/wildfire/reports/data/us-transfer-r71-2026-10-29/`.

**Code (committed with this file; must not change before the Result section):**

| File | sha256 |
|---|---|
| `scripts/validation/us_transfer/common.py` | `b2ebdc2c3e33f2b5898172c5a0ad0e4d4d610d6363836820eadc2e17beddb3e3` |
| `scripts/validation/us_transfer/build_dev.py` (helpers reused by the builder) | `ca8629302077bc3bc9f023a63f98b6c304497b2e2ca85211e94cf5db9b12c157` |
| `scripts/validation/us_transfer/draw_heldout.py` | `9ba9f33055713e147b6c3b34c474d745121d7bbb414aaf183800669154705dd7` |
| `scripts/validation/us_transfer/heldout_inputs.py` | `b2101bd18d69b65db0dc6d6d73b6f92357eae6451af72ab0d3a2dffc94e2cbc4` |
| `scripts/validation/us_transfer/sens_run.py` (variants `base`, `fix1`, `ens_base`, `ens_fix1`) | `e8b48c148dba38c5f35bc63e817f271086e5c70652633928d0c34dae1971bca8` |
| `scripts/validation/us_transfer/score_heldout.py` | `04478023bdf5a66f6fe9e6e6647797dd593f6b087d88a64d138ca010f0f71382` |

## 0. What this test is

- **Why.** On the sealed 2026 US `confirm` fires FireSim over-spread by about 15 times in area ([R67]). The R71
  diagnosis, on open development fires only (the v8 *training* split, 102 US and 61 Canadian fires), found:
  - **(c) suppression / held fire dominates.** Late-stage US fire-days carry +0.63 of the +0.71 log10 US − Canada
    gap, the model moves 21 % of start edges that held, and 69 % of its growth falls outside the final footprint.
  - **(a) the shrub crosswalk (C-4) is second** (−0.23 when shrub → D-1/D-2).
  - **Weather inputs, curing, the scoring geometry and the day-of-burning source are not causes.**
- **What is tested.** One frozen correction for US (LANDFIRE-fuel) runs, sized on the development fires only.
- **What it is not.** FIX1 is a regional calibration for the transfer of FBP to suppressed western-US fires. It
  is not an engine change, it does not touch Canadian runs, and it is not a claim about fire physics. A PASS
  supports using FIX1 in FireSim's US validation runs (harness and scorecard) only.
- **Sealed sets.** Nothing here uses or opens any SEALED_2026 subset. The `confirm` fires are spent; the
  `scorecard` and supplementary `scale` blocks are untouched.

## 1. Hypothesis

**H.** On fresh western-US fire-days, FIX1 reduces the absolute 17 h log-area error of FireSim's deterministic
growth forecast relative to the R67 set-up, reduces the log-area bias, does not lower 17 h F1, improves the
20-member ensemble's P10-P90 coverage, **and does not add under-prediction on the largest observed growth days**.
Canadian runs are unchanged. H0: it does not.

**Why under-prediction is guarded.** FireSim is a preparedness tool: under-predicting a free-running fire is the
worse error. FIX1's rate factor is a suppression calibration, so the test must show it does not cut the big
runs (§7, guard U, deciding).

## 2. The candidate fix (frozen)

**FIX1**, applied only to domains with LANDFIRE FBFM40 fuels (US). Code: `sens_run.fix1`.

1. **Shrub crosswalk.** FBFM40 SH1-SH9 (codes 141-149) → FBP **D-1** (`cfs_national` code 13; the harness
   uses D-2 between day 150 and 258), instead of C-4. All other groups keep the v7/R67 crosswalk.
2. **Held-fire ROS factor.** On **held-fire** fire-days, every cell's rate of spread is multiplied by
   **k = 0.20** for the whole burn day. Held fire means both:
   - the start is **mature** (start age ≥ 2 days, the [R58] `start_type`);
   - **less than 5 %** of the start area burned on the previous day (`start_prev_day_share` < 0.05).

   Both are computed from day of burning before the burn day only, so they are known at forecast time.
   This uses the harness's ensemble `ros_multiplier` hook; members of the FIX1 ensemble get k × their own A15
   factor. All other fire-days: k = 1.

**How it was sized (development fires only; disclosed).**
- **Data:** combined dose runs (`shd1_rmNNN`) on 83 US development fire-days (one per fire, base run ≤ 150 s).
- **Rule families compared:** a persistence rule, a start-area threshold, a fire-age threshold, one factor for
  every day, a mature-only factor, and the held-fire factor.
- **Mature-only was rejected for safety.** It minimises MSE (k = 0.20: bias +0.85 → +0.08, mean |L|
  0.99 → 0.50), but on the top tercile of observed growth it raises the share of days with L < −0.5 from 0.14 to
  0.25 (mean L +0.31 → −0.23). That is the under-prediction the coordinator's review (2026-10-29) made deciding.
- **The held-fire rule keeps that share near the base** (0.18) and the top-tercile mean L positive (+0.10).
  Development result with the frozen `fix1` code (measured, 83 US fire-days):

  | Measure | Base | FIX1 |
  |---|---|---|
  | mean \|L\| | 0.99 | 0.62 (Δ −0.36 [−0.49, −0.25]) |
  | bias | +0.85 | +0.36 |
  | F1 | 0.201 | 0.229 (Δ +0.028 [+0.008, +0.050]) |

- **Thresholds:** 5 % (from 2, 5 and 10 %) and k = 0.20 (from 0.15, 0.20, 0.30) were chosen together for the
  lowest mean |L| with top-tercile under-prediction no worse than shrub-only.
- **Why not on Canadian fires:** the same factor on every day would cost Canadian F1 −0.066 [−0.103, −0.029].
- **This revision was made before registration.** The mature-only run files are kept, superseded, in
  `validation-data/us-transfer-r71/runs/superseded_fix1_mature_all/`.

**Product framing (registered).**
- **If H passes,** FIX1 may ship **opt-in only**, labelled as a *calibration for US fires under active
  suppression (mature, staffed, held edges)*.
- **It is never the default for a new or free-running US fire.**
- **Whether it becomes a default anywhere is Travis Kennedy's decision**, not this test's.
- **The Canadian product is unchanged.**

## 3. Held-out set (drawn by `draw_heldout.py`; attributes and geometry only)

**Pool:** MTBS burned-area boundaries (`validation-data/v8-pool/raw/mtbs/mtbs_perims_DD.shp`; public domain).
Rules, applied in this order:
1. `incid_type = 'Wildfire'`;
2. state (first two letters of `event_id`) in AZ, CA, CO, ID, MT, NV, NM, OR, UT, WA, WY;
3. ignition date in **2017**: LANDFIRE LF2016 (disturbances through 2016) is pre-fire, and the fires are older
   than every fire used by FireSim tests (v8 2018-2025, sealed 2026);
4. `burnbndac` ≥ 12,355.3 acres (5,000 ha, the v8 and sealed size class);
5. no `COMPLEX` in the name;
6. not a v8 split key and not in the SEALED_2026 lists;
7. **never used in the record:** `event_id` or `irwinid`, or "`<name> fire`" (word-bounded, any case), found in any
   text file under `~/dev/wildfire/reports/`, `~/dev/wildfire/validation-data/` (except `sealed2026/` and the
   draw's own folder) or the repository's `docs/`. This drops, for example, every 2017 fire already discussed;
8. boundaries overlapping by > 50 % of the smaller (EPSG:5070): keep the larger.

**Draw:**
- **Order:** sorted `MTBS:<event_id>` keys permuted with `numpy.random.default_rng(2026102911)`.
- **`test`:** orders 1-40 (all if fewer).
- **`reserve`:** the rest. It stays unopened and decides nothing.

**Output:** `~/dev/wildfire/validation-data/us-transfer-heldout/`. The seal is `heldout_us_2017.csv` with its
sha256; the draw prints counts only, never names. Result appended in §11.

## 4. Inputs (built blind by `heldout_inputs.py --go`, only after §10's go)

As the development fires (R71 §1), with these held-out sources:

| Input | Held-out source |
|---|---|
| Perimeter | MTBS boundary of the draw |
| Day of burning | VIIRS S-NPP 375 m, FIRMS yearly archive 2017 (NOAA-20 did not exist yet; disclosed difference from development, which also used NOAA-20). Burn day = date of (local solar time − 12 h), ignition − 1 d to ignition + 120 d, nearest detection inside the boundary + 375 m |
| Fuel | LANDFIRE LF2016 FBFM40 CONUS (LFPS, 30 m, nearest onto the grid) through `common.crosswalk(.., 'base')`, raw codes kept for FIX1 |
| Terrain | LF2020 Elev |
| Weather and daily codes | ERA5 hourly, as development |
| Grid | the R67 rule |
| Fire-days | `fire_day_pairs` defaults, at most 3 per fire (`random.Random(f"2026102912:{fid}")`) |

A fire with no detection inside its boundary, or no eligible fire-day, contributes nothing and is listed.

## 5. Runs (each under `capjob`, ≤ 5 GB, started only with ≥ 8 GB available and ≥ 10 GB disk free)

| Step | Command (`FIRESIM_R71_DATA=~/dev/wildfire/validation-data/us-transfer-heldout/build`) | What |
|---|---|---|
| 1 | `heldout_inputs --go` | build inputs (opens the `test` subset; sha256 of the list checked first) |
| 2 | `sens_run --variants base fix1 --workers 4` | deterministic base (R67 `det_C1` set-up) and FIX1 on every selected fire-day |
| 3 | `sens_run --variants ens_base ens_fix1 --max-base-s 120 --workers 4` | 20-member A15 ensembles (seed 1) on fire-days whose base run took ≤ 120 s (run time only, never outcome) |
| 4 | Canadian guard: `sens_run --variants fix1 --region CAN` on the R71 development root | FIX1 on every Canadian development fire-day that has a base run |
| 5 | `score_heldout <held-out runs> <development runs> <out>` | decision (§6-§7) |

- **Interruptions:** any interruption is resumed (the driver skips finished fire-days) and reported.
- **No re-runs:** no fire-day is re-run for any other reason.
- **Run-time cut:** fire-days whose base run errors are dropped and listed.

## 6. Metrics (17 h; L = log10(modelled growth + 10 ha) − log10(observed + 10 ha))

- **P1 (primary):** paired mean |L| (FIX1 − base), deterministic member.
- **P2:** FIX1's log-area bias, mean L_FIX1.
- **P3:** paired mean 17 h F1 (FIX1 − base), with NaN F1 counted as 0.
- **U (deciding):** under-prediction on the largest days. U = fire-days whose observed 17 h growth is in the top
  tercile of the held-out deterministic set (≥ its 66.7th percentile) **or** ≥ 2,000 ha. Measured:
  - the share of U days with L < −0.5 (modelled area under about a third of observed), base and FIX1;
  - the mean L on U;
  - the mean L on the L < −0.5 days.
- **P4:** ensemble P10-P90 coverage of the observed log10(area + 10 ha) by the 20 members' log areas: FIX1's
  coverage, and the paired gain FIX1 − base.
- **G (Canadian guard):** on every Canadian development fire-day, FIX1's 17 h modelled area equals the base's
  (FIX1 is US-only).
- **CIs:** 95 % percentile bootstrap over whole fires, 2,000 draws, `numpy.random.default_rng(1)`.

## 7. Pass rule (all required)

1. **P1:** the CI upper bound of Δ|L| is < **−0.10**.
2. **P2:** |mean L_FIX1| ≤ **0.50** (point).
3. **P3:** the CI lower bound of ΔF1 is ≥ **−0.02**.
4. **P4:** FIX1 coverage ≥ **0.60** (point), and the CI lower bound of the coverage gain is > 0.
5. **U (under-prediction guard, deciding).** Both must hold, or FIX1 **fails whatever |L| does**:
   - (U1) the FIX1 share of U days with L < −0.5 is ≤ the base share + **0.05** (points);
   - (U2) the mean L_FIX1 on U is ≥ **−0.25** (point).
6. **G:** no Canadian fire-day differs.

**Data rules:**
- **Minimum data:** P1-P3 need ≥ 15 fires and ≥ 30 fire-days; P4 needs ≥ 10 fires and ≥ 25 fire-days.
  Otherwise the verdict is **INCONCLUSIVE**, and the `test` subset is still spent.
- **Otherwise:** PASS if all six hold, else FAIL.
- **Multiplicity:** one joint decision, no separate claims.

**Secondary (descriptive, decide nothing):**
- FIX1 on early or free-running days: start age ≤ 3 days; previous-day share ≥ 0.30; non-mature starts; and
  little held edge (observed static share of the start perimeter < 0.9, an outcome-side descriptor);
- each FIX1 part alone: shrub only, and the factor only;
- the deterministic bias by start type;
- the share of growth outside the final footprint;
- CRPS of log area;
- the reserve subset is **not** used.

## 8. What was looked at before registration (disclosure)

- **Looked at:** all R71 development results (v8 training fires, US and Canada), including the dose-response and
  the FIX1 sizing; the R67 sealed `det_C1` run file, *described* only (bias +1.33); the R67 report.
- **Not looked at:** no 2017 fire's hotspots, weather, growth or model output.
- **The pool size was read** (118 MTBS records pass rules 1-4 before the exclusions) to choose the year. Nothing
  else about the pool was read.
- **Coordinator review (2026-10-29, before registration).** It asked for the deciding under-prediction guard U,
  the opt-in product framing and the early / free-running descriptives. The U margins (+0.05 share, −0.25 mean)
  were set against the development values of the mature-only rule (+0.11, −0.23, which would fail) and the
  held-fire rule (+0.04, +0.10). The fix was revised to the held-fire rule *before* this file was committed.
- **Development coverage was not available at registration.** The `ens_base` / `ens_fix1` runs on US
  development fire-days with base ≤ 40 s were still running (about 15 min per fire-day). They are reported in R71
  when done and **cannot change this file**.
- **The P4 floor (0.60) is set from earlier results:** the raw 20-member ensemble covered 0.36 on the R67 sealed
  fires, where the bias was +1.17. FIX1 removes much of the bias, so coverage should rise; the
  floor is a judgement, not a fitted value.

## 9. What follows from the result

- **PASS:** FIX1 is available **opt-in** for US fires under active suppression (the product framing in §2). The
  owner decides any default use (for example a US scorecard row labelled "US transfer calibration
  (PREREG_US_TRANSFER1)"). It is never the default for new or free-running fires. The Canadian product and
  harness are unchanged.
- **FAIL or INCONCLUSIVE:** FIX1 stays off. The diagnosis stands as a description. The next step is a held-edge
  (barrier) rule for mature US restarts rather than a rate factor.
- **The `test` fires are spent for deciding** after step 2.

## 10. Stopping rule

After this file and the draw result (§11) are pushed, **no held-out input is built and no held-out run is made
until the main session authorises it**. `heldout_inputs.py` refuses to run without `--go`.

## 11. Draw result (appended after the run; nothing above was edited)

(to be appended)
