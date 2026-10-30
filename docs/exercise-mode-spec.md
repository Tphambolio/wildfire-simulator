# Exercise mode: specification (2026-10-25)

**Naming.** This document calls the feature **exercise mode**, a mode of the app, and coins no
product name for it: a trademark search (owner, 2026-10-29) found that "FireSim" itself collides
with existing products, so any product or tier name is an owner decision taken outside this spec.

**Status:** design spec plus PR0 (feature-flagged skeleton). Backlog item E6 ("design spec first")
and strategy-brief item 8 ("an exercise/replay mode before the 2027 season"). Nothing in this
document changes the engine or any model behaviour; exercise mode is a way of *driving* the
existing app against a controlled clock, with the owner's framing rules enforced by the product.

**Why first.** FireSim is positioned as a preparedness and training tool (held-out one-day F1
0.246 on the pre-fire grid, level with WISE defaults; ensemble coverage 49 % and over-confident;
[`OVERVIEW.md`](OVERVIEW.md) §3). That is the right skill level for an *exercise*, where the
fire is a scripted stimulus and the thing being evaluated is the EOC's process, not the model.
Alberta's Local Authority Emergency Management Regulation (AR 203/2018) requires every local
authority to run an annual tabletop exercise and a functional exercise at least every four
years; the commercial path ([business plan, 2026-10-24](PROJECT_RECORD.md#3-decisions-log)) is a
paid exercise-mode tier and facilitated exercises for municipal EOCs, First Nations and
regional districts. (The owner should confirm the regulation's current section numbers and
wording before any of this appears in sales material; this spec does not quote it.)

**Open questions for the owner are in §13. This spec lists them and does not decide them.**

---

## 1. Goals and non-goals

### Goals

1. A facilitator can run a wildfire EOC exercise from FireSim alone: scripted fire, scripted
   weather, controlled clock, injects, hidden forecast, a 209-WF per operational period and a
   hot-wash export, without touching the model or writing code.
2. Players (Planning, Operations, the EOC Director) use the app they already know. Exercise
   mode adds a clock and a banner; it removes nothing and changes no number.
3. Everything the players produce (209 drafts, decisions, annotations) and everything the
   controller did (injects, clock jumps, reveals) is logged in exercise time and exported for
   the after-action report.
4. The exercise is unmistakably an exercise on every screen and every export.
5. Scenario packs are reusable, shareable JSON built from open data only.

### Non-goals (this spec)

- **No new fire science.** The exercise uses the engine, fuel grids, weather chain and ensemble
  exactly as they are. Its skill limits are stated on the hot-wash export, as everywhere else.
- **No evacuation-tier suggestions, no per-building results, no addresses** in any exercise
  view or export (owner's hard rules, §10). The exercise does not "test" whether the EOC chose
  the right tier against a FireSim answer; FireSim has no answer to give.
- **No scoring of players.** The evaluator view shows the timeline of decisions against the
  fire state; it does not grade them. Evaluation is the evaluator's job (ICS Canada / AEMA
  exercise evaluation guides), not the tool's.
- **No real-time data in an exercise.** Live CWFIS/Open-Meteo weather is replaced by the pack's
  weather script. An exercise never mixes a live forecast with a scripted fire.
- **No multi-tenant accounts, billing or SSO** in PR0 to PRn below (§13 Q4).
- **No change to the saved-runs passphrase or to any existing endpoint's behaviour.**

---

## 2. Personas

| Persona | Needs | Sees |
|---|---|---|
| **Facilitator / controller** (exercise director, often the municipality's DEM or a FireSim-facilitated consultant) | Load a pack, start the clock, pause and jump it, push injects on cue, see ground truth, reveal the hidden forecast at the hot-wash, export the record | Everything: exercise clock controls, truth weather, scheduled and ad-hoc injects, players' 209 drafts |
| **EOC Planning Section Chief / Situation Unit** (primary FireSim user today) | Read the modelled fire at exercise time, draft the 209-WF each period, set evacuation status as decided, annotate the map | The player view: exercise clock (read-only), the run as modelled up to exercise time, the forecast valid at exercise time |
| **Operations Section Chief** | Resource picture, ICS 204/205 forms, what the injects changed (resource availability) | Player view plus the resource injects as they arrive |
| **EOC Director** | The 209 summary, the decision log, the "how to read the range" card (backlog E7) | Player view; the EOC Summary card |
| **Evaluator** (AEMA / mutual-aid partner / internal) | A timeline of what the fire did versus what the EOC decided and when, the inject log, each period's 209 snapshot | Evaluator view (read-only), hot-wash export |

Players and controller may be in **one browser** (projected tabletop, PR0 to PR4) or in
**separate browsers sharing a server-side session** (functional exercise, PR5; owner question
§13 Q1).

---

## 3. How exercise mode relates to the existing app

Exercise mode is a *layer over* three things that already exist:

| Existing | Exercise mode use |
|---|---|
| A run's timeline (`TimeSlider.tsx`; frames at `time_hours` after `scenarioStart`) | The exercise clock is an instant on that timeline. The frame shown is the last frame at or before exercise time. The slider's own play/speed controls hide; scrubbing seeks the exercise clock. |
| Multi-day runs and operational periods (`MultiDayPanel.tsx`, `OperationalPeriodPanel.tsx`, `useIncident.ts`) | "Next period" jumps the clock to the next operational-period boundary. Each boundary triggers an auto-drafted 209-WF (§8). |
| RPAS perimeter restart (`POST /perimeter-override`, `PerimeterOverridePanel.tsx`) | A **perimeter inject** is a perimeter-override restart whose perimeter the controller supplies, arriving at a set exercise time as an "observed perimeter" (§6). |
| Weather schedule (`weather_schedule`, `WeatherSchedulePanel.tsx`) and hourly forecast stream (`hourly_weather`) | The pack's weather script becomes the run's `weather_schedule` (ground truth); the players' forecast is a separate, possibly different script shown as the forecast (§7). |
| ICS Canada 209-WF (`utils/ics209.ts`, [`ics-canada-209.md`](ics-canada-209.md)) | Auto-drafted per period from the run at the boundary; players edit; snapshots kept (§8). |
| Run archive and saved runs (`services/archive.py`, `routers/runs.py`, [`run-archive.md`](run-archive.md)) | Packs and exercise sessions are stored beside runs, same volume, same retention, separate passphrase (§11). |
| Incidents (`useIncident.ts`, localStorage) | An exercise session is the server-side successor of an incident for exercise use (backlog E5 noted this gap); the single-browser mode keeps using the local incident. |

---

## 4. Exercise clock

A controller-driven replay clock, decoupled from wall time. Players see it as **"Exercise time"**;
the top-bar Edmonton wall clock stays visible so nobody confuses the two.

**State** (`frontend/src/types/exercise.ts` `ExerciseClockState`; reducer
`frontend/src/exercise/clock.ts`, PR0):

- `enabled`, `mode` (`paused` | `playing`), `rate` (1 | 4 | 12 exercise seconds per wall second),
  `exerciseMs` (an instant on the run's own timeline), `startMs` / `endMs` (the span the clock can
  show), `periodBoundariesMs` (operational-period boundaries, ascending).
- **Actions:** `enable`, `disable`, `play`, `pause`, `setRate`, `tick` (wall time elapsed; advances
  by `rate × wall`), `seek`, `nextPeriod`, `setBounds`.
- **Rules:** every change is clamped to `[startMs, endMs]`; reaching the end pauses; `play` at the
  end restarts from the beginning (as the timeline does); `nextPeriod` goes to the first boundary
  after now, else the end, and pauses; `setBounds` keeps the exercise time and clamps it (so a
  new run while the exercise is on does not move the clock); unchanged bounds return the same
  state (no render loops).

**Span.** With a run on screen the span is its start to its last frame. Without one, 72 h from
now (`DEFAULT_EXERCISE_HOURS`), so a facilitator can start the clock before the first run.

**Period boundaries.** From the open incident's operational periods (each period's start and end
in Edmonton time) when an incident is open; else every 12 h (the default 08:00-20:00 period
length).

**Rates.** 1× is a functional exercise in real time; 4× and 12× compress a 12 h period into
3 h or 1 h for a tabletop. The owner may want 2× and 24× too (§13 Q6).

**Ticking** (`useExerciseClock.ts`): one 250 ms wall interval while playing, measuring the wall
delta rather than assuming it, so a throttled background tab still advances by the wall time
that really passed. Pausing the browser pauses the exercise, which is what a facilitator expects.

**Display.** The banner (§10) shows `Exercise time 11:30 MDT · Wed, 15 Jul · T+1:30 · Running 4×`.
The timeline readout says "Exercise time" and shows the exercise instant, which may fall between
two frames. The map, Situation panel and EOC console show the last frame at or before it. The
banner's clock is `aria-live="off"`: it changes every tick and must not be read out.

**Multi-day runs.** The clock runs through day boundaries exactly as the timeline does; the
`D2 03:32` convention of the timeline and map labels is unchanged. Nothing in the clock depends
on the FWI carry-over; it only chooses which frame to show.

---

## 5. Scenario packs

A pack is one JSON file (`ScenarioPack`, `types/exercise.ts`; schema `firesim.exercise.pack/1`)
that fully describes a repeatable exercise:

| Field | Meaning |
|---|---|
| `title`, `id`, `sources[]` | Name and the open-data sources with licences, printed on every export |
| `startIso`, `durationHours` | Exercise day-1 start (with offset) and length (≤ 72 h, the multi-day limit) |
| `ignitions[]` | One or more `{lat, lng, hoursFromStart}`; ignitions after 0 h are delivered as spot-fire injects |
| `weatherScript[]` | Ground truth, steps `{hoursFromStart, wind, direction, T, RH, precip}`, sent as the run's `weather_schedule` |
| `playerForecast[]` | What players are shown as "the forecast"; omitted = same as the script (§7) |
| `startingCodes` | FFMC / DMC / DC at the start (Pyra chain values the pack author chose, or historical) |
| `fuel` | `city_lidar` (Edmonton), `ab_wildfire` (elsewhere in Alberta) or `uniform` with a fuel type |
| `periods[]` | Operational periods in exercise time (number, start, end) |
| `scheduledInjects[]` | Injects fixed in advance (§6); the controller adds more live |
| `prePlacedAssets` | GeoJSON FeatureCollection of ICS symbols (`types/incident.ts` symbol keys): ICP, staging, reception centre... |
| `notesForFacilitator` | Free text (objectives, expected decision points) |

**Storage.** Packs are stored alongside saved runs (§11): object `packs/<id>.json`, index row
in the archive SQLite. A pack is small (tens of kB without a perimeter inject; a perimeter
inject carries a GeoJSON polygon). Packs are also importable/exportable as files (the incident
export/import pattern of `IncidentPanel.tsx`), so a facilitator can carry one on a USB stick to
a venue without connectivity to the FireSim server, provided the API is reachable for runs.

**Loading a pack** sets Setup from it (ignition, start time, codes, fuel source, weather
schedule), opens an incident with its periods, draws pre-placed assets on the Situation layer,
enables the exercise clock at `startIso`, paused, and schedules its injects. It does **not**
start the run; the facilitator presses Run (so the pre-brief can happen on a blank map), or the
pack says `autoRun: true` (PR2 option).

**Two example packs** (open data only; both are *skeletons*: coordinates and codes to be
confirmed by the owner before they ship, and neither claims to be a prediction):

1. **Edmonton river valley: Terwillegar Park grass fire** (`data/exercise-packs/edmonton-terwillegar.json`,
   PR2). Ignition in the Terwillegar Park grass near the river (about 53.468 N, 113.617 W, the
   ignition of the existing E2E fixture `terwillegar_grass_4h`), a July afternoon, 2 periods of
   12 h, City LiDAR fuel grid, SW wind 20 km/h rising to 35 km/h in the script while the players'
   forecast keeps 20-25 km/h (divergence revealed at the hot-wash), a perimeter inject at T+2 h
   from a drawn polygon ("RPAS flight 1"), a resource inject at T+3 h (mutual-aid engines
   delayed). Sources: City of Edmonton LiDAR fuel grid and neighbourhoods (OGL – Edmonton),
   OSM assets and roads (ODbL), NRCan terrain (OGL – Canada). Note for the author: the City
   grid has no accuracy assessment ([`OVERVIEW.md`](OVERVIEW.md) §3), say so on the pack.
2. **Alberta hamlet: Wandering River, Athabasca County** (`data/exercise-packs/ab-wandering-river.json`,
   PR2). A boreal hamlet on Highway 63 (coordinates from `frontend/public/alberta/places.geojson`,
   OGL – Alberta / OGL – Canada), spring (leaf-off, D-1 / M-1), Alberta Wildfire 2025 fuel grid
   (OGL – Alberta), MRDEM-30 terrain, Microsoft footprints (ODbL; uneven coverage, newer houses
   missing, stated on the pack), 3 periods over 36 h, a wind shift inject from SE to NW at
   T+20 h with the forecast hidden, spot-fire inject across the highway at T+22 h, a perimeter
   inject at T+26 h. The Alberta grid maps little vegetation inside towns and treats cropland
   as non-fuel ([`fuel-grid-alberta.md`](fuel-grid-alberta.md)): the pack notes both. No
   addresses; the hamlet polygon and its name only.

Both packs are open data by default (§13 Q3). Neither includes any SFOC number or condition.

---

## 6. Injects

An inject is something the controller pushes into the exercise at an exercise time. Each kind
maps onto a capability the app already has, so no new model behaviour is needed:

| Kind | What the players get | How it is delivered | API |
|---|---|---|---|
| **Perimeter** (`perimeter`) | "Observed perimeter at HH:MM" drawn on the map, as if from an RPAS flight; Setup → Observed perimeter is pre-loaded with it | A `POST /perimeter-override` from the current run at `exerciseTimeMs` with the inject's polygon (and optional active edges); the restart run becomes the run on screen from that time on, exactly like today's RPAS restart. The controller may use a polygon drawn in the app, a pack file, or (for a divergence drill) the truth run's own perimeter at a later time | existing |
| **Spot fire** (`spot_fire`) | A new ignition marker and a sentence in the inject feed | PR3: a second run (`POST /simulations` at the inject time) shown as a second perimeter layer; merging fronts is engine work and out of scope. PR3 may instead deliver it as a perimeter inject whose polygon is the union; the spec leaves the choice to PR3's design note | existing |
| **Wind shift** (`wind_shift`) | The forecast shown to players changes at delivery (if `hiddenFromForecast`) and the fire follows the pack's truth script | Nothing to run: the truth script already has it. Delivery flips the players' forecast view (§7) and logs it | none |
| **Resource availability** (`resource`) | Text in the inject feed and a pre-filled line in the 209-WF block 43-47 area for the players to accept or edit | Text only | none |
| **Controller note** (`note`) | Text in the inject feed | Text only | none |

**Timestamps.** Every inject carries `exerciseTimeMs` (when players receive it) and
`createdAtIso` (wall clock, when the controller made it). Scheduled injects are **delivered**
when the exercise clock reaches their time (while playing, or on a seek/jump that passes them; a
jump past several delivers them in order). The controller can **withdraw** a scheduled inject;
delivered ones stay in the log. Delivery is idempotent (`markDelivered` only acts on `scheduled`).

**Inject log** (`InjectLog`, `exercise/injects.ts`, PR0): sorted by exercise time; statuses
`scheduled` → `delivered` | `withdrawn`. PR0 ships the data structure, pure functions and an
empty panel ("Exercise injects" in Setup, exercise mode only). PR3 adds the controller's
"Add inject" form and the delivery effects.

**Controller-only.** In the single-browser mode the controller and players share a screen, so
the inject form sits in a controller drawer that the facilitator opens and closes (PR3). In the
shared-session mode (PR5) the form exists only in the controller role.

---

## 7. Hidden forecast

Players should see only the forecast **valid at exercise time**, and only what a forecaster would
have said, not what the fire will do.

- **Truth** = the pack's `weatherScript`, sent to the engine as `weather_schedule`. The run on
  screen is computed from it at once (a run is one POST; the engine is not re-run as the clock
  advances), but players only ever see frames up to exercise time, so the future is hidden by
  the clock.
- **Players' forecast** = `playerForecast` when given, else the truth script. The Weather & FWI
  panel in exercise mode shows a read-only "Forecast (exercise)" table of the steps from the
  exercise start, and marks the current step. A step whose `hoursFromStart` is beyond exercise
  time **plus the forecast horizon** is not shown (horizon default 36 h; pack-settable).
- **Divergence** (truth ≠ players' forecast, for example a wind shift the forecast missed) is
  the main exercise design tool. A `wind_shift` inject with `hiddenFromForecast: true` updates the
  players' forecast at its delivery time ("the 14:00 forecast update now says..."), so the EOC
  experiences a forecast change, as in a real event.
- **Controller view** shows both scripts side by side, the divergence steps highlighted.
- **Reveal at the hot-wash.** "Reveal truth" is a controller action, logged, that shows the
  truth script and the whole run to everyone. Until then the Situation panel's horizon badges and
  the 209's 12/24/48/72 h projections are **computed from the players' forecast run**, not the
  truth run, see below.

**Two runs or one?** The simplest honest design is **two runs**: a *truth run* (truth script,
controller only) and a *players' run* (players' forecast script), both started from the pack at
exercise start. Players see the players' run through the clock; the perimeter inject takes its
polygon from the truth run at the inject time (or a drawn polygon), and the restart from it uses
the players' forecast from then on. The 209 projections come from the players' run. At the
reveal both are shown. When `playerForecast` is omitted the two runs are identical and only one
is started. Cost: two runs per exercise, both archived, both on the Fly machine's one shared CPU
(E10 notes: minutes each at the throttled rate; packs should prefer ≤ 36 h or be run ahead and
loaded from the archive, which the pack supports with `runIds`).

**What the hidden forecast is not.** It is not a test of the model: the "truth" is scripted, and
the point is how the EOC handles a forecast change, not whether FireSim predicted it.

---

## 8. 209-WF per operational period

- At each operational-period boundary (the clock reaching `periods[n].endIso`, or the controller
  pressing "Next period"), the app **auto-drafts** an ICS Canada 209-WF from the players' run at
  that time, exactly as the EOC console does today (`buildICS209HTML`, model-output blocks
  tagged, blocks 9 and 28 left empty for the user, [`ics-canada-209.md`](ics-canada-209.md)).
- Players edit the draft in the form viewer as now. "Submit 209" freezes a **snapshot**
  (`ExerciseSession.snapshots209[]`: period, exercise time, HTML) and the next period starts a
  new draft pre-filled from the previous one where the form carries over (incident name, number,
  commander) and from the run where it is model output.
- Snapshots are stored on the session (server, §11) and exported with the hot-wash pack.
- Every page of every exercise 209 carries the **EXERCISE** watermark and the run stamp
  (run ID, model version, git SHA) exactly as now.
- The 209 **never** fills block 9 (status), block 28 (observed behaviour), evacuation tiers or
  any per-building row; this is unchanged.

---

## 9. Evaluator view and hot-wash export

**Evaluator view** (read-only tab or role, PR4): one vertical timeline in exercise time with
three lanes:

1. **Fire state** (model output): area, head ROS, HFI class and communities within 500 m at
   each frame (the Situation panel's numbers), from the players' run and, after the reveal, the
   truth run.
2. **Injects**: delivered, with the kind and title; withdrawn ones greyed.
3. **Decisions**: the players' recorded decisions (evacuation status set in the Neighbourhoods
   card, objectives entered on the 202, 209 submissions, annotations added), each at the exercise
   time it was made, plus free-text decision notes the evaluator types during play
   (`ExerciseSession.decisions[]`, with a role label).

It shows, it does not grade. No "should have" text is generated.

**Hot-wash export** (PR4): one self-contained HTML (printable to PDF from the browser, as the
ICS forms are today; no server-side PDF) containing: the pack summary and sources; the exercise
timeline (three lanes); the inject log; the 209 snapshots per period; the truth versus players'
forecast comparison; the run stamps; the FireSim limits paragraph (one-day skill, ensemble
over-confidence, fuel-grid caveats) copied from About & sources; and the EXERCISE watermark on
every page. It contains no per-building results and no addresses, by construction (it is built
from the same data as the 209 and the Situation panel, which never have them).

---

## 10. Safety and framing

These are product rules, enforced in code and tested, not guidance:

1. **Exercise banner always visible** while exercise mode is on, on every tab (Simulation, EOC
   Console, About), with the EXERCISE mark and the exercise clock (PR0, `ExerciseBanner.tsx`;
   it shares the top grid area and grows `--topbar-height` so drawers move with it).
2. **"EXERCISE" watermark** on every export made while exercise mode is on: 209-WF, ICS forms,
   IAP package, GeoJSON/KML (as a `firesim_exercise: true` property and a feature name prefix),
   the hot-wash export, printed Situation pages (PR4 for exports; PR0 banner only).
3. **No evacuation-tier suggestions**, in exercise mode as everywhere (owner decision 2026-10-07).
   Injects cannot carry a tier; the inject kinds are closed (`InjectKind`).
4. **No per-building results and no addresses** in any exercise view or export. The involved
   units of house-to-house spread stay a map-only, "Illustrative" layer as decided on 2026-10-10;
   the hot-wash export does not read them.
5. **No SFOC numbers or condition IDs** anywhere (public repo rule). A perimeter inject says
   "observed perimeter"; it does not name a flight authorisation.
6. **Model limits stated**: the hot-wash export and the pack loading screen repeat the
   one-line skill statement and link to About & sources; the "Low one-day skill" badge stays
   in the top bar.
7. **Exercise data is exercise data**: sessions and packs are stored under their own prefix and
   never listed as saved runs; exercise runs carry `label` prefixed `EXERCISE:` so the saved-runs
   list shows them for what they are.

---

## 11. Data model, API, storage, auth

### 11.1 Types (TypeScript; `frontend/src/types/exercise.ts`, PR0)

- `ExerciseClockState`, `ExerciseClockAction`, `ExerciseRate` (§4).
- `Inject`, `InjectKind`, `InjectStatus`, `InjectPayload` (one interface per kind), `InjectLog` (§6).
- `WeatherScriptStep`, `ScenarioPackIgnition`, `ScenarioPackPeriod`, `ScenarioPack` (§5).
- `ExerciseSession`: `packId`, `clock` (last saved), `runIds[]` (the exercise's runs in order, run
  archive IDs), `injectLog`, `snapshots209[]`, `decisions[]`.

The Pydantic mirrors (`api/src/firesim_api/schemas/exercise.py`) come with PR2; the rule of
`CLAUDE.md` applies (keep the schema, `docs/api-reference.md` and the TS types in step).

### 11.2 API (PR2 onwards; nothing in PR0)

All under `/api/v1/exercise`, all behind the **facilitator passphrase** (§11.4) unless noted.

| Method | Path | Purpose |
|---|---|---|
| GET | `/exercise/packs` | List packs (id, title, start, duration, sources, created) |
| POST | `/exercise/packs` | Store a pack (validated `ScenarioPack`; ≤ 2 MB) |
| GET | `/exercise/packs/{id}` | One pack (**no passphrase**: a session's players need it; pack IDs are 32-hex like run IDs) |
| DELETE | `/exercise/packs/{id}` | Remove a pack |
| POST | `/exercise/sessions` | Create a session from a pack; returns the session with its ID |
| GET | `/exercise/sessions/{id}` | Session state (**no passphrase**, same reasoning as runs by ID) |
| PATCH | `/exercise/sessions/{id}` | Append-only updates: clock save, inject add/withdraw/deliver, 209 snapshot, decision note, reveal. The server appends to an event list and rebuilds the state; nothing is overwritten |
| GET | `/exercise/sessions/{id}/export` | The hot-wash HTML (PR4) |
| WS | `/exercise/sessions/ws/{id}` | PR5 only: clock and inject events pushed to players' browsers (§13 Q1) |

The runs an exercise starts use the **existing** `POST /simulations`, `/multiday`,
`/perimeter-override` unchanged, with `label` `EXERCISE: <pack title> · <period>`.

### 11.3 Storage (reuse the run archive)

Same volume, same `archive.py` object store and SQLite index, two new prefixes:
`exercise/packs/<id>.json` and `exercise/sessions/<id>/events.jsonl` (append-only) plus
`state.json` (rebuilt). Two new index tables (`exercise_packs`, `exercise_sessions`), created
with `CREATE TABLE IF NOT EXISTS` as `run_labels` was. Retention follows the archive's
(`FIRESIM_ARCHIVE_MAX_AGE_DAYS`, 365 by default); sessions reference runs by ID and survive a
run's deletion with "run no longer on the server", as the EOC console does today. Byte cap:
sessions count toward `FIRESIM_ARCHIVE_MAX_BYTES`. The volume is 1 GB with an 800 MB cap, so a
pack with a large perimeter polygon should be simplified client-side (≤ 2 MB rule above).

### 11.4 Auth

- **Facilitator passphrase** `FIRESIM_EXERCISE_PASSPHRASE` (Fly secret, owner-set), header
  `X-FireSim-Exercise-Passphrase`, compared like the saved-runs one (SHA-256 of both sides,
  `hmac.compare_digest`, per-IP backoff reusing `routers/runs.py`'s limiter), kept in
  `sessionStorage` only. **Separate** from `FIRESIM_RUNS_PASSPHRASE` so a facilitator need not
  be given the run-list passphrase and the saved-runs passphrase does not open exercises.
- Unset → every passphrase-gated exercise endpoint answers 403 (disabled), as the run list does.
- Players need no passphrase: they open a session by ID (PR5) or share the facilitator's browser.
- Per-customer accounts are a product question (§13 Q4), not part of this build plan.

---

## 12. Build plan (PR by PR) with acceptance tests

Each PR keeps `npm run build` at zero errors and adds Vitest (and, where it touches a flow,
Playwright) tests. Nothing changes the engine. The feature flag stays until PR5; the owner
decides when to turn it on by default.

| PR | Scope | Acceptance tests |
|---|---|---|
| **PR0** (this PR) | Feature flag (`VITE_FIRESIM_EXERCISE=1` or `?exercise=1`, default off); exercise clock state (`clock.ts` reducer, `useExerciseClock.ts`); "Exercise mode" toggle in the top bar (flag only); persistent banner with the EXERCISE mark, exercise clock and controls (pause / play, 1× 4× 12×, next period, exit); the timeline's "Exercise time" readout following the clock, its own play controls hidden; the map and panels showing the frame at exercise time; inject log data structure, pure functions and an empty "Exercise injects" panel; TS types for packs and sessions. No API change. | Vitest: reducer (enable, play/tick/rate, clamp and pause at the end, seek, nextPeriod, setBounds no-op), `frameIndexForExerciseTime`, inject log (add/sort/due/deliver/withdraw), flag parsing (env "1" only, `?exercise=1` only), hook gating (flag off → enable is a no-op), TopBar without the prop renders nothing exercise-related, TimeSlider shows "Exercise time" and hides play controls only with `exerciseTimeMs`, banner renders nothing while off, inject panel empty state. Build zero errors; full suite green. |
| **PR1** | Playwright E2E of PR0 behind the flag: toggle on, banner visible on all three tabs, clock plays at 12× and the map frame advances, "Next period" jumps to the incident's period end, exit restores the normal timeline; axe check of the banner's contrast; flag off → no toggle in the DOM. | The E2E spec passes against the mocked API fixture; a11y baseline unchanged. |
| **PR2** | Scenario packs: `ScenarioPack` Pydantic schema, `packs/` endpoints and storage, facilitator passphrase, pack import/export as files, "Load pack" in Setup (exercise mode), the two example packs (open data, skeleton values confirmed by the owner), pack loading sets Setup, the incident periods, the clock span and boundaries, and schedules injects. `docs/api-reference.md` updated. | API tests: 403 without the secret, 401 wrong, 429 backoff, round-trip of a pack, 413/422 on an oversize or invalid pack, IDs 32-hex. Vitest: pack → Setup mapping, period boundaries. E2E: load the Terwillegar pack, Run, banner span equals the pack duration. |
| **PR3** | Injects: controller drawer with the "Add inject" form (kind, exercise time, payload), scheduled-inject delivery on clock advance and jumps (in order, idempotent), perimeter inject = perimeter-override restart at exercise time (reuses `buildOverrideRequest`), wind-shift inject flips the players' forecast table, resource/note injects in the feed and 209 pre-fill, withdraw. Inject log panel lists all with status. | Vitest: delivery order over a jump across several injects; perimeter inject builds the same request `PerimeterOverridePanel` would; withdrawn never delivers. E2E: a perimeter inject at T+2 h restarts the run and the inject log says delivered at 12:00. |
| **PR4** | Hidden forecast (players' forecast table with horizon, divergence highlight for the controller, two-run design when `playerForecast` differs, reveal action logged); 209 auto-draft at period boundaries with snapshots; evaluator view; hot-wash HTML export with the EXERCISE watermark on every page; EXERCISE watermark on all exports made in exercise mode (209, ICS forms, IAP, GeoJSON/KML property). | Vitest: forecast horizon cut-off; divergence detection; 209 draft at a boundary equals `buildICS209HTML` at that frame with the watermark; export contains no `building_exposure_detail`, no `structure_spread_detail`, no address-like fields (a negative test over the HTML); every exported page has the watermark. E2E: two periods → two snapshots in the export. |
| **PR5** | Shared session (if the owner chooses it, §13 Q1): server-side session events, WebSocket push of clock and inject events, roles (controller / player / evaluator) chosen on join, players open by session ID. | API tests for append-only PATCH and event replay; E2E with two browser contexts: controller plays, player's banner follows within one tick; player cannot see the truth script or the inject form. |
| **PR6** | Facilitator pack editor (form over the JSON, perimeter drawing for injects, weather script table), pack validation messages, "run ahead and load from archive" option for slow 72 h packs. | Vitest for validation; E2E save → reload → identical pack. |
| **PR7** | Flag removal / default on (owner decision), About & sources section "Exercise mode", `OVERVIEW.md` §2 line, pricing-relevant limits if any (§13 Q2). | Docs reviewed; E2E without the flag. |

Rough sizes: PR0 done; PR1 done (2026-10-29); PR2 M; PR3 M; PR4 L; PR5 L; PR6 M; PR7 S. PR2 to PR4 are the
minimum for a facilitated tabletop in one browser; PR5 is the functional-exercise case.

---

## 13. Open questions for the owner (listed, not decided)

1. **Multi-player sync.** Same browser projected in the EOC (PR0 to PR4 suffice) or a shared
   server-side session with pushed clock and injects (PR5, roles, WebSocket)? The regulation's
   functional-exercise case suggests the latter eventually; the first paid tabletop may not need it.
2. **Pricing-relevant limits.** Should the free/open tier cap anything (packs stored, sessions
   per month, exercise duration, export) or is the paid tier facilitation and support only? The
   business plan says open-core; the spec has no limits built in.
3. **Packs open by default?** Example packs are open data and could ship in the repo. Should
   customer-made packs be private to their passphrase, shareable by link, or publishable to a
   community library? Pack metadata may name a community; is that sensitive for a First Nation
   or a small hamlet?
4. **Accounts.** One facilitator passphrase per server (as the run list) is enough for the City
   and a handful of pilots. Per-customer tenancy (separate archives, passphrases, retention) is a
   later product decision.
5. **Spot-fire inject design.** Second run shown as a second layer, or a union-perimeter restart
   (§6)? The honest option depends on whether merging fronts is wanted in the engine at all.
6. **Rates.** 1× / 4× / 12× as specified, or also 2× and 24×? Should the controller be able to
   step backwards (replay a decision point) and, if so, does that re-open delivered injects?
7. **Reveal policy.** Reveal only at the hot-wash, or allow a mid-exercise "pause and show truth"
   teaching moment (logged)?
8. **Regulation text.** Confirm the AR 203/2018 exercise requirement wording and section before
   it appears in any FireSim material; this spec deliberately does not quote it.
9. **Retention.** Exercise sessions under the 365-day archive rule, or longer (an after-action
   record is a municipal record)? Export covers the record; the server copy is a convenience.
10. **Evaluator role content.** Should the evaluator view offer AEMA / ICS Canada evaluation
    checklists (text only, no scoring), or stay a timeline?

---

## 14. References

- Backlog E6 and strategy brief item 8 (`~/dev/wildfire/reports/`, internal, 2026-10-18).
- ICS Canada Form 209-WF and instructions: [`ics-canada-209.md`](ics-canada-209.md).
- Run archive and passphrase design: [`run-archive.md`](run-archive.md); API: [`api-reference.md`](api-reference.md).
- Validation status quoted above: [`OVERVIEW.md`](OVERVIEW.md) §3, [`validation.md`](validation.md), [R45], [R49].
- Owner decisions respected here: no evacuation tiers (2026-10-07), per-house display for
  exercises only, never exported (2026-10-10), ICS Canada not NIMS (2026-10-08), shared list behind
  one passphrase (2026-10-12), long run IDs (2026-10-13) ([`PROJECT_RECORD.md`](PROJECT_RECORD.md) §3).
- Alberta Local Authority Emergency Management Regulation, AR 203/2018 (exercise requirement;
  wording to be confirmed by the owner, §13 Q8).
