# Pre-registration: the v8 long-range mode, spot fires 3-15 km out (LR8L)

**Status:** frozen 2026-10-29, committed and pushed before any held-out fire is drawn, built or scored.
**Owner approval:** Travis Kennedy, 2026-10-29 (~02:10 MDT), approving [R70] §7 ("the long mode needs its own test (the
3-15 km band, where M8 is weakest)").
**Branch:** `feat/long-range-embers-v8`.
**Planning data folder:** `reports/data/lr8-followups-prereg-2026-10-29/` (README, `extension_order.csv` + `.sha256`,
`scripts/seal_extension.py`, `long_mode_planning.json`).

## 1. Question

For new spot fires that start 3-15 km beyond the observed perimeter, on fires never used in v8, does M8 rank the jump
cells higher **with its long-distance mode switched on** than with the short mode only? Ranks are taken among candidate
cells 3-15 km from the start perimeter.

Why this question: the v8 training fit put the long mode at e^{−4.67} of the short mode, close to off, and [R70]'s skill
fell to 0.59 at 3-15 km.

## 2. Models (fixed here; no fitting on held-out fires)

All three share M8's inputs, sources, intensity and short kernel (PREREG_LR8 §5 with A1-A4 and A2 β), and differ only
in β_L:

- **M8-L (tested):** β_L = ln 0.5. This is the prior centre of A2, the glowing-brand factor of v7 (Manzello 2006/2008).
  The long mode is lognormal, median 733 m, σ_ln 0.99, cut at 15.6 km, with the plume factor of A2.3.
- **M8-S (comparator):** short mode only (β_L → −∞; implemented as β_L = −50).
- **M8-fit (reported):** the v8 β_L = −4.675.
- **B1 (reported):** as PREREG_LR8 §5.

β₀, β_w and β_m are as A2. They cancel in within-interval ranks, except that β_w weights the bed-wind classes; β_w = 0.

## 3. Fires (held out, sealed)

- **Pool A:** the undrawn v8 decision fires, Canada ranks 201-477 and US ranks 201-303 of the sealed `decision_order.csv`
  (sha256 `73bae68c…f6cb`). This is the same pool as LR8C; one draw serves both studies.
- **Pool E (extension, new):** NBAC 2026-05-13 fires of 2018-2025, ≥ 5,000 ha, not prescribed, whose largest row is in
  ON, QC, NL, NS, NB or PE (162 fires: QC 80, ON 72, NL 8, NS 2). None was in the v8 pool.
  - Order: keys sorted, then permuted with `default_rng(2026102901)`.
  - Sealed file `extension_order.csv`, sha256 `033e5ab82d384271863111e6cdf2d7a4ba4260cd738f27844d0cebd748c033b6`.
  - Built from fire IDs, years and sizes only.
- **Eligibility:** as v8 (A2.1). Stations for ON/QC/NL/NS come from the IEM networks of those provinces (open). Fuels:
  CFS FBP 2024 + SCANFI v2 back-cast as for Canada in v8 (A3).
- **No sequential stop:** all fires of A and E are drawn.
- **Not decidable:** if fewer than **40 ranked 3-15 km jumps** or fewer than **25 fires** holding them are observed.

## 4. Metric and pass rule

- **Jumps:** the PREREG_LR8 §4 rule with A1. Only members within 375 m of the final perimeter are ranked, and the
  distance to the start perimeter must be 3-15 km.
- **Candidates:** PREREG_LR8 §6 candidate cells with centres 3-15 km from the start perimeter.
- **Ranks:** percentile ranks with ties counted half, within the interval, among those candidates.
- **Statistic:** a_f = mean over the fire's 3-15 km jumps of (r_M8-L − r_M8-S). S3 weights w_f = K_f/(1 + 0.3(K_f − 1)).
- **Pass (both required):**
  1. S3(M8-L − M8-S) > 0 by the one-sided fire-level sign-flip test (20,000 flips, seed 2026102912), p < 0.025, pooled
     over regions. Flips are within region, as v8.
  2. AUC(M8-L) ≥ 0.60 on the 3-15 km jumps (S3-weighted, pooled).
