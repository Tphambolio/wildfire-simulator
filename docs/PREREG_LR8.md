# Pre-registration: long-range embers v8 (target L: where new spot fires start)

**Status:** frozen 2026-10-18, committed and pushed before any decision-fire run. The owner's two commit
conditions are met (§9): measured training-split rate 1.67 detected jumps per fire (≥ 0.25, OD9 not triggered);
power 0.89 at +0.08 AUC with K_min 150 and a cap of 250 fires per region.
**Branch:** `feat/long-range-embers-v8`, from `master` `fbcf3d6` (after PR #90).
**Owner approval:** Travis Kennedy, 2026-10-18 (~00:05 MDT, reconfirmed later the same day): every
recommended option OD1-OD12 of *v8 ember study design draft for owner review 2026-10-18* (§10, §10a), with
the condition that this file is committed only if the measured training-split jump rate is ≥ 0.25 per fire
and K_min 150 with the fire cap reaches ≥ 80 % power at +0.08 AUC.
**Design and pre-freeze evidence:** `reports/v8 ember study design draft for owner review 2026-10-18.md`
(§0-§13 design; §14 pre-freeze addendum) and `reports/data/v8-prefreeze-checks-2026-10-18/` (outside the repo).
**Analysis script:** `scripts/validation/v8_score.py` (this branch; sha256 in §11). Unit tests:
`engine/tests/validation/test_v8_score.py`.

Nothing in this file was changed after any decision-fire jump location, VIIRS hotspot attributed to a
decision fire, DINS outcome or model score was seen. None has been opened.

## 1. Question

**L (primary, decides).** Given the observed fire perimeter at the start of each interval and observed
weather during it, does the v8 ember model (M8) rank the locations of new VIIRS-detected spot fires
≥ 1 km beyond that perimeter better than the pre-specified baseline B1, on fires FireSim has never used?

The test is powered for a true gain of **Δ = +0.08 AUC** over B1 (B1 AUC ≈ 0.65) (OD1). Δ is what the test
is sized for, not a pass threshold.

## 2. Data (open only; nothing requested from any agency)

| Data | Use | Licence / terms |
|---|---|---|
| NASA FIRMS VIIRS 375 m active fire, S-NPP (VNP14IMG) and NOAA-20 (VJ114IMG), yearly country archives (Canada, United States), standard product (version 2) | Jumps; observed perimeters; active segments | NASA open data; FIRMS acknowledgement text and DOIs 10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002, 10.5067/FIRMS/VIIRS/VJ114IMGT_NRT.002; Schroeder et al. 2014 |
| NBAC, release 2026-05-13 | Canadian pool, final perimeters, record dates | OGL-Canada |
| MTBS burned-area boundaries (composite, retrieved 2026-10-18) | US pool 2018-2024, final perimeters | Public domain (USGS/USFS) |
| NIFC WFIGS Interagency Perimeters (retrieved 2026-10-18) | US pool 2025, final perimeters | Public domain |
| Station weather (ECCC / provincial open archives; RAWS via open archives) | Hourly wind, FFMC | Open, per source |
| Pre-fire fuels: FireSim pre-fire FBP method (#79) in Canada; LANDFIRE pre-fire vintage in the US | Emission, receptivity, B1 | OGL / public domain |
| CAL FIRE DINS | Building arm only (never decides) | CC BY |

NOAA-21 (VJ214IMG) is **not used**: it is not in the keyless yearly archive, and using it only from 2024
would change the detection rate within the era. All fires, training and decision, use S-NPP + NOAA-20.

## 3. Fires

- **Pool** (OD2, OD3): final size ≥ 5,000 ha, not prescribed, 2018-2025.
  - Canada: NBAC GIDs (rows of one GID summed) whose largest row is AB, BC, SK, MB, NT or YT: **721 fires**.
    Parks Canada (PC) and DND rows are federal and are not in the pool.
  - Western US (AZ, CA, CO, ID, MT, NV, NM, OR, UT, WA, WY): MTBS `Wildfire` events 2018-2024
    (`burnbndac` ≥ 12,355.3 acres) and WFIGS `WF` perimeters 2025 (≥ 12,355.3 acres, one per IRWIN ID):
    **467 fires**. Alaska is not in the pool.
- **Exclusions** (frozen list `exclusions.csv`, sha256 `4160eea3c68d4a9844ea6d7cf6ee6789c5d700ad694faf8b82a0c6e57b4eb8e4`):
  - never decide, may train (forced training): the AB validation-harness fires of 2018-2025; Camp, Woolsey,
    Eaton, Palisades; Carr, Kincade, Zogg (OD11);
  - neither train nor decide: Glass, CZU, North Complex, Dixie, Caldor (disclosed checks); Park 2024
    (building arm); Jasper 2024.
- **Split** (OD4): strata region × year; within a stratum the fire keys are sorted, permuted with
  `numpy.random.default_rng(S_SPLIT + stratum_index)`, and the first round(n/3) go to training.
  `S_SPLIT = 2026101801`. Result: training 238 CAN + 151 US (+ 13 legacy), **decision 477 CAN + 303 US**.
  `split.csv` sha256 `dee6eaac66cf6ddccc89e5992009904e74dc099aaac08402d3a6f7a72163b5c1`.
- **Decision order:** per region, decision keys sorted and permuted with `default_rng(S_ORDER + r)`,
  r = 0 CAN, 1 US; `S_ORDER = 2026101802`. `decision_order.csv` sha256
  `73bae68c0da1528314dea91e5634e8bebfcf84e5e94b39e22ac49b61a8a0f6cb`.
- **Eligibility, checked blind to hotspots, in order of draw:** an hourly station within 50 km for the burning
  window; pre-fire fuels available. A fire with fewer than two valid intervals (§4) contributes no jumps.
  Ineligible fires are skipped, listed and do not count toward the cap.
- **Sequential draw** (OD1): blocks of 25 fires per region in decision order. After each block the jump
  catalogue (§4) writes **only per-fire jump counts** to `counts.csv`; locations go to a sealed file
  (sha256 recorded) that is not opened until every model has run on every drawn fire.
  - **Stop** when the total is **≥ K_min = 150** and **each region holds ≥ 60**. Until then both regions
    keep drawing blocks, except that a region already holding ≥ 60 stops once the total is ≥ 150.
  - **Cap: 250 fires per region** (drawn, eligible). A region at its cap stops drawing.
  - If the caps are reached first: **not decidable** (§8).

## 4. Jump rule (frozen; as run on the training split, `v8_viirs_jumps.py`)

- **Detections:** `type == 0` (FIRMS static, industrial and offshore sources removed); S-NPP + NOAA-20.
  Jump members must be confidence `n` or `h` with **both pixel dimensions ≤ 550 m** (`scan` ≤ 0.55 km and
  `track` ≤ 0.55 km; along-scan size alone is not monotonic in scan angle, Schroeder et al. 2014 p.86).
  Perimeters use every detection.
- **Fire detections and window:** inside the final perimeter buffered 2 km; from the record start − 2 d
  (NBAC earliest HS/AG start; MTBS ignition date; WFIGS discovery) to the earlier of record end + 2 d
  (when recorded) and the last in-perimeter detection before a ≥ 21-day gap.
- **Epochs and intervals:** acquisitions merged when < 60 min apart (one epoch). Interval i runs from the
  first acquisition of epoch i−1 to that of epoch i. **Valid intervals are ≤ 12 h** (design value kept; 14 h and 24 h reported as sensitivities).
- **Start perimeter at epoch i:** every earlier detection dilated by 375 m, then a 1 km morphological
  closing (alpha-shape surrogate), on a per-fire 125 m grid (250 m above 4 M cells). Progression perimeters
  are not added: CFSDS ends in 2024 and FEDS is not available offline, so they could not be applied alike to
  every fire; both are VIIRS-derived. (Change from design §8.4, disclosed.)
- **Active segments** (M8 sources, B1 distance; OD12): start-perimeter edge cells with a nominal/high detection
  within 1 km acquired in the 12 h before t_start. All other edge cells are held and emit nothing.
- **Counted band:** a jump counts only if it lies 1-15 km from the start perimeter (the candidate band, §6);
  jumps beyond 15 km are reported, never counted or scored.
- **Jump:** epoch-i detections outside the start perimeter are clustered by single linkage at 750 m. A cluster
  is a jump if: its nearest qualifying member is **≥ 1,000 m** from the start perimeter (2,000 m reported);
  no other epoch-i detection outside the perimeter lies within 750 m of the straight line from that member to
  the nearest perimeter cell; that member is inside the final perimeter (375 m tolerance); and it is ≥ 2 km
  from the start point of any other recorded fire with a start date within [t_start − 1 d, t_end + 1 d]
  (NBAC representative points, MTBS centroids, WFIGS initial points). Each jump counts once, at the 375 m
  candidate cell of its nearest qualifying member.
- **Gap class (reported):** the mid-point of the jump-to-perimeter line is `unburned` (outside the final
  perimeter), `later_detected` or `never_detected` (burned but never detected: possible undetected front run).

## 5. Models (same perimeters, weather and candidate cells)

- **M8:** sources only on active segments; intensity = FBP at the observed spread rate over the previous
  interval (OD8; FRP-based emission as a sensitivity); two-mode kernel with the design §4 bounds (short mode:
  Sardoy correlation at I_eff = min(I, 40 MW/m), U_eff = clip(U, 6.70, 17.88 m/s); long mode: lognormal median
  733 m, σ_ln 0.99, cut 15.6 km; crosswind σ_Y as v7); ember mass 0.05 g, mass yield 27 g/kg; establishment
  logit P_ign = β₀ + β_m (m_ff − 21 %) + β_w ln(1 + U_bed) + β_L [long mode] + κ_g, with β_m fixed from the
  Beverly & Wotton grass curve, κ_g fixed, and β₀, β_w ≥ 0, β_L ≤ 0 fitted on the training split only by
  penalised Poisson likelihood (if the training split holds < 100 jumps, only β₀ is fitted). Score = expected
  established first-generation spots per candidate cell over the interval.
- **B1 (no fitting):** Beverly et al. 2021 hazard share within 500 m (C and M types in Canada; FBFM40 timber and
  shrub groups in the US) × exp(−(d − 1 km)/1.24 km), d to the nearest active segment, × 1 within ±45° of the
  interval's mean downwind direction, else 0. B1 from the whole perimeter, B1a and B1b are reported.
- **C-ELMFIRE (never decides; OD10):** black box, image `elmfire-bench:cbf924a`, guide-documented keywords only:
  `GENERATION_MODEL='PER-MW'`, `SPOTTING_DISTANCE_MODEL='EMPIRICAL'`, `ACCUMULATION_MODEL='EULERIAN'`,
  `IGNITION_MODEL='PHYSICAL'`, `DIFF_WILDLAND_IGNITION=.TRUE.`, 90 m, `phi` from the same start perimeter, held
  segments non-burnable for the interval; no tuning. Score = cumulative ember flux aggregated to the 375 m cells.
  Run on a **seeded subset of 150 decision fires** (`default_rng(2026101805)` permutation of the drawn
  decision fires, first 150 in that order; all if fewer).
- **Shared inputs:** per-zone winds on fires whose box spans > 30 km (nearest station within the drainage basin);
  grass curing from summer NDVI.

## 6. Metrics

- **Candidate cells:** burnable 375 m cells with centres 1-15 km from the start perimeter, unburned at t_start,
  inside the run box.
- **Rank:** within each fire-interval, the percentile rank of a jump cell's score among all candidate cells,
  ties counted half (Mann-Whitney).
- **Per fire:** a_f = mean over the fire's jumps of (r_M8 − r_B1); AUC_f(model) = mean r.
- **S3** = Σ w_f a_f / Σ w_f, w_f = K_f / (1 + 0.3 (K_f − 1)); region AUC uses the same weights.
- **Secondary (reported):** S1, S2; Brier of P(any jump); CRPS of jump distance; direction error; tail ratio
  P(> 3 km)/P(> 1 km) against 0.20; predicted vs observed jumps per fire; by region, year, terciles of the
  active share of the start perimeter, fuel group, gap class and perimeter-agreement flag; active-length ratio
  and M8 emission per km of active edge.

## 7. Pass rule (all four required)

1. **K ≥ 150**, each region ≥ 60 (else **not decidable**).
2. **Transfer gate G1** (design §3.3, computed from inputs only before scoring) passed on ≥ 90 % of decision
   fires; otherwise v8 **fails** (establishment did not transfer). G1a: ≤ 100 predicted established spots
   ≥ 1 km per 12 h interval in ≥ 95 % of a fire's intervals; G1b: per-active-hour mean within ×/÷ 10 of the
   training 5th-95th percentile range.
3. **S3(M8 − B1) > 0** by a one-sided fire-level **sign-flip randomisation test**, 20,000 flips,
   `S_TEST = 2026101803`, p < 0.025.
4. **Region AUC(M8) ≥ 0.60** in each region (S3-weighted point estimate).

Reported with the decision: stratified fire bootstrap 95 % CI of S3 (2,000 draws, `S_BOOT = 2026101804`)
overall, by region and per fire. ELMFIRE comparisons (ΔAUC M8 − ELMFIRE, ELMFIRE − B1, bootstrap CIs) are
reported, never deciding; no M8 setting may change after they are seen.

## 8. Exclusions and stopping rules

- **Interval:** no valid start perimeter; longer than the valid maximum (§4); > 50 % of the 1-15 km band
  under cloud in the end overpass; a model failed (M8 or B1 failure drops the interval for both; ELMFIRE
  failures drop it for ELMFIRE only; M8 failing on > 10 % of intervals fails v8).
- **Candidate cell:** non-fuel or water; burned before t_start; outside the box.
- **Only look before the final analysis:** per-block jump counts. Sealed locations and all scores are opened
  once, after M8, B1 and ELMFIRE have run on every drawn fire.
- **Not decidable:** caps reached before K_min. Reported descriptively; no pass, no fail, no lowered K_min.
- **Engine changes after the freeze** only as dated addenda with equivalence evidence, never after any decision
  score is seen. No configuration is promoted or re-tuned after scoring.
- **Compute:** jobs via `capjob`, ≤ 5 GB each, launched only with ≥ 8 GB free; ELMFIRE under
  `docker run --memory 5g`.

### 8a. Rules added from the Dixie diagnostic review ([R53] §6.4; external review, 2026-10-18)

Added before the freeze, adapted to the spot-jump target L (and applied as written to the tertiary arrival
target A):

1. **Left-censored targets excluded.** L: a jump must lie outside the start perimeter (§4), so no positive can
   be left-censored; candidate cells burned before t_start are excluded (§6); the first epoch of a fire (no
   start perimeter) is never scored. A: blocks with t_hi ≤ 0 or inside the start perimeter are excluded.
2. **Outside the final perimeter = never reached, not a positive.** L: a jump counts only if its member lies
   within 375 m of the final perimeter (§4); clusters ≥ 1 km out whose member is > 540 m outside the final
   perimeter are coded "never reached": they enter the count and Brier secondaries only, never the ranks.
   Members 375-540 m outside are neither positives nor negatives (counted). A: blocks > 540 m outside the final
   perimeter are right-censored "never reached" (Brier/count only).
3. **No mixed-source intervals; minimum width; agreement flag.** L: every interval is bounded by two VIIRS
   epochs (one source); epochs merge acquisitions < 60 min apart, so every interval is ≥ 1 h (≥ 0.5 h holds).
   No GOFER or other perimeter time is paired with a VIIRS time. The perimeter-agreement flag of the design
   (§9 implication 5) is reported. A: a GOFER t_lo is never paired with a later VIIRS-only t_hi; intervals must
   be ≥ 0.5 h wide; a GOFER/VIIRS agreement flag is reported, with scores on blocks where the two agree within
   6 h as a sensitivity.
4. **Compute guard (D4).** The v8 runners (`v8_run_m8.py`, `v8_run_b1.py`, `v8_run_elmfire.py`) enforce ≤ 5 GB
   per process (`resource.setrlimit(RLIMIT_AS)` in Python plus `capjob`; ELMFIRE `docker run --memory 5g`).
   Domains whose estimated peak exceeds 5 GB (Tubbs-class or larger) are split into tiles that overlap by
   15 km (the candidate band), each run separately. Peak RSS (`resource.getrusage`, docker stats for ELMFIRE)
   is recorded for every run in `runs.csv`; a run that hits the guard is a model failure (§8).
5. **ELMFIRE mode pinned.** Eulerian accumulation is the pinned comparator mode (§5). The particle-mode crash
   signature (`ERFINV_LOCAL(X) domain is |X| < 1. Received: -1`) and the seeds tried (default 2024; 7) are
   recorded in the comparator README (`reports/data/elmfire-black-box-comparator-2026-10-17/README.md`).
   Every ELMFIRE run log is checked for that error string; a hit is a run failure.

## 9. Measured before the freeze (training split only)

All numbers from the training third only (389 open training fires; legacy fires reported separately).
Data: `reports/data/v8-prefreeze-checks-2026-10-18/` (outside the repo; Claude for Science FireSim project).

| Quantity | Value |
|---|---|
| Detected jumps per fire, frozen rule (1-15 km, ≤ 12 h intervals) | **μ̂ = 1.67** (fire bootstrap 95 % 1.14-2.29); NB dispersion k̂ = 0.21; 61 % of fires none; top 10 % of fires hold 75 % of jumps |
| By region | Canada 2.34 (1.51-3.32), k̂ 0.21, n = 238; US 0.61 (0.41-0.83), k̂ 0.39, n = 151 |
| Sensitivities (all regions) | excluding never-detected gaps 1.12; ≥ 2 km 0.56; intervals ≤ 14 h 2.88; ≤ 24 h 3.42; no separate-start mask 1.67 |
| Gap class of counted jumps | unburned 31 %, later detected 36 %, never detected 33 % (possible undetected front runs) |
| Jump distance | median 1.5 km; 90th percentile 5.0 km; P(> 3 km)/P(> 1 km) = 0.17; 22 otherwise-qualifying jumps lay beyond 15 km |
| Clusters removed | line check 1,511; outside the final perimeter 147 (38 of them > 540 m: "never reached"); separate start 4; interval > 12 h 1,579 |
| Valid intervals | median 16 per fire; 94 % of fires have ≥ 2; the 12 h limit keeps 75 % (Canada) and 56 % (US) of the ≤ 14 h intervals |
| **OD9** | **not triggered** (1.67 ≥ 0.25; also not triggered by the strict rule, 1.12, or the US alone, 0.61) |

**Power** (`power_measured.py`; 2,000 simulated studies, sign-flip test with 2,000 flips, region floor 0.60,
per-region measured μ̂ and k̂, B1 AUC 0.65, heterogeneity as the design draft):

| Setting | Power at Δ = +0.08 | P(not decidable) | Median fires drawn (both regions) |
|---|---|---|---|
| **Frozen: K_min 150, ≥ 60 per region, cap 250 per region** | **0.89** | 0.00 | 175 |
| Same, μ at the lower 95 % bounds (Canada 1.51, US 0.41) | 0.92 | 0.00 | 250 |
| Same, strict rule rates (Canada 1.63, US 0.30) | 0.86 | 0.06 | 300 |
| Same, pooled k̂ | 0.85 | 0.00 | 175 |
| Same, M8-B1 agreement at jumps ρ_s 0.3 (base 0.6) | 0.70 | 0.00 | 175 |
| False-pass rate at Δ = 0 | 0.020 | | |
| Reported: Δ = +0.05, K_min 400 (≥ 150 per region), cap 400 / 303 | 0.82 | 0.05 | 400 |

The US region sets the length of the draw (≈ 100-125 US fires to reach 60 jumps). The cap (250 per region) is
within the US decision pool (303).

**Compute** (`results/compute_estimate.json`): an M8-style scoring pass (all array steps of §5, placeholder
physics) took 0.18 s per 12 h interval on Camp and 0.82 s on the largest training fire (NBAC 2024_160, 327
intervals, 4.2 min); about 2 CPU-hours of core work for 200 decision fires, plus weather and fuel preparation.
One ELMFIRE Eulerian 12 h interval on Camp (90 m, 501 × 466 cells, pinned deck) took 28 s wall and 273 MiB.
Scaled by grid cells and valid intervals of the training fires (linear scaling assumed [H]): 0.9 h per fire on
average (median 0.18 h; Canada 1.3 h, US 0.23 h; the largest fire ≈ 36 h, to be tiled under §8a.4), so the
150-fire ELMFIRE subset needs ≈ 80-135 CPU-hours (≈ 1-2 days at three concurrent runs).

**Pre-freeze unit tests:** G3 kernel bounds (`engine/tests/validation/test_v8_gate.py`: clamped median max
137.7 m ≤ 138 m, 99.9th percentile ≤ 4.2 km, Sardoy Fig. 9 case μ 4.15, σ 1.08); rank/ties against
`sklearn.metrics.roc_auc_score`, sign-flip null calibration and the decision logic
(`engine/tests/validation/test_v8_score.py`); jump-rule geometry on synthetic detections
(`test_jump_rule.py` in the data folder). All pass.

**Not yet done (done on training fires before any decision run, as dated addenda to this file):** the β fit
(§5) and the G1 training ranges; the active-segment rule test (observed static share vs held share); the
station-to-zone wind assignment and NDVI curing source. None of these can change §3, §4, §6 or §7.

## 10. Disclosures

- Known before the freeze: the design review and its §5.9 items; v7's Dixie and Caldor results; the NBAC,
  MTBS and WFIGS pool counts (attributes only); the power simulations; the Dixie early-arrival diagnostic
  (post-hoc, on burned fires); the training-split jump catalogue (training fires only) and its rate; one
  M8-style and one ELMFIRE timing run on Camp (a legacy training fire).
- The FIRMS yearly files were streamed in full; rows outside training-fire buffers (including any decision-fire
  rows) were discarded chunk by chunk and never written or examined.
- Not opened: any decision-fire hotspot attribution, jump location, DINS outcome or model output.
- Free values of the jump rule (§4) were set before the training catalogue was run and were not changed after
  seeing it, except the valid-interval maximum, which is chosen in §9 from the training interval lengths only.

## 11. Frozen files

Data-folder files (`reports/data/v8-prefreeze-checks-2026-10-18/`):

| File | sha256 |
|---|---|
| `scripts/v8_pool.py` | `89c82cabc576b8d17478f5f5040ca29bcd45e6ad28e90f049f54a0fee0adf4f0` |
| `scripts/v8_viirs_jumps.py` (jump rule, §4) | `d03339573088222fce1607a31b8f71507cd2428b38e1b636b8d0465b321c1581` |
| `scripts/jump_rate_summary.py` | `a2aa23f012aaee4dee63601a4cbcb3d7405d42bc8a7bed1fbc077a10e0b7815b` |
| `scripts/power_measured.py` | `854ab4b5928bc046cf3cd02995ceb60f590c0a8fd4db59c12139a3f4e5bd8255` |
| `scripts/test_jump_rule.py` | `079e3aa79ffa1b4a934ebdab3772d93616d8254dc4e1eb02d6e318cdca663bca` |
| `results/exclusions.csv` | `4160eea3c68d4a9844ea6d7cf6ee6789c5d700ad694faf8b82a0c6e57b4eb8e4` |
| `results/split.csv` | `dee6eaac66cf6ddccc89e5992009904e74dc099aaac08402d3a6f7a72163b5c1` |
| `results/decision_order.csv` | `73bae68c0da1528314dea91e5634e8bebfcf84e5e94b39e22ac49b61a8a0f6cb` |
| `results/jumps_training.csv` | `5da778ae194c09d3d6d05f03cd5c15b3e8334786c159f846453bfbc7b23ff54e` |
| `results/fires_training.csv` | `5749f1f36eab6817e53f954e443ac5bea08e1ca2412d047823b1fe8bb42620a3` |

Repo files (this commit): `scripts/validation/v8_score.py`
`d0f61e31ce47bce8da5d42ce4c6dc9acf5daa88f4b8115a9471aa45845eb55e6`; `scripts/validation/v8_gate.py`
`a9c251a76900b73adef3574e2accdfc2fd9f9f7f9e31352522e3e55c3e8cb3a5`.

Seeds: `S_SPLIT 2026101801`, `S_ORDER 2026101802`, `S_TEST 2026101803`, `S_BOOT 2026101804`,
ELMFIRE subset `2026101805`.

---

## 12. Addendum A1 (2026-10-24): owner decisions on the [R61] rule check

**Dated:** 2026-10-24. **Owner approval:** Travis Kennedy, 2026-10-24 ("go ahead as recommended" on the five
points of [R61] §5). **Status of everything above:** §1-§11 are the text frozen at `6452c1d` (2026-10-18), byte for
byte (sha256 of that file `b0f6662d84382a6c67dbc6a42e51b138d625d7e05b0de67c31c5be5257d04f43`); nothing above this
line was edited. Where this addendum and the text above differ, this addendum governs.
**Source:** [R61] *v7 held-out arrivals recoded with [R53] rules 2026-10-24* §5 and its fixtures
(`reports/data/v7-arrivals-recoded-2026-10-24/scripts/v8_rule_fixtures.py`, `results/v8_rule_check.json`);
[R53] *Dixie early-arrival diagnostic 2026-10-18* §6.4.

### A1.1 Single-source rule restored to the [R53] wording (replaces the target-A sentence of §8a.3)

For any arrival scoring, the sentence of §8a.3 "a GOFER `t_lo` is never paired with a later VIIRS-only `t_hi`" is
replaced by [R53] §6.4 as written: **a GOFER (or other perimeter) `t_lo` is never paired with a later VIIRS-only
`t_hi` when no perimeter contains the block; in that case `t_lo` is the previous VIIRS overpass with a
detection-capable view (night passes preferred).** A perimeter `t_lo` stays valid when that perimeter source
later contains the block. Reason: the broader wording dropped valid lower bounds and hollowed out scores
(CZU V7 MAE 2.44 → 0.28 h; [R61] §4). The spot-jump target L is unaffected: its intervals are bounded by two
VIIRS epochs and read no perimeter product (§4, §8a.3).

### A1.2 Target A is not scored in v8

The tertiary arrival target A (named only in §8a) **is not scored in v8**: no metric, unit, source or pass
condition was registered for it and no scorer exists on this branch ([R61] §5). Arrival-time work moves to the
front/restart programme (backlog A1, [R62]). **Target L (spot-jump locations, §1, §6, §7) is the only deciding
target.** A1.1 is kept so that any later arrival scoring starts from the corrected rule.

### A1.3 Never-reached guard in the deciding script (§8a.2 made executable)

`scripts/validation/v8_score.py` now applies §8a.2 itself instead of relying on the step that writes `jumps.csv`:

- `jumps.csv` carries `out_final_m` (distance of the cluster's nearest qualifying member outside the final
  perimeter, 0 when within the 375 m tolerance), and holds every cluster that passes the §4 rule except the
  final-perimeter condition. A `jumps.csv` without `out_final_m` is refused.
- Members ≤ 375 m outside are the positives and are ranked; members **> 540 m outside are "never reached"** and
  members **375-540 m outside are neither positive nor negative**: neither is ever ranked.
- Every row is counted, per fire and in total, in the output `jump_accounting`: ranked; never reached; 375-540 m;
  interval excluded (no candidate file, formerly skipped silently at `v8_score.py:96`); cell not a candidate
  (formerly skipped silently at `:99`). Never-reached clusters enter the count secondary (`count_secondary` =
  positives in scored or unscored intervals + never reached) and the Brier secondary (§6) as observed jumps;
  375-540 m clusters enter neither.
- The ranks, a_f, S3, the sign-flip test, the bootstrap, the region floor and the decision logic are unchanged.
  The K_min stopping count (§3) remains the count of positives (members within 375 m).

### A1.4 Perimeter-agreement flag struck (§6 and §8a.3)

The "perimeter-agreement flag" breakdown in §6 (secondary, "… gap class and perimeter-agreement flag") and the
L-sentence "The perimeter-agreement flag of the design (§9 implication 5) is reported" of §8a.3 are **struck**:
no perimeter source applies equally to Canada and the US (CFSDS ends in 2024; FEDS is not available offline; GOFER
covers California only), and §4 already reads no perimeter product. The gap-class breakdown stays.

### A1.5 Code aligned to the registered text

1. **Epoch merging (§4: "acquisitions merged when < 60 min apart").** The frozen `v8_viirs_jumps.py:234` started a
   new epoch only when `diff > 60 min`, so it merged acquisitions exactly 60 min apart. The amended rule starts a
   new epoch at `diff ≥ 60 min` (function `new_epoch`, shared by the runners). Every interval is still ≥ 1 h.
2. **Rule-2 counts (§8a.2: never-reached clusters "enter the count").** The frozen `jump_rate_summary.py:59`
   dropped both outside classes from every K. The amended script adds the rule `count_secondary` (primary rule
   without the final-perimeter condition, keeping members ≤ 375 m and > 540 m outside) and the per-fire columns
   `K_never_reached` and `K_out_375_540`; the primary (ranked) rule and every sensitivity are unchanged.

The amended jump-rule scripts are committed in the repository (`scripts/validation/v8_rule/`); the frozen
originals in the pre-freeze data folder are kept unchanged (sha256 in §11) and are no longer used by any run.

### A1.6 Effect on the training split (training fires only; no decision fire processed)

The amended catalogue was re-run on the 402 training fires (`validation-data/v8-pool/rule_A1/`):
no training fire has two acquisitions exactly 60 min apart (S-NPP and NOAA-20 pass about 50 min apart), so the
epoch change moves nothing: epochs, intervals and every catalogue row are identical (`jumps_training.csv` sha256
`5da778ae194c09d3d6d05f03cd5c15b3e8334786c159f846453bfbc7b23ff54e`, the same as §11). The ranked rate is unchanged
at **1.67 jumps per fire** (650 jumps, 389 open training fires; Canada 2.34, US 0.61; strict 1.12). The new count
rule gives **1.77 per fire** (687: 650 positives + 37 never reached); 4 clusters lie 375-540 m outside (neither).
§9's power and K_min are therefore unchanged.

### A1.7 Tests ([R61] fixture cases)

`engine/tests/validation/test_v8_addendum_a1.py` (never-reached guard: the [R61] R2-L case, where the frozen
script ranked the 700 m member, now ranks only the inside member; class boundaries at 375 / 540 m; rows skipped
for an excluded interval or a non-candidate cell are counted; a `jumps.csv` without `out_final_m` is refused) and
`engine/tests/validation/test_v8_addendum_a1_rule.py` (the [R61] R3-L case: acquisitions 59 / 60 / 61 min apart
give 2 / 3 / 3 epochs, the frozen script gave 2 / 2 / 3; the [R61] R2-L geometry gives primary K 1,
`count_secondary` 2, one 375-540 m cluster). `python3 scripts/validation/v8_score.py selftest` adds the same
guard case. All pass.

### A1.8 Scripts (sha256 at this addendum)

| File | sha256 | Replaces |
|---|---|---|
| `scripts/validation/v8_score.py` | `623233f687eb97d83a5cd3d81806accb28f78a6ef699f941249247c38ebad880` | `d0f61e31…55e6` (§11) |
| `scripts/validation/v8_rule/v8_viirs_jumps.py` | `d480ca5abbda67a81cd86be5a41d9dfb3fbe55adbdad1e9a927678ab1fa433e6` | `d0333957…1581` (§11, data folder) |
| `scripts/validation/v8_rule/jump_rate_summary.py` | `131f052e615277f7d0187de52e61998bca6e09b6d920ad1c12aea2c12dc874d7` | `a2aa23f0…815b` (§11, data folder) |
| `scripts/validation/v8_gate.py` | `a9c251a76900b73adef3574e2accdfc2fd9f9f7f9e31352522e3e55c3e8cb3a5` | unchanged |

Split, decision order, exclusions and seeds are unchanged (§3, §11).

### A1.9 Blindness at the time of this addendum

At the time of this addendum **no decision-fire jump, VIIRS hotspot attributed to a decision fire, DINS outcome or
model score had been opened**. Checked on 2026-10-24 before committing: the v8 data directory
(`validation-data/v8-pool/`) holds no `viirs_decision/`, `draw/` or `final/` directory; every per-fire directory
under `fires/` and every row of `runs.csv` (2026-10-18 18:08-20:23 UTC: inputs, intervals, two ELMFIRE timing
runs on a training fire) is a training or legacy-training fire of `split.csv` (three rows and three directories
carry two training keys run together by a shell-quoting slip; both halves are training fires); the FIRMS yearly
files show access times of 2026-10-18 00:10 MDT on a `relatime` mount, so none was read after 2026-10-19 00:10,
and their only readers were the training extractions that discard non-training rows chunk by chunk (§10); [R61]
read only the v8 scripts and synthetic fixtures. No model has been run on any decision fire.
