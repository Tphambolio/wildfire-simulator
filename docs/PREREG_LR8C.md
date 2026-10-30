# Pre-registration: v8 ember counts and probabilities, per-region calibration (LR8C)

**Status:** frozen 2026-10-29, committed and pushed before any held-out fire is drawn, built or scored.
**Owner approval:** Travis Kennedy, 2026-10-29 (~02:10 MDT): approved every recommendation of [R70] §7. Those include
"a calibration study per region before any spot count or probability is shown".
**Branch:** `feat/long-range-embers-v8` (after [R70]).
**Planning data folder:** `reports/data/lr8-followups-prereg-2026-10-29/` (outside the repo). Its README, `calibration_planning.json` and
`v8_decision_intervals_planning.csv` hold the planning numbers. They come from the v8 decision intervals, which [R70] already
opened and scored.
**Gate this study controls:** FireSim shows **no** M8 spot count and **no** spot probability unless this test passes.
A FAIL keeps the ember feature as it is: ranking only, opt-in.

## 1. Question

On fires never used in v8, are M8's per-region recalibrated outputs good enough to show? The outputs are:

- (P) the probability that an interval holds at least one new spot fire 1-15 km out.
- (N) the expected number of such spots per fire.

The bar is calibration and skill against the observed VIIRS spot jumps.

## 2. Model under test (fixed here; nothing is fitted on held-out fires)

- **M8 exactly as decided in [R70]:** PREREG_LR8 §5 with addendum A2 β (β₀ −22.670, β_w 0, β_L −4.675, β_m −0.2441).
  Inputs, intervals, candidate cells and jump rule are those of PREREG_LR8 with addenda A1-A4. λ_i is the sum of the
  M8 score over the candidate cells of interval i.
- **Recalibration (statistical, two parameters per region; not ML):**
  - P_i = logistic(a_r + b_r ln λ_i).
  - The count is E_f = c_r Σ_i λ_i over the fire's valid intervals.
  - The parameters were fitted on the 5,174 v8 decision intervals, which are already opened. Logistic maximum
    likelihood gives a and b; c is the ratio of observed to predicted jumps.

| Region | a_r | b_r | c_r | climatology rate (interval with ≥ 1 jump) |
|---|---|---|---|---|
| CAN (western jurisdictions as v8) | −3.0771 | 0.04104 | 2.5170 | 0.03122 (72 of 2,306) |
| US (western states as v8) | −2.8280 | 0.15864 | 0.16105 | 0.02232 (64 of 2,868) |

An observed interval event is any cluster that passes the PREREG_LR8 §4 rule with its member within 375 m of the
final perimeter, or more than 540 m outside it ("never reached", A1). Clusters 375-540 m outside count as neither:
an interval whose only clusters are 375-540 m out is left out of P. N counts positives plus never-reached clusters,
which is the A1 count secondary.

## 3. Fires (held out, sealed)

- **Pool:** the v8 decision fires that were **not drawn** in v8. These are ranks 201-477 (Canada, 277 fires) and 201-303
  (US, 103 fires) of the sealed `decision_order.csv` (sha256 `73bae68c0da1528314dea91e5634e8bebfcf84e5e94b39e22ac49b61a8a0f6cb`;
  PREREG_LR8 §3).
  - None of these fires has had a hotspot attributed, a jump catalogued or a model run.
  - Input builds of ranks 201-300 (Canada) and 201-303 (US) were started blind, before v8 stopped, and some finished:
    station, fuel and curing data only (A3).
- **Eligibility:** as v8 (A2.1): an IEM station within 50 km with ≥ 50 % hourly coverage over the record window, plus
  pre-fire fuels. Ineligible fires are listed and skipped.
- **No sequential stop:** every fire of the pool is drawn, because calibration needs the zeros. The order is kept for
  the record.
- **Not decidable:** if, in a region, fewer than 40 interval events or fewer than 60 eligible fires are observed, that
  region is not decidable (no pass, no fail).

## 4. Metrics and pass rule (all required, in each region)

1. **Skill of P:** Brier skill score against the region's fixed climatology rate (§2):
   BSS = 1 − Brier(P)/Brier(rate). Pass if the **fire-bootstrap 95 % lower bound > 0** (2,000 draws, seed 2026102911).
2. **Calibration of P in the large:** O/E = (number of event intervals) / ΣP_i. Pass if the fire-bootstrap 95 % CI lies
   inside **[1/1.5, 1.5]**.
