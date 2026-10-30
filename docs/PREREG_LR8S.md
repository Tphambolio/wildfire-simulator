# Pre-registration: M8 on a simulated active edge (LR8S) [R73]

**Status:** frozen 2026-10-29, committed and pushed **before any data of its fires is opened**: no hotspot of a
pool A or pool E fire (below) has been extracted, no jump catalogued, no model run on them. Only blind input builds
(station, fuel, curing) exist for some pool A fires (PREREG_LR8C §3).
**Owner approval:** Travis Kennedy, 2026-10-29: "push embers into the standard simulation via a pre-registered path";
this test is the first step of that path.
**Branch:** `feat/lr8-next` (from `master` `cbaeafc`).
**Related:** PREREG_LR8 (`6452c1d` + A1-A4) and its result [R70]; PREREG_LR8C; PREREG_LR8L; PREREG_LR8G [R74];
PREREG_COMPARATOR_C1 §4 (FireSim's operational set-up).
**Planning data:** `reports/data/lr8-next-prereg-2026-10-29/` (outside the repo; README, `power_lr8s.py`,
`power_lr8s.json`). The planning numbers come only from the v8 decision fires, already opened and scored in [R70].

## 1. Question

In [R70], M8's sources were the **observed** active edge: start-perimeter edge cells with a VIIRS detection within
1 km in the 12 h before the interval. In the app there is no such observation hours ahead; the edge comes from
FireSim's own simulated front. **When M8's active edge comes from FireSim's simulated front (each fire-day simulated
from the previous day's observed perimeter), does M8 still rank the observed new VIIRS spot locations 1-15 km out
better than B1 computed from the same simulated edge?**

- **Primary (decides):** S3(M8_sim − B1_sim), with the PREREG_LR8 §7 criteria (adapted only as §5 says).
- **Descriptive ceiling:** M8_obs, M8 on the observed edge exactly as [R70] (registered code and β).
- **Secondary (reported, never deciding):** the loss from the observed edge, S3(M8_obs − M8_sim), with its bootstrap
  CI; AUCs of all four models (M8_sim, B1_sim, M8_obs, B1_obs) by region and distance band.

## 2. Fires (held out, unopened)

- **Pool A:** the v8 decision fires not drawn in v8: ranks 201-477 (CAN) and 201-303 (US) of the sealed
  `decision_order.csv` (sha256 `73bae68c0da1528314dea91e5634e8bebfcf84e5e94b39e22ac49b61a8a0f6cb`). These are the
  next fires in the documented sealed order after v8's decision fires.
- **Pool E:** the eastern-Canada extension of PREREG_LR8L §3 (`extension_order.csv`, sha256
  `033e5ab82d384271863111e6cdf2d7a4ba4260cd738f27844d0cebd748c033b6`; 162 NBAC fires in ON, QC, NL, NS). Pool E
  fires are region **CAN** for every rule below (the PREREG_LR8 regions are CAN and US); CAN-west and CAN-east are
  reported separately.
- **Shared draw.** LR8C and LR8L draw the same pools. One draw serves all three studies: inputs, detections, the
  sealed jump tables and every model run of LR8C, LR8L and LR8S are produced before the single `unseal`. The
  jump locations are opened **once**, after M8_obs, M8_sim, B1_obs, B1_sim and the LR8L variants have run on every
  drawn eligible fire. Per-fire counts are sealed too (LR8C scores counts), so nothing about any outcome is seen
  before that.
- **Eligibility** as v8 (A2.1): an IEM station within 50 km with ≥ 50 % hourly coverage over the record window, plus
  pre-fire fuels. **No sequential stop:** every fire of both pools is drawn.
- **Exclusions:** fires of the PREREG_LR8 exclusion list never enter (none is in the pools by construction).

## 3. The simulated front (fixed here)

For each eligible fire and each **fire-day D** in local standard time (UTC offset = round(longitude / 15) h, the
rule v8 already uses for the daily codes, A2.2), with D running 06:00 D to 06:00 D+1:

1. **Observed start of day D.** The PREREG_LR8 §4 start perimeter built from every detection acquired before
   06:00 D (dilation 375 m, 1 km closing, 125 m grid): `P_obs(D)`. A day with no detection before 06:00 D (the first
   day) is not simulated, and its intervals are not scored (they also have no v8 start perimeter).
2. **Domain.** The fire's v8 grid (`inputs.npz`: code, curing; 125 m, UTM). Terrain: `inputs.npz` carries no
   elevation (M8 needs none), so elevation comes from the ELMFIRE comparator's open sources (A2.11): NRCan MRDEM-30
   (Canada) and LANDFIRE LF2020 elevation (US), resampled bilinear to the run grid. FireSim runs on a lat/lng domain
   resampled from it nearest-neighbour at the same nominal cell size; arrival times are mapped back to the 125 m
   grid nearest-neighbour. Fuel: the v8 code layer (`cfs_national` scheme for CIFFC codes; US: the same FBP codes the
   v8 inputs hold). Leaf rule, percent conifer and dead fir as the harness (`RunOptions` defaults); O-1 curing = the
   v8 NDVI curing (A2.3) of the cell.
3. **FireSim operational set-up** (PREREG_COMPARATOR_C1 §4, the record's `active2_spin_bp1020`), through
   `firesim.validation.harness` unchanged: start 06:00 D; `P_obs(D)` is the burned start; **active edges** = cells of
   `P_obs(D)` with a detection in the 48 h before 06:00 D (one-cell buffer), the rest burned out; hourly FFMC spun up
   from 17:00 D−1; burning period 10:00-20:00; deterministic; FireSim's own spotting **off**; no suppression; 24 h.
4. **Weather.** The v8 zone station's hourly temperature, RH, wind and ERA5 precipitation (A2.1); start FFMC = v8's
   hourly FFMC at 17:00 D−1; DMC and DC = v8's daily codes of D−1 (A2.2). One stream per fire-day: the zone holding
   most active-edge cells of `P_obs(D)` (multi-zone fires, A2.4).
5. **Simulated perimeter at time t** (06:00 D ≤ t < 06:00 D+1): `P_sim(t)` = `P_obs(D)` ∪ {cells with simulated
   arrival ≤ t}, then the same 375 m dilation and 1 km closing as §4 (so its edge is built like the observed one).
6. **Simulated active edge at t:** edge cells of `P_sim(t)` within 1 km of a cell that is "burning" in the 12 h
   before t, where a cell is burning if its simulated arrival lies in (t − 12 h, t], or, for times before 06:00 D,
   if it holds a nominal/high VIIRS detection in that window (the simulation cannot see the night before it starts).
   This is the OD12 rule with simulated arrival in place of detection.
7. **Simulated spread rate** at a source cell: distance from the cell to `P_sim(t_prev)` divided by the time since
   t_prev (floor 0.5 m/min, ≥ 60 min, as `intervals.py`), with t_prev = the previous epoch's time, or 06:00 D when
   the previous epoch is earlier than 06:00 D (then `P_obs(D)` is the reference).

## 4. Models (same intervals, jumps and candidate cells as PREREG_LR8)

- **Intervals, jumps, candidate cells, ranks:** exactly PREREG_LR8 §4 and §6 with A1-A4, all defined on the
  **observed** start perimeter. The jump catalogue and the candidate set do not change with the model, so all four
  models are ranked on the same cells. A candidate cell that `P_sim` already covers keeps the model's score there
  (it is not removed; reported: share of ranked jumps inside `P_sim(t_start)`).
- **M8_sim:** `intervals.py` physics with A2 β (β₀ −22.670, β_w 0, β_L −4.675, β_m −0.2441), sources = the simulated
  active edge (§3.6), spread rate = §3.7. Nothing is refitted.
- **B1_sim:** PREREG_LR8 §5 B1 with d = distance to the nearest simulated active cell and the ±45° sector taken from
  it (when no cell is active: from `P_sim(t_start)`, as `intervals.py` does with the observed edge).
- **M8_obs, B1_obs:** the registered models on the observed edge (`intervals.py` sha256 `4303e544…4dbe`, unchanged).
- **Failures:** a FireSim error or a 2 h wall-time / 5 GB cap on a fire-day drops that fire-day's intervals **for the
  sim models only**, counted; if more than 10 % of the valid intervals are dropped this way, LR8S **fails**
  (the simulated edge is not available often enough to be used). M8_obs/B1_obs are scored on the intervals both sim
  models have.

## 5. Pass rule (PREREG_LR8 §7, with a fallback fixed now)

1. **K:** counted jumps (PREREG_LR8 §3 count: members within 375 m) **K ≥ 150 and each region ≥ 60**.
2. **G1** (PREREG_LR8 §7.2, A2.6 ranges) on M8_sim passes on ≥ 90 % of eligible fires.
3. **S3(M8_sim − B1_sim) > 0**, one-sided fire-level sign-flip test, 20,000 flips within regions,
   seed **2026102931**, p < 0.025.
4. **Region AUC(M8_sim) ≥ 0.60** in each region (S3 weights).

**Fallback (power check, §6; fixed before any data).** Pool A + E may not reach 60 counted US jumps. If, after every
fire is drawn, criterion 1 fails only because a region holds < 60:
- the test is still decided if **K ≥ 100** in total and ≥ **25 fires** hold ranked jumps;
- criterion 4 then applies to each region holding ≥ 30 ranked jumps; a region with fewer is reported with its
  bootstrap CI and does not decide ("CAN-led decision", stated in the result);
- criteria 2 and 3 are unchanged (pooled sign-flip within regions).
If K < 100 or fewer than 25 fires hold ranked jumps: **not decidable** (no pass, no fail). No other change to the
criteria is allowed after data are opened.

**Reported with the decision:** stratified fire bootstrap 95 % CI of S3 (2,000 draws, seed **2026102932**), overall,
by region (CAN-west, CAN-east, US) and per fire; S3(M8_obs − M8_sim) and S3(M8_obs − B1_obs) with CIs; AUCs by
distance band (1-2, 2-3, 3-15 km) and gap class; share of jumps inside `P_sim`; the simulated vs observed active
length.
**Fuel-seam check (reported, never deciding):** the [R70] fuel-seam sensitivity repeated on these fires: M8_sim and
B1_sim rescored on Canadian fires with the fuel layer restricted to the B1 hazard class (`scripts/validation/v8/
fuel_seam.py` rule: hazardous → C-2, burnable non-hazardous → D-1/D-2, non-fuel unchanged; FireSim's growth run itself
keeps the full codes), with S3 and the CAN AUCs beside the primary numbers.

## 6. Power (planning, v8 decision data already opened)

Resampling the 73 v8 per-fire a_f values (SD 0.24), recentred to a true Δ, with the sign-flip test
(`power_lr8s.py`, 1,000 studies × 1,000 flips):

| Fires with ranked jumps | Δ = +0.04 | +0.06 | +0.08 | +0.10 |
|---|---|---|---|---|
| 29 CAN + 25 US (expected from pool A alone) | 0.26 | 0.54 | **0.81** | 0.95 |
| 40 CAN + 25 US (pool A + a modest pool E yield) | 0.30 | 0.64 | **0.89** | 0.98 |
| 20 CAN + 20 US (low yield) | 0.21 | 0.43 | 0.66 | 0.87 |

Expected yield: pool A CAN 277 fires, of which 11 of the 49 built blind are eligible (22 %) → about 62 eligible, about
94 counted jumps at v8's 1.52 per eligible Canadian fire; pool A US 103 fires, 80 eligible (built blind) → about 46 at
v8's 0.57. Pool E's yield is unknown. **So the US will probably hold < 60 counted jumps and the fallback is the likely
decision route.** [R70]'s margin was +0.147; the test is sized for the simulated edge to keep about half of it
(+0.08). A smaller retained gain (≤ +0.05) will most likely not reach p < 0.025; that outcome is a FAIL and is
informative (the simulated edge loses M8's advantage).

## 7. Rules

- Open data only; nothing requested from any agency. No ML; nothing is fitted on these fires.
- Heavy jobs via `capjob`, ≤ 5 GB each, started only with ≥ 8 GB RAM available and ≥ 20 GB disk free.
- Development of the §3 simulation code on **v8 training fires only** (equivalence checks, timing); its sha256 goes
  into a dated addendum here **before** any pool fire is simulated. No addendum after any score of this test is seen.
- **Gate:** the main session gives the go before `unseal` and before any LR8S score is computed.
- ELMFIRE and W.I.S.E. are not used.

---

## 8. Addendum A1 (2026-10-29): implementation, before any pool fire is simulated

**Dated:** 2026-10-29. Sections 1-7 above are the text frozen at `e9c6e51` and were not edited. Nothing of pool A or
pool E had been simulated, catalogued or scored when this was committed. Development used v8 **training** fires only.

1. **Code.** The simulated-edge runner is `scripts/validation/v8/simedge.py` (sha256 `c356a927ee7e1966acac7ffcaf05a59967d1604d9739866cb7d418b4c04f43f3`). The shared draw is
   `scripts/validation/v8/heldout.py` (sha256 `9cb4677df56ef73cbc2363b3190ea85ef5ed7a6c4f5a21a8c1886d97de79d40f`): inputs, detections, the sealed jump table with counts, and
   the registered intervals.
2. **Equivalence.** In observed mode (`--obs`), the runner uses the same M8/B1 code path with the observed perimeter,
   active edge and spread rate. Its interval files are **bit-identical** to the registered `intervals/` files on
   4 training fires: NBAC:2018_2184, NBAC:2021_1740 (multi-zone), MTBS:UT3942511234720200626 (multi-zone, US) and
   MTBS:CO3905510606920180628. That is 22 intervals, every array equal. Candidate cells are identical in sim mode too.
3. **Deviation from §3.2 (O-1 curing).** The harness takes one grass curing value per run, not one per cell. Each run
   therefore uses the **median v8 NDVI curing over the fire's grass cells** (codes 31/32; 60 % if none).
4. **§3.3 start.** The surrogate DOB is: every cell of `P_obs(D)` = 1, and cells holding a detection in the 48 h before
   06:00 D = D−1. With `RunOptions(ignition="active", active_days=2)`, those cells are exactly the registered active
   edges; the harness adds its one-cell buffer. The FireSim domain is cropped by `harness.crop_for_day` (20 km margin).
5. **§4 failures.** The 2 h cap is a per-fire-day SIGALRM. A day with no detection before 06:00 D is recorded as
   `no_start`; its intervals are dropped for the sim models and counted.
6. **Timing (training).** 5-12 s per fire-day on small Canadian domains. US fires add a one-off LANDFIRE elevation
   export of about 5-13 min per fire, cached.

- **Hash update (2026-10-29, later the same evening).** `heldout.py` now runs fires in batches of 25 (one FIRMS pass per batch), as v8's `draw.py` did; the per-fire steps are unchanged. New sha256 `111bc43c3a52e0b45b46ec96d06100b3c34fc469b2109da68b70ec054bf7ca5f`, replacing `9cb4677df56ef73cbc2363b3190ea85ef5ed7a6c4f5a21a8c1886d97de79d40f`. The first 3 pool A fires (CAN ranks 201-203; one eligible) were processed by the earlier version. No sealed file was opened and nothing was scored.
