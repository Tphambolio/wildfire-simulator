# PREREG_LR7: long-range embers v7, calibrated front then embers (pre-registration)

- **Purpose:** to fix the v7 held-out test before any held-out fire is simulated or scored: fire split, fitted front, fitted ember establishment, models, metrics, pass criteria, bootstrap and disclosures.
- **Audience:** the FireSim owner (EOC Chief of Plans), reviewers and later sessions.
- **Status:** pre-registered, fixed on 2026-10-17 (drafted 2026-10-16), committed and pushed before any held-out simulation.
- **Machine-readable copy:** `scripts/long_range_v7_prereg.py` (`PREREG_LR7`). Its SHA-256 is printed by that script and carried in every held-out result file (`_about.prereg_sha256`).
- **Related:** v6 (`docs/PREREG_LR6.md`, report [R47]); spec §6.8; PROJECT_RECORD decision of 2026-10-15/16.

## 1. Why v7

v6 failed target A: community-block arrival MAE 11.17 h with embers vs 10.64 h without (+0.53 h, CI +0.34 to +0.75). The front was the problem: one proxy fuel and event-strength constant winds put Glass and North Complex 10-15 h early and CZU late. Embers only add spread, so the v6 fit pushed η to its lowest grid value. v7 fixes the front first on the training fires, then refits η on top.

## 2. Questions

- **A1:** Does the calibrated front (pre-fire LANDFIRE fuels, hourly observed winds, four calibrated spread parameters) predict community-block arrival better than the v6 no-ember front on fresh held-out fires?
- **A2:** On the calibrated front, do long-range ember spot fires with a variable η improve community-block arrival, without worse probabilistic reach forecasts?
- **B:** Does a combined building-loss model with a separate wildland-edge ember term, a 0-9 m vegetation ring and a fire-level wind term discriminate destroyed from surviving buildings better than density + year built on fresh held-out fires? (B2: do embers add beyond fire reach?)

## 3. Fires

| Role | Fires | Notes |
|---|---|---|
| Training (everything fitted here) | Eaton 2025, Palisades 2025, Camp 2018, Tubbs 2017, Woolsey 2018 | as v6 |
| **Primary held-out: fresh, decides** | **Dixie 2021** (Greenville / Indian Valley run, 4-5 Aug), **Caldor 2021** (Grizzly Flats run, 16-17 Aug) | never simulated or scored in any FireSim round; GOFER hourly perimeters + VIIRS; DINS with a No Damage class; pre-fire footprints |
| Secondary held-out: seen, disclosed, never decides | Glass 2020, CZU 2020, North Complex 2020 | scored in v4-v6 |

**Fresh candidates rejected** (`calfire-dins/refs/heldout_v7_selection.md`): Zogg 2020 (3 community blocks inside the perimeter), Carr 2018 (no "No Damage" class; 99.3 % post-fire footprints), Kincade 2019 (78.5 % post-fire footprints), Thomas 2017 (pre-2018 DINS protocol; not in GOFER).

**Power warning.** Fresh fires give 72 scored community blocks (Dixie 60, Caldor 12). Caldor is borderline and wind-light (sustained 2-8 mph, CZU-like). The decision therefore rests mostly on Dixie. This is stated before scoring.

## 4. Stage 1: the calibrated front (training fires only; done before this block)

| Input | v6 | v7 |
|---|---|---|
| Fuel | one proxy FBP type (C-4 or C-7) | LANDFIRE FBFM40, latest served vintage before the fire (LF2016 for 2017-2021 fires; LF2024 for 2025 fires); crosswalk G (GR, GS) -> O-1b, S (SH) -> C-4, T (TU, TL, SB) -> C-7, U (NB1 or WorldCover built >= 25 %) -> O-1b x 0.5, NB -> non-fuel (`scripts/long_range_v7_fuels.py`) |
| Wind | member constant direction, speed × station gain | hourly observed: speed of the fire's wind-event station, direction = speed-weighted vector mean over its stations (Camp: JBGC1 only); 10 m wind = f_w × RAWS 20 ft speed |
| FFMC | constant | hourly (Van Wagner 1977), 24 h spin-up |
| Spread calibration | none | m_G, m_S, m_T (ROS multipliers per group), f_w |