3. **Counts N:** O/E = Σ observed / Σ E_f over eligible fires. Pass if the fire-bootstrap 95 % CI lies inside **[1/2, 2]**.

The study passes only if 1-3 hold in both regions. A pass allows showing P and N for that region, labelled as calibrated
to VIIRS-detected spots ≥ 1 km out; any wider claim needs its own test. Reported, never deciding: the reliability
diagram (deciles of P), the calibration slope (logistic of event on logit P), and the fire-level Spearman of E_f
against observed counts.

## 5. Planning evidence and expected outcome (stated before any held-out data)

- On the v8 decision intervals, the fitted recalibration has in-sample BSS **+0.001 (CAN)** and **+0.010 (US)**.
  Interval-level discrimination, measured as the AUC of ln λ for "any jump", is only **0.54 (CAN)** and **0.65 (US)**.
  M8 ranks *where* spots land within an interval, but carries almost no information on *whether* one occurs.
- **Power:** resampling v8 decision fires (`calibration_planning.json`) gives:
  - Criterion 1: P(lower bound > 0) ≈ **0** at the expected sample size (about 87 eligible Canadian fires, 72 US).
  - Criterion 3 (×/÷ 2 margin): 0.51 (CAN) and 0 (US).
  - Criterion 2: 0.67 (CAN) and 0.05 (US).
- **Expected outcome: FAIL** (or not decidable in the US). The test is registered as the owner asked, so that showing
  counts or probabilities needs a pre-registered pass. A FAIL is the informative result. It means M8 counts and
  probabilities stay hidden, and the next step is a model change (e.g. a fire-level activity term), not a
  re-calibration.

## 6. Exclusions, compute, rules

- Interval and cell exclusions as PREREG_LR8 §8 with A1-A4. No cloud exclusion (A2.7).
- Open data only; nothing requested from any agency. No ML model is fitted (the logistic recalibration in §2 is fixed
  here). Heavy jobs via `capjob`, ≤ 5 GB each, started only with ≥ 8 GB RAM and ≥ 20 GB disk free.
- Locations of held-out jumps are sealed per fire with a sha256 manifest (as v8 A2.10). They are opened once, after M8
  has run on every drawn eligible fire.
- Changes after this freeze only as dated addenda, before any held-out score is seen.

## 7. Frozen values

Seeds: bootstrap `2026102911`. Files: `calibration_planning.json` (sha256 in the data-folder README), the v8
`decision_order.csv` above, and `scripts/validation/v8_score.py` `623233f6…d880` (ranks unchanged; not used for the
decision here).

---

## Addendum A1 (2026-10-29): draw implementation, before any held-out fire is opened

Nothing above was edited. This addendum changes no criterion, model or value.

- **Shared draw.** LR8C, LR8L and LR8S (PREREG_LR8S §2, frozen `e9c6e51`) share one draw of pools A and E:
  `scripts/validation/v8/heldout.py` (sha256 `9cb4677df56ef73cbc2363b3190ea85ef5ed7a6c4f5a21a8c1886d97de79d40f`).
- **Per fire, in sealed order, with no stop:** blind inputs, then VIIRS detections, then the A1 jump catalogue,
  then the registered `intervals.py` (`4303e544…4dbe`).
- **Counts are sealed too.** Each fire's whole jump table, its counts included, goes to a sealed file listed in a
  sha256 manifest. No count or location is written elsewhere or printed before `unseal`; LR8C scores counts, so
  this is stricter than v8, which showed per-block counts.
- **Eastern stations.** IEM ASOS networks CA_QC, CA_NF (Newfoundland and Labrador), CA_NS, CA_NB and CA_PE were
  added to the station lists on 2026-10-29. These are station metadata only; CA_ON was already present. No western
  fire has a station of these networks within 50 km.
- **Gate.** `unseal` and every score wait for the main session's go.

- **Hash update (2026-10-29, later the same evening).** `heldout.py` now runs fires in batches of 25 (one FIRMS pass per batch), as v8's `draw.py` did; the per-fire steps are unchanged. New sha256 `111bc43c3a52e0b45b46ec96d06100b3c34fc469b2109da68b70ec054bf7ca5f`, replacing `9cb4677df56ef73cbc2363b3190ea85ef5ed7a6c4f5a21a8c1886d97de79d40f`. The first 3 pool A fires (CAN ranks 201-203; one eligible) were processed by the earlier version. No sealed file was opened and nothing was scored.
