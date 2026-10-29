# Validation scorecard: a dated, per-release artifact

**What it is.** `docs/scorecard/<date>.json` is a snapshot of FireSim's validation numbers at a
release: held-out spread skill, area bias, the ensemble calibration scores, the structure-spread
test, the long-range ember tests and the FBP equation conformance, each with its 95 % CI where the
report gives one, the reference it is compared against, a one-line plain-English gloss, and the
report number, result file, JSON path and SHA-256 it was read from. `docs/scorecard/README.md`
lists every dated scorecard. The first is `2026-10-25.json` ([R69]).

**Framing (fixed).** FireSim is a preparedness and training tool for EOC Planning sections. It is not
an operational fire-growth forecast and is not for real-time operational prediction; the lead
agency's prediction is authoritative. The scorecard exists so that claim is checked against numbers
at every release, not so a single number can be quoted as "accuracy".

## Where it shows

- **API:** `GET /api/v1/scorecard` returns the latest dated file (by file name) with two added
  fields, `file` and `available_dates`; 404 when the server has none. The directory is
  `FIRESIM_SCORECARD_DIR` (the Docker image copies `docs/scorecard/` to `/app/docs/scorecard`), else
  the repository's `docs/scorecard/`. It is read once at startup (`routers/scorecard.py`).
- **App:** the **Validation** button in the top bar (next to the "Low one-day skill" badge) opens a
  read-only dialog (`frontend/src/components/ValidationScorecard.tsx`): framing, date, commit, engine
  version, report number, then one row per metric (status, value, CI, gloss, report number). Offline
  it keeps the framing and points to `docs/scorecard/`.
- **Docs:** `docs/model-card.md` "Validation scorecard"; `docs/OVERVIEW.md` §3 stays the
  plain-language table and should agree with the latest scorecard.

## How it is made (no validation is run)

```bash
python scripts/make_scorecard.py --date YYYY-MM-DD --report Rnn   # from the repository root
```

The script needs only the standard library and the result files of the numbered reports:

| Metric (id) | Report | File read |
|---|---|---|
| `fbp_cffdrs_conformance` | `docs/verification.md` §1, §1b | the doc itself (figures checked present); tests `engine/tests/fbp/test_cffdrs_reference.py`, `engine/tests/fwi/test_cffdrs_fwi_reference.py` |
| `heldout_f1_17h`, `area_bias_17h` | [R45] | `~/dev/wildfire/reports/data/validation-rescore-backcast-2026-10-15/results_rescore_backcast.json` (`levels.C1.held_out.*`, `paired.C1.held_out.f1_17h`, `wise.means`) |
| `ensemble_coverage_p10_p90`, `ensemble_crps_skill`, `ensemble_spread_skill`, `ensemble_p10_holds_90pct`, `burn_probability_reliability_top_bin` | [R49] | `~/dev/wildfire/reports/data/validation-rescore-backcast-e1-2026-10-17/results_e1.json` (`descriptive.levels.backcast.*`, `direction_aware_reading.*`, `descriptive.pooled_summary_registered.*`) |
| `ensemble_b2_postprocessing` | [R65] | `~/dev/wildfire/reports/data/b2-ensemble-calibration-2026-10-24/results.json` (`arms.P1.*`, `decision`) |
| `structure_hamada_jasper_kappa` | [R10] | `~/dev/wildfire/validation-data/jasper2024/results-2026-10-10/results.json` (`runs[0]`, the pre-registered primary run) |
| `long_range_embers` | [R39] (v4), [R-v5], [R47], [R48]; [R54] (v8, report text) | `prereg_lr4_pooled.json`, `results_lr5_pooled.json`, `scores_PREREG_LR6.json`, `scores_PREREG_LR7_fresh.json` under `~/dev/wildfire/reports/data/long-range-embers-*` (`decision`, `pooled`) |

Rules the script follows:

1. **Read, never re-run.** Values come from the files above. If a file is missing, the value is
   copied from the report text and the metric's `source.kind` is `"report text"`; the file lists
   those ids in `unsourced_from_files`. The first scorecard has none.
2. **Every number is traceable:** `source.report` (the [Rnn] tag), `source.file`, `source.path`
   (the JSON path) and `source.sha256`. Comparators and deltas carry their own `source`.
3. **CIs are the report's.** `ci95` is set only where the report computed one (the [R49] ensemble
   levels are descriptive whole-fire bootstraps; the [R45] F1 test is a paired difference, so the F1
   level has `ci95: null` and the difference's CI sits under `delta`). The one computed quantity is
   `ci95_derived` on the F1 level: a whole-fire bootstrap of `per_fireday.csv` (2,000 draws, seed 1,
   the [R45] scheme), labelled derived with its method and file. `--no-derived` leaves it out.
4. **The grid is named.** Spread and ensemble scores are on the pre-fire back-cast Alberta grid, the
   fuel FireSim uses for dated historical Alberta replays ([R45], [R49]); the CFS 2014b values are
   kept as comparators. `fuel_grid.scope` says live (undated) runs are not scored on observed fires.
5. **`display` is the number as the record quotes it** ("0.246", "49 %", "κ 0.472"); `value` is the
   raw figure rounded to four decimals. The app shows `display`.
6. **Nothing private:** home-relative paths only (`~/dev/wildfire/...`), no addresses, no SFOC
   numbers. The script refuses to write a file containing `/home/`; `api/tests/test_scorecard.py`
   checks the committed files for the same, for the schema and for the headline numbers of the record.

`git_commit` is `HEAD` when the script runs (the code state the numbers describe), or `--commit`.
Because the scorecard is committed after it is built, the commit it names is the one before it.

## Release rule

- **Every tagged release gets a dated scorecard** in `docs/scorecard/`, built on the release commit
  with `--date` = the release date and `--report` = the report that records it. Rebuild it when a
  validation report changes a headline number (new [Rnn] with new result files), even between tags.
- **Old files are never edited or deleted.** A correction is a new dated file; the README table shows
  the history. The frontend and the API show the latest file only.
- **A new scorecard needs an API deploy to be live:** the Fly image copies `docs/scorecard/` at
  build time, and CI deploys only when `api/`, `engine/`, `data/` or `fly.toml` change, so a
  docs-only scorecard commit is served after the next API deploy (or run `flyctl deploy`).
- **Per-release re-score on sealed fires (future).** `docs/SEALED_2026.md` holds a `scorecard` set
  of 16 sealed 2026 fires for a descriptive re-score at each release once a pre-registration is
  committed; they never decide. No model has run on them and the first scorecard uses none.
- The metric list may grow (new ids), but an id's definition never changes; a changed definition is a
  new id and a `schema_version` bump when the top-level shape changes.

## Status words

`validated` (matches its reference: equations), `partly_validated` (scored on observed fires with
real but limited skill), `not_validated`, `failed_test` (missed its pre-registered decision rule),
`pass_opt_in` (passed its pre-registered rule but is not applied by default), `pending`. The
`docs/OVERVIEW.md` §3 status key uses the same words.

## Related

`docs/validation.md` (the protocol and every round), `docs/verification.md` (equations),
`docs/model-card.md`, `docs/PROJECT_RECORD.md` §4.4 (every test, pass / fail / void),
`docs/REPORT_NUMBERS.md` (the [Rnn] register), [R69] *Validation scorecard v1* (the first scorecard's
report, `~/dev/wildfire/reports/`).