- **Grid:** m_G {0.08, 0.15, 0.25, 0.4, 0.6} × m_S {0.6, 1.0, 1.5, 2.2, 3.2} × m_T {0.5, 1, 2, 4} × f_w {0.45, 0.6} (200 settings), deterministic run per setting and fire, 180 m grid.
- **Objective:** J = (MAE/MAE_v6A0 + Brier/Brier_v6A0)/2 per training fire; minimise the mean over the five fires. J = 1 is v6 A0's level.
- **Result: θ = (m_G 0.25, m_S 1.5, m_T 2.0, f_w 0.45)**, mean J 0.80.

| Fire | MAE (h) at θ | bias (h) | J at θ | J, fire left out (LOFO) |
|---|---|---|---|---|
| Eaton | 0.48 | +0.28 | 0.43 | 0.43 |
| Palisades | 2.43 | +2.22 | 1.04 | 1.04 |
| Camp | 4.62 | +4.62 | 1.11 | 1.11 (no scored block reached: Camp's run to Paradise was ember-driven) |
| Tubbs | 0.65 | +0.65 | 0.99 | 3.00 (without Tubbs the fit picks m_T 0.5, which stalls Tubbs) |
| Woolsey | 2.12 | +0.64 | 0.45 | 0.45 |

Mean LOFO J 1.20. f_w sits at the lower edge of the grid.

**History (disclosed).** A first 3-parameter grid (m_open shared by grass and shrub, m_T, f_w {0.6, 0.85, 1.15}) ran on the training fires (`fit_front_stage1.json`, partial, superseded). It showed grass-dominated Woolsey wanting m_open ≤ 0.15 and shrub-dominated Palisades ≥ 1.2, with the optimum at f_w's lower edge, so the grid was redesigned once. Training fires only.

## 5. Stage 2: variable η on the calibrated front (training fires only; done before this block)

- **Model:** expected spot fires per unburned fuel cell = η0 · w(group) · p_e(category, FFMC(t)) · (landed short + k_L landed long). w = 1 for grass and town cells, κ for shrub and timber. p_e = Beverly & Wotton (2007) grass (eq 2.02) or pine (eq 2.03) at the hourly FFMC.
- **Grid:** η0 {1e-8, 1e-7, 3e-7, 1e-6, 3e-6, 1e-5} × κ {0.3, 1, 3}; 6 members (A0c paired, same wind perturbations).
- **Objective:** J = (MAE_V7/MAE_A0c + Brier_V7/Brier_A0c)/2 per training fire; minimise the mean.
- **Result: η_final = (η0 1e-8, κ 3)**, mean J 0.918 (`train/stage2/fit_eta.json`, 6 members, 108 V7 + 6 A0c runs per fire).

| Fire | A0c MAE (h) | V7 MAE (h) at η_final | J at η_final | LR spots / member | J, fire left out (LOFO) |
|---|---|---|---|---|---|
| Eaton | 0.81 | 0.53 | 0.86 | 36 | 0.86 |
| Palisades | 2.68 | 2.37 | 0.87 | 69 | 0.87 |
| Camp | 4.62 | 4.62 | 0.99 | 32 | 0.99 |
| Tubbs | 0.81 | 0.34 | 0.83 | 134 | 1.08 (without Tubbs the fit picks η0 1e-7, κ 3) |
| Woolsey | 2.71 | 2.81 | 1.03 | 28 | 1.03 |

Mean LOFO J 0.97. **η0 sits at the grid's lower edge again** (as in v6). The surface is flat near the optimum (η0 1e-7 κ 1: 0.931; η0 1e-7 κ 3: 0.944), so with 6 members the choice among these is not sharply determined. Higher η0 drives Eaton and Tubbs MAE towards 0 but over-spreads (Brier worse) and only helps Camp (J 0.50 at η0 1e-5 κ 3), whose run to Paradise was ember-driven. The grid was not extended.

## 6. Target A (held-out)

| | Metric | Condition |
|---|---|---|
| **A1 (primary)** | pooled MAE(A0c) − MAE(A0v6), h, fresh fires' scored blocks | upper 95 % CI < 0 |
| **A2 (primary)** | pooled MAE(V7) − MAE(A0c), and Brier(V7) − Brier(A0c) | MAE upper 95 % CI < 0 **and** Brier lower 95 % CI ≤ 0 |
| A3 (check) | V7 tail ratio P(>3 km)/P(>1 km); maximum | in [0.10, 0.40] (Storey 2020: 0.20); vs 13.9 km / 33 km |

- **Models:** A0v6 = v6's baseline (secondary fires: v6's own 20 held-out runs; fresh fires: the v6 rule, `v6_baseline_cfg`, re-run on the current engine). A0c = calibrated front + Albini short-range spotting. V7 = A0c + long-range embers at η_final. 20 members each; A0c and V7 share wind perturbations (speed × exp(0.2 z), direction + 10 z°).
- **Units and observations:** as v6 (540 m blocks, ≥ 10 footprints; [t_lo, t_hi] from GOFER/FIRIS perimeters, VIIRS, NIST).
- **Secondary (never decide):** the same tests on Glass, CZU and North Complex, and on all five held-out fires pooled; per-fire MAE and bias.

