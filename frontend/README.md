# FireSim frontend

React 19 + TypeScript + Vite 7 + MapLibre GL 5. Development: `npm run dev` (Vite on :3000,
proxying `/api` to the FastAPI backend on :8000). Production bundle: `npm run build`.

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
  `mockApi(page, { mode: "poll" })` closes the socket instead, to test the polling fallback.
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
| `evac.spec.ts` | Neighbourhoods: no evacuation tier appears after a run until Planning sets one; setting a status from the map popup draws the labelled blue outline and survives reload; arrival-table and picker (keyboard) path; axe clean. |

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
