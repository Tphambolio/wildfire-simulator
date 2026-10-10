# FireSim frontend

React 19 + TypeScript + Vite 7 + MapLibre GL 5. Development: `npm run dev` (Vite on :3000,
proxying `/api` to the FastAPI backend on :8000). Production bundle: `npm run build`.

## UI text: badges, tooltips, About & sources

Since 2026-10-10 (owner request: a decluttered interface) the panels carry labels, values and
short badges only. Explanations, caveats and literature live in:

- **Tooltips** (`src/components/InfoTip.tsx`: the "i" button, `TipButton`; `Badge.tsx`): open
  on hover (~300 ms), keyboard focus or tap, close on Esc or an outside click, stay open while
  the pointer is over them (WCAG 1.4.13). Plain-text tips are `role="tooltip"` linked by
  `aria-describedby`; tips with links (`interactive`) put `aria-expanded` on the trigger and Tab
  moves into them. They render as manual popovers (top layer), so the scrolling columns do not
  clip them. For a disabled control, put the tip beside its label.
- **`src/content/explanations.ts`**: every tip text and badge label, shared by the tips, the
  About tab and the tests. Change wording there.
- **About & sources tab** (`AboutPanel.tsx`, top bar tab 3): intended use, limits and
  validation, methods and references (links to `docs/` on GitHub), data sources and licences,
  responsible use (RPAS reminder, HFI class caveat), ICS Canada forms, help, model version.

Badges that stay visible because they are safety- or science-essential: "Low one-day skill"
(top bar, opens About ▸ Limits), "Range too narrow", "Model output", "Status set by Planning",
"Illustrative" (spotting, house-to-house spread), "Unsourced" (WUI modifiers), "Exposure, not
ignition", "C-2 generalisation", "D-2 aspen won't burn", FWI and HFI class chips, the
per-asset "verify" flags, "NOT safe — withdraw crews", the outside-burning-period banner,
disabled-control reasons, empty states, legends and the map attribution.

The main run shows a progress bar in the Situation panel (`RunProgress.tsx`; server phases from
the WebSocket `simulation.status` event, `docs/api-reference.md`). After a run, "New ignition"
(beside Run) arms the map for a new ignition; "Clear results" (Situation panel) clears the run.
The ignition marker can be dragged. Moving the ignition keeps weather and FWI the user, a
preset or a scenario set; "Update weather for this location" loads the nearest station's values.

## Testing

Node 22 or later (Vitest 5 needs it).

### Unit tests (Vitest + React Testing Library + jsdom)

```bash
npm test             # run once (CI)
npm run test:watch   # watch mode
```

Tests sit next to the code as `src/**/*.test.ts(x)`; shared helpers are in `src/test/`.

### End-to-end tests (Playwright + axe-core)

```bash
npx playwright install chromium   # once
npm run test:e2e                  # builds, serves with `vite preview` on :4173, runs tests/e2e
E2E_SKIP_BUILD=1 npm run test:e2e # reuse an existing dist/
```

No backend is needed. `tests/e2e/mockApi.ts` mocks the API inside the browser:

- HTTP calls (`POST /api/v1/simulations`, `GET /api/v1/simulations/{id}`, fuel-grid image,
  current weather) are answered with `page.route` from the recorded fixture.
- The simulation WebSocket is intercepted with `page.routeWebSocket` and replays the fixture
  frames as `simulation.frame` events, then `simulation.completed`, as the API does.
  `mockApi(page, { mode: "poll" })` closes the socket instead, to test the polling fallback;
  `{ phases: true }` sends `simulation.status` progress messages before the frames.
- All external requests (map tiles, fonts, Open-Meteo, Nominatim, Overpass) are blocked; tiles
  get a transparent PNG. Chromium runs WebGL on SwiftShader (no GPU needed).

The suite:

| Spec | Checks |
|---|---|
| `smoke.spec.ts` | App loads, ignition by map click, Run, frames replay to "completed", metrics show the final area; polling fallback. |
| `ignition.spec.ts` | Keyboard-only ignition (Tab to the map, arrow keys, Enter, Ctrl+Enter runs); typed DMS coordinates and start date/time (`start_time` with the Edmonton offset, timeline from the start); pasted coordinate pair; hourly forecast sliced from the scenario start. |
| `performance.spec.ts` | 0 WebGL draw calls in 3 s of idle (no spot fires), before and after a run. |
| `a11y.spec.ts` | axe (WCAG 2.x A/AA): fails only on serious/critical violations that are not in `tests/e2e/axe-baseline.json`. After fixing violations, re-record with `UPDATE_AXE_BASELINE=1 npx playwright test a11y` and commit the baseline. |
| `layout.spec.ts` | Reports (does not enforce yet) the number of visible text nodes below 12 px, in the console and as an attachment. |
| `skill-options.spec.ts` | Burning period on by default (request, hour validation, timeline shading, Situation note); evening FFMC spin-up with hourly forecast (records from 17:00 the evening before); RPAS restart with active edges drawn on the map and picked by side (`active_edges`, buffer, observation time); the ignition hint ends after a typed ignition; axe clean. |
| `evac.spec.ts` | Neighbourhoods: no evacuation tier appears after a run until Planning sets one; setting a status from the map popup draws the labelled blue outline and survives reload; arrival-table and picker (keyboard) path; "Status set by Planning" badge; where statuses are saved in a tooltip; axe clean. |
| `ensemble.spec.ts` | Range of outcomes: option tooltip (cost, caveat), progress, P10 extent, member range, "Range too narrow" badge with its tooltip on hover and keyboard focus, P10 clock-time lines, burn probability. |
| `assets.spec.ts` | Critical assets: automatic layers, reached assets and roads in clock time, "Model output" badge with sources and data notes in its tooltip, EOC Console summary badge; axe clean. |
| `structure.spec.ts` | House-to-house spread: opt-in (badge tooltip), counts, chart, map layer, caveat tooltips. |
| `declutter.spec.ts` | About & sources tab (8 sections, GitHub references, version; the low-skill badge opens Limits; hover persistence); keyboard tooltip access (Tab, focus, Esc) and axe with a tooltip open; wording fixes; main-run progress bar (phases, advancing, gone on completion); re-run at a new ignition without reload (New ignition and Move ignition, weather kept, view kept, Clear results). |

### Regenerating the fixture

`tests/fixtures/terwillegar_grass_4h.json` is a real engine run (grass fire west of
Terwillegar on the Edmonton fuel grid, 4 h, 15 min snapshots, W 20 km/h, FFMC 92 / DMC 40 /
DC 300) serialised by the API's own routes in the `cells_mode: "incremental"` format the app requests, plus `fuel_grid_image.json`, `arrival.json` and the 30-member ensemble of the same run (`ensemble.json.gz`, served by the mock only with `mockApi(page, { ensemble: true })`). When the engine or
the frame format changes, regenerate both from the repo root with the engine and API
dependencies installed:

```bash
PYTHONPATH=engine/src:api/src python frontend/tests/fixtures/record_fixture.py
# or, from frontend/:  PYTHON=/path/to/venv/bin/python npm run fixture:record
```

The run is deterministic (same inputs, same bytes). If the file would exceed 3 MB the script
thins `burned_cells` on intermediate frames and records the factor in `_fixture`.