- **Reported:**
  - the bootstrap 95 % CI (2,000 draws, seed 2026102913);
  - results by region (CAN west, US, CAN east);
  - M8-fit, B1 and M8-L − B1;
  - the tail ratio P(> 3 km)/P(> 1 km) of each model;
  - gap classes.

If 1 and 2 hold, the long mode at β_L = ln 0.5 is supported. The next ember release may then use it in place of the
fitted β_L, after its own equivalence note. A FAIL leaves the long mode off (as fitted) and the 3-15 km claim out of all
product text.

## 5. Planning evidence (v8 decision data, already opened in [R70])

- On the 32 ranked v8 decision jumps at 3-15 km (21 fires):
  - AUC among 3-15 km candidates: M8-L 0.606, M8-S 0.508, M8-fit 0.582, B1 0.498.
  - S3(M8-L − M8-S) = +0.099. The per-fire SD is 0.215, and only 48 % of fires have a_f > 0, because the gain sits in a
    few fires.
- **Power** (sign-flip, per-fire SD 0.215, K distribution of v8):

  | Fires with long jumps | Jumps (approx.) | Δ = +0.06 | Δ = +0.08 | Δ = +0.10 |
  |---|---|---|---|---|
  | 25 | 38 | 0.25 | 0.41 | 0.56 |
  | 30 | 46 | 0.30 | 0.46 | 0.66 |
  | 40 | 61 | 0.37 | 0.59 | 0.79 |
  | 50 | 76 | 0.43 | 0.67 | 0.86 |

- **Expected yield:** about 24 long jumps from pool A, at the v8 rate of 0.15 per eligible fire. Pool E's yield is
  unknown, though the 2023 Quebec fires are large. The likely total is 25-40 fires, so **power is about 0.4-0.6 at the
  planning effect, and a not-decidable outcome is possible**. This is stated before any data. The owner chose to
  register the test rather than wait for 2026 fires.

## 6. Rules

Open data only; nothing requested from any agency; no ML; ELMFIRE not used. `capjob`, ≤ 5 GB per job, started only with
≥ 8 GB RAM and ≥ 20 GB disk free. Jump locations are sealed per fire (sha256 manifest) and opened once, after all three
M8 variants and B1 have been scored as inputs on every drawn fire. Changes only as dated addenda, before any held-out
score is seen.

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


- **Draw errors and retries (2026-10-30; shared draw, same text as PREREG_LR8C A3, before any sealed file is opened).** The first draw pass logged 9 pool A
  Canadian fires as ERROR. All 9 failed in the blind inputs step, after their weather was fetched and at least one
  IEM station had passed the ≥ 50 % coverage test. Each failed during the NRCan SCANFI v2 server reads, so none was
  sealed. The causes come from the per-fire error records (`heldout/state.json`, `runs.csv`). No outcome data was read
  to find them.
  - CAN 218, 226: `ModuleNotFoundError` in the first launch, because the repo was not on PYTHONPATH. They were retried
    and are done.
  - CAN 262, 265, 274: `RasterioIOError` ("Read failed") after 67-80 min. The SCANFI server timed out after the
    registered 12 back-off retries; 218 failed the same way once before it succeeded.
  - CAN 296, 299, 303, 327: `CRSError('CRS is invalid: None')` after 4-14 min. Under load the SCANFI server returned
    a dataset with no georeferencing (rasterio `NotGeoreferencedWarning` in the run log). The back-off loop retries
    only `RasterioIOError`, so the error was not retried inside the build.
  - **Rule (no change to the code or the per-fire steps).** A fire that errors is not done, and every draw pass
    retries every fire that is not done, in sealed order, with the same code. Each errored fire's blind inputs were
    rebuilt with the unchanged `inputs.build(blind=True)`, and a second draw pass then seals it and runs the
    registered intervals. A fire that still fails for data-access reasons after that is listed here, with its cause,
    as a **draw failure** before `unseal`. It is reported as such and is not counted as ineligible. Eligibility is
    decided only by the station rule.
  - **Retry results:** CAN 296, 299, 303 and 327 rebuilt cleanly on the first retry (all eligible by IEM; the SCANFI layers that were missing were fetched from the server). CAN 262 was being rebuilt at the time of writing. CAN 265 and 274 are paused: their own `wx_*.csv` files were made read-only, so that no existing file is overwritten while another session reads `v8-pool/` (constraint from the main session, 2026-10-30). The second draw pass that seals and runs intervals for all seven waits for that constraint to be lifted. The final status of each fire is added here before `unseal`.
  - **Slip disclosed.** The first diagnostic retry (CAN 296) was run by mistake in training mode (`v8.inputs` CLI,
    `blind=False`). It read only the training and decision FIRMS caches, found no rows for this fire (`None` window),
    and wrote an ineligible `meta.json`. That file was deleted with `rm` (a permanent delete, against the owner rule to move files to the trash with `gio trash`) before the blind rebuild. No held-out data was read.