## 7. Target B (held-out)

| Model | Features |
|---|---|
| B_dens | log(1 + buildings within 100 m) |
| B_dens_age | + year built (decades from 1950) + missing flag |
| B_comb_A0c | B_dens_age + log NN separation + canopy 0-9 m + canopy 0-30 m (+ missing flags) + wildland-edge flag + fire-level wind (log p90 hourly 10 m wind) + P(reach) from A0c |
| B_comb_V7 | B_comb_A0c with P(reach) from V7 + log10 landed embers × edge + log10 landed embers × interior |

- Logistic, standardised, L2 C = 1, fitted on the five training fires (Woolsey surveyed-only rule); labels as v6.
- **B1 (primary):** fire-averaged AUC(B_comb_V7) − AUC(B_dens_age), fresh fires: lower 95 % CI > 0.
- **B2 (pre-registered secondary):** AUC(B_comb_V7) − AUC(B_comb_A0c) lower 95 % CI > 0.
- **B3 (reported):** AUC(B_comb_V7) vs the owner's 0.70-0.80 reference.
- The fire-level term cannot change within-fire AUC; it is for Brier and reliability.
- Training LOFO (η_final runs, 6 members; `train/loss_lofo_training_eta_final.json`), fire-averaged AUC: B_dens 0.645, B_dens_age 0.623, B_comb_A0c 0.647, B_comb_V7 0.648. B_comb_V7 − B_dens_age +0.025 (+0.002 to +0.055); B_comb_V7 − B_comb_A0c +0.002 (−0.008 to +0.013). Year built lowers AUC on training (B_dens_age − B_dens −0.021; Tubbs has year built for 0.3 % of units). Per fire, B_comb_V7: Eaton 0.57, Palisades 0.69, Camp 0.73, Tubbs 0.67, Woolsey 0.58. Fire-averaged Brier: B_comb_V7 0.231 vs B_dens 0.217 (worse).

## 8. Bootstrap

2 km spatial super-blocks resampled within each fire, 2,000 draws, seed 20261015, percentile 95 % intervals. A: pooled over the set's blocks. B: per-fire AUC differences averaged over the set's fires in each draw. With two fresh fires a whole-fire bootstrap is not informative; the cluster bootstrap understates between-fire uncertainty, and this is stated.

## 9. Disclosures (seen or decided before fixing this block)

