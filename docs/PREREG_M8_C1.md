# Pre-registration (draft): M8-C1, a short-range landing decay from the active edge for FireSim's ember model [R76]

**Status:** **draft for the owner's approval, 2026-10-30 (about 08:30 MDT).** Written by the professor session after the
stage-2 result [R68] (PREREG_ML_EMBER §12) and the *physics translation memo 2026-10-30* (change C1). Nothing is
frozen until the owner approves §9 and the implementation of §4 exists with its hashes in §8. No held-out fire is
opened by this draft; the decay shape (§3) comes from the training split only.
**Owner of the code:** the v8 runner and the M8 engine port (#118) are the FireSim main session's; this test is to be
implemented there or on a branch agreed with it. The held-out block (§5) must be agreed with that session, which draws
from the same sealed pools (one claims registry).
**Constrained by:** PREREG_LR8 (target, candidate cells, jump rule, metric, test, draw order, stop rule), **PREREG_LR8S [R73] /
LR8C / LR8L on `feat/lr8-next`** (the sealed pools A and E and their one-shot unseal, which this test shares, §5) and
`docs/SEALED_2026.md`. **Record:** `docs/ML_EMBER_RECORD.md`. **Report number:** [R76], claimed 2026-10-30.

---

## 1. Hypothesis and question

[R68] showed that a one-coefficient decay in distance from the nearest **active** perimeter segment ranks the next
spot-fire cell at AUC 0.902 on 73 unseen fires, against 0.672 for M8, and that nothing else transfers (RQ1b fail).
**H-C1:** M8's within-interval ranking is limited by its landing-density field, not by its establishment physics; a
landing density that decays from the active edge as the training data show, with M8's establishment moisture factor
kept, ranks spot-fire cells better than M8 on fires neither model has seen.

Deciding question: **S3(M8-C1 − M8) > 0** on the held-out block of §5, one-sided fire-level sign-flip test, p < 0.025.

## 2. The model M8-C1

For a candidate cell c in an interval, with d = distance (km) from c to the nearest active-segment cell at t_start
(v8's `d_act`) and m_ff the interval's fine-fuel moisture at the cell's zone:

    score_C1(c) = exp( f(d) ) · p_e(m_ff)

- **f(d)** is the frozen short-range decay of §3: the log relative likelihood of a counted jump at distance d, a
  piecewise-linear function of d in km, non-increasing, with a floor beyond 8 km (the long-range floor is what the
  data show; it is also where M8's long mode keeps skill, [R68] §4).
- **p_e(m_ff)** is M8's establishment moisture factor exactly as frozen in PREREG_LR8 A2 (Beverly & Wotton 2007 curve,
  β_m fixed); no wind factor (β_w = 0 as fitted by v8; memo C3 is a later test); no sector weighting; no receptor
  group factor (memo C4 is a later test); no scale β₀ (ranking only, counts are memo C5).
- Not in M8-C1: wind direction, intensity, FRP, fuel group, terrain, curing (all tested and found not to transfer,
  [R68] RQ1b). M8-C1 is a two-factor model and can be followed by an EOC: "nearest active edge, how far, how dry".

**Sensitivity (reported, never deciding):** f replaced by the global logistic slope of the DIST model (−0.1315 per km,
e-folding 7.6 km); and score_C1 without p_e (pure decay = DIST). These show whether the shape and the moisture term
matter beyond the one-coefficient decay.

## 3. The frozen decay shape f(d) (training split only; `stage2/m8c1_decay_shape.json`, sha256 `fec2d886…`)

Derived 2026-10-30 from the stage-1 evaluation table (4,194,927 candidate cells in the 161 training jump intervals,
234 positives; the same table the ML ladder used, never a decision fire): the log of (share of positives in a distance
bin) / (share of candidates in the bin), both regions pooled (the two regions agree within 0.4 in every bin).

| d (km) | 1–2 | 2–3 | 3–4 | 4–5 | 5–6 | 6–8 | ≥ 8 (floor) |
|---|---|---|---|---|---|---|---|
| positives | 78 | 27 | 17 | 15 | 11 | 19 | 55 |
| log ratio | +2.46 | +1.22 | +0.63 | +0.43 | +0.06 | −0.17 | −1.03 |

f(d) = the log ratio at the bin midpoints (1.5, 2.5, 3.5, 4.5, 5.5, 7.0 km), linearly interpolated between them,
constant at +2.46 below 1.5 km and at **−1.03** from 8 km outward (the jump-weighted mean of the four bins beyond
8 km: −1.00, −0.79, −1.12, −1.12). Monotone non-increasing by construction. Positives' distance quantiles: 10 % 1.3,
50 % 3.7, 90 % 17.1 km; candidates' 4.6, 14.2, 58.1 km (the candidate band is 1–15 km from the *perimeter*; from the
*active* edge it reaches much further, which is where the floor applies).

**The shape is frozen at this draft and is not refitted on any held-out fire.** If the owner prefers a smoother
parametric form, it is chosen on the training split before the freeze and recorded here; after the freeze nothing
changes.

## 4. Implementation (before the freeze)

- `score_C1` as a function of the frozen v8 interval product (`d_act` array, `m_ff` per zone) in the v8 runner
  (`scripts/validation/v8/intervals.py` or a sibling), selected by a `kernel="c1"` argument; M8's own path untouched.
- Unit tests: (a) `score_C1` reproduces the table of §3 at the bin midpoints and is non-increasing in d for fixed m_ff;
  (b) p_e equals M8's own factor; (c) on five training fires the C1 ranks of the training jumps equal those computed
  from the stage-2 feature table (bit-for-bit candidate sets).
- Code and data sha256s added to §8 at the freeze; the implementing commit named.

## 5. Held-out data and power (revised 2026-10-30 09:30 after the FireSim main session's reply; owner decision D9)

- **There is no undrawn remainder.** The v8 decision fires not drawn in v8 (ranks 201–477 CAN and 201–303 US of the
  sealed `decision_order.csv`) are **pool A** of PREREG_LR8S/LR8C/LR8L (frozen 2026-10-29, `feat/lr8-next`), and the
  eastern-Canada extension is **pool E**; their draw is running and their per-fire counts are sealed to this project.
- **Registered plan (recommended; the main session's proposal):** M8-C1 is **an additional, separately registered
  comparison on the same LR8 pool-A (+E) sealed jumps**, scored by LR8's one-shot unseal with the registered jump
  rule, candidate cells and interval products (`d_act` and `m_ff` are both in them). This file and PREREG_LR8S
  cross-reference each other as sharing data. Conditions, fixed now: (a) this file is **frozen and pushed before
  LR8's unseal is given the go**; if it is not, M8-C1 is not scored on pool A and waits for the next pool; (b) no
  pool-A or pool-E count or content reaches this project before the freeze (the main session keeps it so);
  (c) the deciding test of §6 is evaluated once, on the same ranks, by the same scorer run.
- **Stop rule and fallback, aligned with PREREG_LR8S §5:** K ≥ 150 counted jumps with ≥ 60 per region; if, after
  every fire is drawn, a region holds < 60, the test is still decided if **K ≥ 100 in total and ≥ 25 fires hold
  ranked jumps**; a region with < 30 ranked jumps is reported with its bootstrap interval and does not decide alone
  ("CAN-led decision", stated in the result); K < 100 or < 25 fires → not decidable. Identical to LR8S so that the two
  results sit on one footing.
- **Power:** sized at Δ = +0.08; recomputed from the unsealed counts with the stage-2 power machinery before any
  score is read (counts are unsealed by LR8's own procedure, not by this project), and reported as descriptive if
  power at +0.08 is below 0.80. The development expectation (DIST − M8 +0.23 on the v8 decision fires) is far above
  the margin.
- **Spent fires:** the v8 decision fires are spent (v8 and [R68]) and never decide here; they may be reported as a
  descriptive stratum, labelled.
- **Alternative (not recommended):** wait for a new pool (LR8G's BC/SK/MB/NT/YT 2014–24 pool after LR8C passes, or a
  2026-season pool next year); more data later, nothing this season.

## 6. Pass rule and what is reported

- **Pass:** S3(M8-C1 − M8) > 0, one-sided fire-level sign-flip test (20,000 flips, seed `S_TEST_C1 = 2026103002`),
  p < 0.025, on the block of §5; candidate cells, labels, ranks and weights exactly PREREG_LR8 §6, A1.
- Reported with it: stratified fire bootstrap 95 % interval (2,000 draws, seed `S_BOOT_C1 = 2026103003`); region AUCs
  (M8's ≥ 0.60 floor reported, not deciding); S3 by region, gap class, **distance band (1–3, 3–7, 7–15 km)**, active
  share; M8-C1 − B1; M8-C1 − DIST and M8-C1 without p_e (sensitivities of §2); ELMFIRE where run; the count secondary
  untouched (M8-C1 does not change counts).
- **Readings fixed now.** Pass: the landing decay is the correction; memo C3–C5 follow on the same framework. Fail with
  M8-C1 ≈ DIST ≈ M8: the [R68] decay did not transfer either, and the ember work moves to the inputs (perimeter and
  active-edge quality) before any further kernel change. Fail with M8-C1 < M8 beyond 7 km only: keep the long mode as
  a separate additive term (memo C2) and re-register.

## 7. Blindness and disclosures

- The shape of §3 was derived from the training split after [R68] was known; [R68]'s strata (where the decay fails
  beyond 7 km) informed the decision to keep a floor rather than let f fall to −∞. No held-out or sealed fire enters
  §3.
- The professor session's one blinding slip (PREREG_ML_EMBER §6) concerned M8 vs B1 under altered fuel on the decision
  fires; it has no bearing on §3 or §5.
- The FireSim main session has run post-hoc analyses on the v8 decision fires (fuel seam); none is used here.

## 8. Frozen files (filled at the freeze)

| File | sha256 |
|---|---|
| `reports/data/ml-ember-cast-2026-10-29/stage2/m8c1_decay_shape.json` | `fec2d886…` (full hash at freeze) |
| implementation commit, `score_C1` module, unit tests | at freeze |
| the block's `draw/counts.csv` at the stop | at scoring |

## 9. Owner decisions requested

- **D8** approve M8-C1 as the next ember test, with the two-factor model of §2 and the frozen shape of §3.
  *Recommend: approve.*
- **D9** approve §5: M8-C1 co-registered on the LR8 pool-A (+E) sealed jumps, frozen before LR8's unseal, with the LR8S
  fallback rule. *Recommend: approve* (the main session proposes the same; no new data, most power this season).
- **D10** who implements §4: the main session on the v8 runner (`kernel="c1"` next to `m8_score`, M8's path
  byte-identical, this file's §4 unit tests), reviewed by this session. *Recommend: the main session* (it prefers this;
  one owner for the scoring code both studies use at unseal).

**Approval:** _(verbatim, with date and time, at the freeze)_.

## 10. Result (appended after the one-shot run)

_(empty)_
