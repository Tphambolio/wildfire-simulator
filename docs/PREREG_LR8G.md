# Pre-registration: M8 ember coupling in FireSim's growth model (LR8G) [R74]

**Status:** frozen 2026-10-29, committed and pushed **before any data of its fire-days is opened** (no fire-day of
§3 has been selected, built, run or looked at; the selection rule is fixed here and has not been executed).
**Owner approval:** Travis Kennedy, 2026-10-29: "push embers into the standard simulation via a pre-registered path".
**Branch:** `feat/lr8-next` (from `master` `cbaeafc`).
**Gate (fixed now):** LR8G runs **only if PREREG_LR8C passes** (both regions; its §4 criteria 1-3). If LR8C fails or
is not decidable, LR8G is not run and this file records why. PREREG_LR8C §5 states that a FAIL is the expected
outcome, so this registration may never be executed; it is registered now so that the path, the metrics and the
margins cannot be chosen after the LR8C result is seen.
**Related:** PREREG_LR8 + A1-A4 [R70]; PREREG_LR8C; PREREG_LR8L; PREREG_LR8S [R73]; PREREG_COMPARATOR_C1 (17 h F1,
margin 0.03) and C1B (interval-censored community-arrival error) [R66]; PREREG_SEALED_CONFIRM_H1H2 [R67] (US bias).

## 1. Question

Does FireSim with **M8-placed spot ignitions** (G1) forecast community arrival better than FireSim **without
spotting** (G0), without losing daily-growth skill and without worsening the US over-spread, on fresh held-out
fire-days?

- **Non-inferiority (must hold):** 17 h F1, G1 − G0, lower 95 % bound > **−0.03** (the C1 margin).
- **Primary benefit:** community-arrival error (PREREG_COMPARATOR_C1B §5, interval-censored), G1 − G0, upper 95 %
  bound **< 0** ("better").
- **Guard:** US log-area bias (R67: ensemble-mean bias +1.17 log10) may not worsen by more than **+0.10 log10**
  (upper 95 % bound of Δbias = bias_G1 − bias_G0 ≤ +0.10, about +26 % in area).

PASS = non-inferiority **and** benefit **and** guard. Anything else is FAIL; too little data is NOT DECIDABLE (§6).

## 2. The two arms (fixed here; the engine option is built after the gate, its code hash added by addendum)

Both arms: FireSim's operational set-up as PREREG_COMPARATOR_C1 §4 (start 06:00 local, active edges = cells burned in
the 2 days before the burn day with a one-cell buffer, hourly FFMC spun up from 17:00, burning period 10:00-20:00,
no suppression, 24 h), FireSim's existing Albini spotting **off** in both.