- **Engine fix `2ec1b17`** (2026-10-15, before any held-out run): grid-model point ignitions stalled under strong wind (narrow starting ellipse; C-7, 180 m, 61 km/h burned 2 cells). All v7 runs use it. v6's secondary A0 runs predate it and are used as recorded.
- **Memory fix (2026-10-17, during training stage 2, before any held-out run).** Stage-2 Tubbs runs reached 14.6-16 GB: at extreme intensity the short-range kernel (and the long kernel) extended tens of km beyond the run grid. `LR.kernel(crop=…, return_total=True)` now keeps only offsets a `mode="same"` convolution on the grid can use (exact) and normalises the short kernel by the full kernel's sum as before; the full sum is taken only over the crosswind strip where exp(−c²/2σ_Y²) is not exactly 0.0 (one Tubbs member 2 kernel had a 458 km median and a 2,394 km cap: 172 s per kernel, then 1.3 s). **Equivalence check** against the committed engine (`72b0a76`): (1) kernel level, 300 random kernels (all tails, directions incl. 0/90/180/270°, full and cropped): kernel cells **bit-identical**, normalising sum within 3.4e-16 relative; (2) run level, four stored stage-2 runs re-run with the final fix (Palisades V7 η0 1e-5 κ 1 m5; Tubbs V7 η0 1e-5 κ 1 m1; Tubbs V7 η0 1e-7 κ 1 m2; Camp V7 η0 3e-6 κ 3 m2): arrival, long-range and Albini spot lists and emitted embers **bit-identical**; the `landed` raster differs by ≤ 4.6e-13 (absolute) from FFT rounding (different FFT sizes): identical for cells ≥ 1e-3, ≤ 1e-7 relative for cells ≥ 1e-6, so target B's log10(1 + landed) changes by ≤ 2e-13. Stated tolerance: arrival and spots exact; landed ≤ 1e-12 absolute. Peak memory of any re-run: 0.43 GB (was 14.6-16 GB). Stage-2 runs are pooled across versions: 462 of 540 V7 runs used `72b0a76`; 65 used the first form of the fix (kernel cells within ~1e-19 of `72b0a76` from a changed multiplication order; its own three-run check gave bit-identical arrival, spots and emitted embers); the 13 Tubbs member-2 runs used the final form. Unit tests `test_kernel_crop_is_exact_on_the_grid`, `test_kernel_strip_matches_dense_evaluation`.
- **Camp wind direction** set to JBGC1 only after the all-station mean sent every training run south of Paradise (training-only decision).
- **Stage-1 grid redesigned once** on training results (§4).
- **Fresh observation counts** (counts only): Dixie 275 community blocks, 60 scored (42 two-sided; 41 perimeter, 19 VIIRS upper bounds); Caldor 213, 12 scored (all two-sided).
- **Dixie DINS aggregate class counts** were seen in the 2026-10-10 profiling; nothing in v7 was set from them. Caldor narrative suppression left two short fragments visible (no numbers). The Grizzly Flat RAWS (STEELY FORK) froze at 0 mph from 17 Aug 10Z (fire timing at one point). The data agent knows from public reporting that Greenville and Grizzly Flats were heavily hit; not used.
- **Fresh run windows** were set by the data agent from wind records and incident narratives (`refs/<fire>/v7_config.json`); boxes = the timed perimeter at the window end + 8 km within FRAP + 8 km. DMC/DC use the v4 convention 100/800 (the agent's 1 March recursion gave 255/964 and 656/1033, outside FBP calibration).
- **Secondary fires' v4-v6 results** are known (v6 A0 MAE 10.64 h; B AUC 0.60).
- **LANDFIRE LF2019/LF2020 are no longer served**, so 2018-2021 fires use LF2016 (disturbances between 2016 and the fire are missing, e.g. the 2017 Tubbs scar inside the Glass box).
- Target B training LOFO and the η fit were run before this block on training fires only.

## 10. Rules

Fit only on training fires. No held-out simulation before this block is pushed. No configuration promoted after scoring. Aggregate outputs only: no addresses, no per-building outputs outside research files. FireSim never suggests evacuation tiers.

## References

| Reference | Use | Zotero key |
|---|---|---|
| Storey MA, Price OF, Bradstock RA, Sharples JJ (2020) *Fire* 3(2):10, pp.6-7 | spot-distance check | `MS74WYUD` |
| Cruz MG et al. (2012) *For. Ecol. Manage.* 284:269-285, p.269 | 33 km maximum | `9PGICYK7` |
| Beverly JL, Wotton BM (2007) AMS preprint, eqs 2.02-2.03, p.5 | p_e by fuel category | — |
| Van Wagner CE (1977) hourly FFMC (as `firesim.fwi.calculator.hourly_ffmc`) | hourly FFMC | — |
| Scott JH, Burgan RE (2005) USDA FS RMRS-GTR-153 | FBFM40 groups | not recorded |
| Forestry Canada Fire Danger Group (1992) ST-X-3 | FBP types | — |
| Cruz MG, Alexander ME (2019) *Ann. For. Sci.* 76:44 | shrub surrogate rationale (10 % rule) | not recorded |
| Rollins MG (2009) *Int. J. Wildland Fire* 18:235-249 | LANDFIRE | not recorded |
| Liu T et al. (2024) GOFER, *Earth Syst. Sci. Data* 16:1395 (page not checked) | timed perimeters | not recorded |
| Andrews PL (2009) RMRS-GTR-213 p.58 | 20 ft -> 10 m wind | — |
| Zamanialaei M et al. (2025) *Nat. Commun.* 16:8041 | B realism reference | not recorded |
