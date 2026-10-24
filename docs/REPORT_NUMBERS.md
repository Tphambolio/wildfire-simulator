# Report-number registry

**How to claim a number.** Before you write a report, take the next free number below, add one line
for it with status `claimed`, and commit that line (on your branch, or on `master` in a small docs PR).
Then write the report. If two branches claim the same number, the one merged to `master` first keeps
it and the other takes the next free number before it merges. Never reuse a number, even when a report
is dropped: mark it `withdrawn`. When the report's PR merges, change the status and keep the line in
step with `docs/PROJECT_RECORD.md` §4.2, which holds the full entry (path, contents, data folder).

Parallel sessions gave [R39] to three reports on 2026-10-12 (see the [R39] lines). This file exists
so that does not happen again.

- **Next free number: [R66].** ([R59] is held for the clock follow-up; [R60] is claimed on
  `feat/front-overspread` (#96), [R62] on #100 and [R63] on #101; those lines are not on `master` yet,
  so they stay out of this table until their PRs merge.)
- **Status values:** `master` = in the record on `master`; `open PR` = the report's PR is not merged;
  `claimed` = number taken before the report is written or recorded; `withdrawn` = not used, never reuse.
- Dates are the report dates. "—" in Branch / PR = research report with no code PR of its own
  (registered in the record by a later docs PR).

| No. | Title | Branch / PR | Date | Status |
|---|---|---|---|---|
| R1 | FireSim EOC best practice review | — | 2026-10-07 | master |
| R2 | Fuel type grids: Canada and global | — | 2026-10-07 | master |
| R3 | Structure ignition in the WUI: a literature review for FireSim | — | 2026-10-06 | master |
| R4 | Structure ignition paper check | `docs/structure-spread-spec`, #37 (basis of the spec) | 2026-10-09 | master |
| R5 | Grass diagonal spread fix validation | `fix/grass-diagonal-spread`, #35 | 2026-10-09 | master |
| R6 | Structure spread: owner decisions and reasoning | `analysis/structure-sensitivity`, #40 | 2026-10-09 | master |
| R7 | Structure spread: sensitivity to the cutoff, contact distance and footprint filter | `analysis/structure-sensitivity`, #40 | 2026-10-09 | master |
| R8 | Structure spread: front contact measured from the building's own cells | `fix/structure-front-contact`, #41 | 2026-10-09 | master |
| R9 | Jasper 2024 building data search | — | 2026-10-09 | master |
| R10 | Jasper 2024: building-by-building check of FireSim structure spread | `analysis/jasper-structure-validation`, #42 | 2026-10-10 | master |
| R11 | Structure spread memory fix | `fix/structure-memory-ui`, #43 | 2026-10-10 | master |
| R12 | FireSim mechanics decisions (M1-M6) | — | 2026-10-10 | master |
| R13 | FireSim mechanics: what the NRES 799 literature says to change | — | 2026-10-10 | master |
| R14 | Grass curing default: validation before and after | `feat/curing-default-and-citations`, #44 | 2026-10-10 | master |
| R15 | FireSim vs Pyra: fire weather, FWI and forecasting comparison | `fix/fire-weather-path`, #47 | 2026-10-10 | master |
| R16 | Structure ember ignition | `feat/structure-embers`, #46 | 2026-10-10 | master |
| R17 | Open-data fuel grid for Edmonton and St. Albert | `feat/open-data-fuel-grid`, #51 | 2026-10-10 | master |
| R18 | WUI 20 m grid | `feat/wui-20m-window`, #52 | 2026-10-10 | master (report header said [R19] until 2026-10-18) |
| R19 | Structure burn-out | `feat/structure-burnout`, #53 | 2026-10-10 | master (report header said [R18] until 2026-10-18) |
| R20 | Re-run and multi-day fixes | `fix/rerun-and-multiday`, #56 | 2026-10-11 | master |
| R21 | Structure step 3: building-level vegetation and roof data | — | 2026-10-10 | master |
| R22 | Roof and building vulnerability from open data | — | 2026-10-10 | master |
| R23 | Structure step 3: vegetation and roof build | `feat/structure-veg-roof`, #57 | 2026-10-10 | master (report header said [R22] until 2026-10-18) |
| R24 | Alberta Wildfire fuel grid default | `feat/ab-wildfire-fuel-default`, #55 | 2026-10-11 | master |
| R25 | Alberta buildings | `feat/alberta-buildings`, #58 | 2026-10-11 | master |
| R26 | Open-data fuel grid full FBP key, with its v2 rework | `feat/fuelgrid-full-fbp-key`, #54 | 2026-10-10 / 11 | master (rework report said [R24] until 2026-10-18) |
| R27 | Open-data fuel grid northern Alberta fire test | `analysis/opendata-grid-fire-test`, #61 | 2026-10-10 / 11 | master |
| R28 | Multi-day, run-to-EOC bridge and storage QA | `test/e2e-multiday-eoc-storage`, #62 | 2026-10-11 | master |
| R29 | Alberta assets roads terrain | `feat/alberta-assets-roads-terrain`, #64 | 2026-10-11 | master |
| R30 | Run archive phase 1 | `feat/run-archive`, #65 | 2026-10-11 | master |
| R31 | Long-range ember ignition: literature review | `feat/long-range-embers`, #59 | 2026-10-11 | master (record entry; PR #59 open) |
| R32 | Long-range ember chain: build and pre-registered Eaton test (v1) | `feat/long-range-embers`, #59 | 2026-10-11 | master (record entry; PR #59 open) |
| R33 | Long-range embers v2: urban source | `feat/long-range-embers`, #59 | 2026-10-11 / 12 | master (record entry; PR #59 open) |
| R34 | ODB municipal buildings and Edmonton box | `feat/odb-municipal-buildings`, #73 | 2026-10-12 | master |
| R35 | Alberta fuel grid decisions | `docs/ab-fuel-grid-decisions`, #70 | 2026-10-12 | master |
| R36 | Hamlets and localities | `feat/hamlets-localities`, #69 | 2026-10-12 | master |
| R37 | Long-range embers v3 | `feat/long-range-embers`, #59 | 2026-10-12 | open PR (#59, held by the owner) |
| R38 | Edmonton fuel grids compared: City LiDAR grid vs open-data grid | `docs/edmonton-grid-licence`, #75 | 2026-10-12 | master |
| R39 | Long-range embers v4 | `feat/long-range-embers`, #59 | 2026-10-12 | open PR (#59). Duplicate number: keep [R39] or rename [R39-embers] at the #59 merge (owner's choice) |
| R39-fuel | Pre-fire fuel grids for historical replays | — | 2026-10-12 | master (an R39 duplicate, given the suffix so existing references stay valid) |
| R40 | Building size filter and licence review | `feat/building-size-licence`, #76 | 2026-10-12 | master |
| R41 | Owner field test: Slave Lake SWF-065 ignition and wind | — (fixes in #78) | 2026-10-12 | master |
| R42 | Owner field test: SWF-065 two-stage replay | — (fixes in #78) | 2026-10-12 | master |
| R43 | Burn-scar fuel bias in validation | `feat/prefire-fuel-grid`, #79 | 2026-10-12 | master |
| R44 | Pre-fire fuel grid build and Slave Lake replay | `feat/prefire-fuel-grid`, #79 | 2026-10-12 / 13 | master |
| R45 | Validation rescored on pre-fire fuel grid | `feat/rescore-backcast`, #84 | 2026-10-15 | master |
| R46 | Chrome tester 72 h rerun (R6) | — (labelling fixes in #86) | 2026-10-17 | master |
| R47 | Long-range embers v6: reframed validation | `feat/long-range-embers-v6`, #83 (also on #87) | 2026-10-13 | open PR |
| R48 | Long-range embers v7: calibrated front | `feat/long-range-embers-v7`, #87 | 2026-10-15 | open PR |
| R49 | Validation ensemble rescored on pre-fire fuel grid | `docs/rescore-backcast-e1-ensemble`, #88 | 2026-10-17 | master |
| R50 | Literature review: new references (with Addendum A) | `docs/record-backlog-2026-10-18`, #90 | 2026-10-12 | master (cited as "[R39] Literature review new references" until 2026-10-18; the third R39 duplicate) |
| R51 | Ember literature systematic review before v8 (with protocol) | `docs/record-backlog-2026-10-18`, #90 | 2026-10-17 | master |
| R52 | Ember literature ML comparison before v8 | `docs/record-backlog-2026-10-18`, #90 | 2026-10-17 | master |
| R53 | Dixie early-arrival diagnostic | `docs/record-backlog-2026-10-18`, #90 (concerns PR #87) | 2026-10-18 | master |
| R54 | v8 ember study design: draft for owner review | `docs/record-backlog-2026-10-18`, #90 | 2026-10-18 | master |
| R55 | Slave Lake replay: arrival at the built-up town, not the census subdivision | `docs/record-backlog-2026-10-18`, #90 | 2026-10-18 | master |
| R56 | Chrome tester multi-day labels #86 live | `fix/field-test-round4`, #91 | 2026-10-18 | master |
| R57 | Alberta permanent UTC−6 switch | `fix/alberta-permanent-utc6`, #94 (Pyra FWI#7) | 2026-10-18 | master |
| R58 | Harness start-type strata baseline (with §7 whole-perimeter rerun) | `feat/harness-start-strata`, #93 | 2026-10-18 | master |
| R61 | v7 held-out arrivals recoded with [R53] rules (A3 descriptive addendum; v8 rule check) | `docs/a3-v7-arrivals-recoded` (record only; report and data in `~/dev/wildfire/reports/`) | 2026-10-24 | open PR |
| R64 | E10 live multi-day run time: throttled shared CPU, a 3-4x faster grid step, honest time left | `perf/e10-live-runtime` | 2026-10-24 | open PR |
| R65 | Ensemble re-calibration with a bias term (B2, PREREG_ENSEMBLE_B2) | `feat/b2-ensemble-calibration` | 2026-10-24 | claimed |

**Not yet numbered.** *Long-range embers v5: two-mode kernel* (2026-10-12, `feat/long-range-embers-v5`,
PR #81, open) calls itself [R-v5], "number assigned at merge"; claim a number here when #81 merges.
Other reports in `~/dev/wildfire/reports/` without a number (for example the reference inventory,
UI declutter, map focus layout, CAL FIRE DINS test data) are not cited by number in the record; give
one only when the record starts to cite them.