- **Local SCANFI copy and the draw's SCANFI reads (2026-10-31; shared draw, same text as PREREG_LR8C A4; record only, decision pending).**
  - **Copy.** The 26 national files the runner reads (SCANFI v2 release 20260119, 13 layers × epochs 2015 and 2020,
    44.85 GB) were downloaded unchanged from the registered URLs on 2026-10-30 as a contingency (owner approval
    10:20 MDT) to `validation-data/v8-pool/raw/scanfi_v2_national/`. URLs, byte counts and sha256 are in its
    `README.md` and `_about.json`. Code: `scanfi_national.py` on branch `park/scanfi-local-reader` (34bb1e4).
  - **Where it was used.** The main draw pass read only the NRCan server. It finished all 542 fires on 2026-10-30
    (pool A CAN 277, US 103; pool E CAN 162). Draw pass 2 (23:11-23:35 UTC) and the ECCC pass (to 23:48 UTC) ran
    with a working-tree `inputs.py` (sha256 `9cca7875…56f6`, now committed on the side branch, not in the draw path)
    that reads a clip from the local copy when that file's download is complete. Every file was complete by then.
    So **24 pool fires** (the fires that had errored on the server; all eligible) and the **19 ECCC-eligible fires**
    took some or all SCANFI clips from the local copy. Their meta.json `scanfi_cache` says `local_national_clip`.
    The intended order was equivalence first, then use. The use came first because the chain started pass 2 by
    itself when the main pass ended.
  - **Equivalence (12 server-built Canadian fires, 2018-2025, both epochs, 2 multi-zone; blind inputs only).** On
    11 fires, all 13 clips and the whole fuel array are identical. On NBAC:2025_179 the server clip of
    `spsCC_otherConiferous` is all nodata (255). The local copy has values there, and 441 of 123,264 fuel cells
    differ.
  - **Audit of all 2,275 cached server clips** against the same windows of the local copy (`logs/
    scanfi_clip_audit_2026-10-31.json`): 2,272 are identical. 3 are all-nodata server artefacts, each in an
    eligible, sealed pool A fire: NBAC:2019_382 (`att_nfiLandcover` 2015; 170,040 of 887,040 fuel cells differ),
    NBAC:2021_1177 (`att_nfiLandcover` 2020; 18,067 of 227,088), and NBAC:2025_179 (`spsCC_otherConiferous` 2020;
    441 of 123,264). The server sometimes returned nodata blocks without an error (see A3).
  - **Not decided here:** whether these 3 fires' blind inputs, intervals and sim edges are rebuilt from correct
    SCANFI values before `unseal`, or whether they are kept as drawn. Either choice is made and recorded before any
    sealed file is opened.