- **G0:** that set-up.
- **G1:** the same plus M8 spot ignitions, a new opt-in engine option (off by default, labelled illustrative until a
  pass):
  - **Sources, each model hour h (inside the burning period):** the simulated active edge = burned-region edge cells
    whose arrival lies in (h − 1 h, h] (the front that moved this hour), with Byram intensity from FireSim's own
    simulated rate of spread and the FBP fuel consumption of the cell (as M8's `tfc_intensity`).
  - **Placement (M8):** the PREREG_LR8 §5 / A2 M8 landing and establishment field λ_h(c) over burnable, unburned cells
    c 1-15 km from the current burned region (the band M8 was tested and calibrated on), A2 β (β₀ −22.670, β_w 0,
    β_m −0.2441), grass curing as the run's, κ_g 0. **β_L:** −4.675 (A2 fit); **if PREREG_LR8L passes, β_L = ln 0.5
    (its M8-L long mode) instead.** This rule is fixed now.
  - **Count (LR8C):** N_h ~ Poisson(c_r Σ_c λ_h(c)), with c_r the LR8C per-region count calibration (CAN 2.5170, US
    0.16105; PREREG_LR8C §2), the values LR8C passes with. Cells drawn multinomially ∝ λ_h (with replacement; a repeat
    adds nothing). Each drawn cell becomes a point ignition at the end of hour h and then grows like any FireSim fire.
  - **Stochastic members:** FireSim's existing ensemble, `firesim.spread.ensemble.perturb_config` with the A15 sizes,
    K = 20 members, ensemble seed 1 (as R67 H2). G0 and G1 share each member's perturbations (common random numbers);
    G1's spot draws use `numpy.random.default_rng` seeded by sha256(`"{fire_id}:{day}:ens1:{i}:m8"`) (first 8 bytes).
  - Undetected and < 1 km spots are not modelled (M8 is calibrated to VIIRS-detected spots ≥ 1 km out): disclosed.
- **Jump share (optional, by addendum only).** The R72 under-spread diagnosis may later report a jump share (share of
  community arrivals due to spotting). A dated addendum may add it here as a reported quantity or a stratum **only
  before any LR8G fire-day is selected or opened**. It cannot change §1's criteria or margins.

## 3. Fire-days (fresh; selection rule fixed, not yet executed)

- **Canadian pool (F1 and communities):** CFSDS v1.1 fires 2014-2024 with centroid in **BC, SK, MB, NT or YT** (any
  size), excluding (a) every fire of the v8 pool (training, decision, exclusions; PREREG_LR8 §3) and of the LR8L pool E,
  (b) every fire whose ID (`<year>_<NFIREID>`, or the NBAC GID) or "`<name> Fire`" appears in any text file under
  `~/dev/wildfire/reports/`, `~/dev/wildfire/validation-data/` (except `sealed2026/`) or the repository's `docs/`
  (SEALED_2026 rule 8, same scan), (c) Alberta fires (the record's 32 fires, C1B and the harness have used them).
- **Fire-days:** `firesim.validation.cfsds.fire_day_pairs` with the harness defaults (day n−1 burned; day n growth
  touches the earlier area; ≥ 1 growth cell).
- **Domains:** the harness `prepare` method (90 m, 180 m where needed to stay ≤ 1.2 M cells, fire + 15 km, MRDEM-30),
  fuel = CFS FBP 2024 national grid (`cfs_national`) with the SCANFI v2 pre-fire back-cast (the v8 Canadian method,
  A3). Weather: ERA5 hourly via Open-Meteo, CFSDS previous-day codes (as C1).
- **Set F (17 h F1):** fires in the order `numpy.random.default_rng(2026102941).permutation` of the sorted eligible
  fire IDs; up to **5 fire-days per fire** (a seeded `random.Random(f"2026102942:{fire_id}")` sample when more); fires
  are added in order until **80 fire-days** on which both arms completed, or the pool ends.
- **Set C (community arrival):** every fire-day of the Canadian pool on which the observed day-n growth touches a
  community reach zone no earlier day touched (C1B §2 rule 3). Community units outside Alberta: Statistics Canada
  2021 census subdivisions of settlement type (StatCan CSD type codes CY, T, VL, SV, DM, IM, IGD, NL, HAM, SET, CG, SG,
  RV, NV, NH, IRI, S-É; rural and unorganised types such as RDA, RM, NO, LGD are not communities) and CGNDB populated places (OGL-Canada) as 250 m discs; reach zone = unit + 500 m,
  all-touched. No sampling.
- **Set U (US guard):** the R67 H2 population: the 62 fire-days of 14 sealed-2026 `confirm` fires (orders 1-16) with
  R67's inputs and FEDS progression. **Disclosed reuse:** these fires are spent for deciding and their G0 bias is
  known (+1.17). They serve only as a guard, which can only fail G1, never support it; Δbias is a paired spot-minus-
  no-spot increment no run has measured.

## 4. Metrics

- **17 h F1 (set F):** `harness.score_day` per member; the fire-day value is the mean over the 20 members; d =
  F1_G1 − F1_G0.
- **Community-arrival error (set C):** a community's arm arrival = median over the 20 members of the member's earliest
  arrival in the zone (not reached in 24 h → 24 h); error by C1B §5 (observed interval [lo, hi] from CFSDS day and
  VIIRS; 0 inside, else the distance); fire-day value = mean over its reached communities; d = err_G1 − err_G0.
  Reported: hits ≤ 18 h, false arrivals at unreached eligible communities (a guard against spots that "arrive"
  everywhere: reported with CI, never deciding).
- **US bias (set U):** `ensemble_scores` `ens_mean_bias_log10` per arm on the same 62 fire-days; Δbias = G1 − G0.
- **Uncertainty:** 95 % percentile bootstrap of whole fires, 2,000 draws, `default_rng(2026102943)` (F),
  `2026102944` (C), `2026102945` (U).

## 5. Expected outcome (stated now)

- F1: spot fires 1-15 km out add little area on most days (c_r Σλ is small in Canada), so non-inferiority is likely.
- Communities: arrivals FireSim misses are mostly large under-spreads ([R66] C1B: 10 of 16 communities not reached by
  either model); M8 spots land near and downwind of the active edge ([R70]), so they can shorten some arrival errors,
  but the effect may be small. An inconclusive benefit (CI crossing 0) is a FAIL.
- US guard: c_US = 0.161 keeps US spot counts low; the guard is expected to hold unless spots fall on fast US fuels.

## 6. Minimum data, failures, rules

- **Not decidable** if set C has < 10 fire-days or < 6 fires with both arms complete, or set F has < 40 fire-days.
- **Failures:** a FireSim error, the 5 GB cap or 2 h per member on a fire-day: recorded, never retried (except a
  workstation failure, rerun once, disclosed); the primary populations are fire-days where both arms completed;
  sensitivity: a failed G1 fire-day scored as G0.
- Open data only, nothing requested from any agency; no ML (M8 and c_r are fixed, nothing fitted here);
  W.I.S.E. and ELMFIRE are not used. `capjob`, ≤ 5 GB, ≥ 8 GB RAM available, ≥ 20 GB disk free.
- **Engine change:** the G1 option is an engine change; it needs the full test stack, an equivalence check (G1 with
  c_r = 0 is bit-identical to G0 on two calibration fire-days) and a dated addendum with its code sha256 **before**
  any §3 fire-day is selected. It stays opt-in unless LR8G passes; a FAIL leaves it opt-in and unmerged.
- Never in the product: evacuation tiers, addresses, owner data, per-building results.
- **Gate:** the main session gives the go before any LR8G fire-day is selected, run or scored.
