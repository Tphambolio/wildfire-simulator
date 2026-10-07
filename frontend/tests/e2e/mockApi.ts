/**
 * Mock FireSim API for Playwright.
 *
 * - HTTP: page.route answers /api/v1/** from the recorded fixtures written by
 *   record_fixture.py (terwillegar_grass_4h.json, fuel_grid_image.json, arrival.json).
 * - WebSocket: page.routeWebSocket intercepts /api/v1/simulations/ws/{id} in the browser and
 *   replays the fixture frames as `simulation.frame` events, then `simulation.completed`,
 *   exactly as the API's WebSocket endpoint sends them. No server process is needed.
 * - Polling fallback: GET /api/v1/simulations/{id} returns the fixture (status "completed"), so
 *   `mode: "poll"` (WebSocket closed at once) exercises useSimulation's polling path.
 * - Everything outside localhost (map tiles, fonts, Open-Meteo, Nominatim, Overpass) is
 *   blocked; raster tiles get a transparent PNG so the map settles quickly and deterministically.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";
import type { Page, Route } from "@playwright/test";

const FIXTURES = fileURLToPath(new URL("../fixtures/", import.meta.url));

export interface Fixture {
  simulation_id: string;
  status: string;
  config: Record<string, unknown> | null;
  frames: Array<Record<string, unknown> & { time_hours: number; area_ha: number; cells_offset?: number }>;
  error: string | null;
}

export const fixture: Fixture = JSON.parse(readFileSync(FIXTURES + "terwillegar_grass_4h.json", "utf8"));
const fuelGridImage = readFileSync(FIXTURES + "fuel_grid_image.json", "utf8");
const arrival = readFileSync(FIXTURES + "arrival.json", "utf8");
/** The completed 30-member ensemble of the same run (GET /simulations/{id}/ensemble) */
export const ensembleFixture = JSON.parse(gunzipSync(readFileSync(FIXTURES + "ensemble.json.gz")).toString("utf8")) as {
  total: number;
  area_ha: { min: number; p50: number; max: number };
  note: string;
};
const ensembleBody = JSON.stringify(ensembleFixture);

// 1x1 transparent PNG
const BLANK_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

const CURRENT_WEATHER = {
  lat: 53.46,
  lng: -113.66,
  ffmc: null,
  dmc: null,
  dc: null,
  isi: null,
  bui: null,
  fwi: null,
  wind_speed: null,
  wind_direction: null,
  temperature: null,
  relative_humidity: null,
  source: "mock",
  available: false,
  message: "Live weather disabled in e2e tests",
  data_timestamp: null,
};

export interface MockOptions {
  /** "ws": replay frames over the routed WebSocket (default). "poll": close the socket so the app polls GET. */
  mode?: "ws" | "poll";
  /** Delay between replayed frames (ms). */
  frameDelayMs?: number;
  /**
   * Serve the recorded ensemble: "pending" on the first poll, then "running" with `done`
   * rising by `ensembleStep` members per poll, then the completed fixture. Off (default):
   * GET /ensemble answers 404 "No ensemble requested", as the API does for a run without one.
   */
  ensemble?: boolean;
  ensembleStep?: number;
}

export interface MockState {
  posts: Array<Record<string, unknown>>;
  wsConnections: number;
  polls: number;
  ensemblePolls: number;
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: typeof body === "string" ? body : JSON.stringify(body) });

export async function mockApi(page: Page, opts: MockOptions = {}): Promise<MockState> {
  const mode = opts.mode ?? "ws";
  const frameDelayMs = opts.frameDelayMs ?? 40;
  const state: MockState = { posts: [], wsConnections: 0, polls: 0, ensemblePolls: 0 };
  const ensembleStep = opts.ensembleStep ?? 8;

  // External requests: tiles get a blank PNG, everything else is aborted
  await page.route(
    (url) => url.hostname !== "localhost" && url.hostname !== "127.0.0.1",
    (route) => {
      const req = route.request();
      if (req.resourceType() === "image" || /tile|\.png|\.jpg/.test(req.url())) {
        return route.fulfill({ status: 200, contentType: "image/png", body: BLANK_PNG });
      }
      return route.abort("blockedbyclient");
    },
  );

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (path === "/api/v1/simulations/fuel-grid-image") return json(route, fuelGridImage);
    if (path === "/api/v1/weather/current") return json(route, CURRENT_WEATHER);
    if (path === "/api/v1/simulations" && req.method() === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.posts.push(body);
      return json(route, { simulation_id: fixture.simulation_id, status: "running", config: fixture.config, frames: [], error: null });
    }
    if (path === `/api/v1/simulations/${fixture.simulation_id}/arrival`) return json(route, arrival);
    if (path === `/api/v1/simulations/${fixture.simulation_id}/ensemble`) {
      if (!opts.ensemble) return json(route, { detail: "No ensemble requested for this run" }, 404);
      const n = ++state.ensemblePolls;
      const total = ensembleFixture.total;
      if (n === 1) return json(route, { status: "pending", done: 0, total });
      const done = (n - 1) * ensembleStep;
      if (done < total) return json(route, { status: "running", done, total, error: null });
      return json(route, ensembleBody);
    }
    if (path === `/api/v1/simulations/${fixture.simulation_id}` && req.method() === "GET") {
      state.polls++;
      return json(route, fixture);
    }
    if (path === "/api/v1/health") return json(route, { status: "ok" });
    return json(route, { detail: `not mocked: ${req.method()} ${path}` }, 404);
  });

  await page.routeWebSocket(/\/api\/v1\/simulations\/ws\//, (ws) => {
    state.wsConnections++;
    if (mode === "poll") {
      ws.close({ code: 1011, reason: "mock: force polling" });
      return;
    }
    const simId = ws.url().split("/").pop();
    let i = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const next = () => {
      if (i < fixture.frames.length) {
        ws.send(JSON.stringify({ type: "simulation.frame", simulation_id: simId, frame: fixture.frames[i++] }));
        timer = setTimeout(next, frameDelayMs);
      } else {
        ws.send(JSON.stringify({ type: "simulation.completed", simulation_id: simId }));
      }
    };
    ws.onClose(() => clearTimeout(timer));
    timer = setTimeout(next, frameDelayMs);
  });

  return state;
}

/** Wrap WebGL draw calls so tests can count map redraws (from the UI audit's runtime.py). */
export const DRAW_COUNTER_INIT = `
(() => {
  window.__draws = 0;
  const wrap = (proto) => {
    for (const fn of ['drawElements', 'drawArrays']) {
      const o = proto[fn];
      proto[fn] = function (...a) { window.__draws++; return o.apply(this, a); };
    }
  };
  if (window.WebGL2RenderingContext) wrap(WebGL2RenderingContext.prototype);
  if (window.WebGLRenderingContext) wrap(WebGLRenderingContext.prototype);
})();
`;
