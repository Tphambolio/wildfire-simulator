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
/** A run with structure_spread on (record_fixture.py --structure): counts per frame, involved units on the last */
export const structureFixture: Fixture = JSON.parse(readFileSync(FIXTURES + "structure_spread_4h.json", "utf8"));
/**
 * A recorded 3-day POST /simulations/multiday run (record_fixture.py --multiday): Terwillegar
 * grass, 120 min snapshots, burning period 10-20, rain on day 3; frames carry `day` and the
 * day's carried-over FWI codes `day_fwi`, incremental cells (whole frame at each day start).
 */
export const multidayFixture: Fixture = JSON.parse(readFileSync(FIXTURES + "terwillegar_multiday_3d.json", "utf8"));
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
  /** Replay the house-to-house spread fixture instead of the Terwillegar one */
  structure?: boolean;
  /** Send `simulation.status` messages (loading, spread 0-100 %, finishing) before the frames,
   * one every `phaseDelayMs`, as the API does while the grid model computes */
  phases?: boolean;
  phaseDelayMs?: number;
  /** Keep the ensemble "running" (never completes), as a slow server ensemble does */
  ensembleNeverCompletes?: boolean;
  /** Multi-day runs replay the recorded engine fixture (first N days of it) instead of the
   * synthetic multiDayFrames */
  multidayRecorded?: boolean;
  /** Shift every coordinate of the replayed frames by [dLat, dLng] (runs outside Edmonton) */
  translate?: [number, number];
  /** Each POST /simulations gets its own ID (fixture ID, then `<fixture ID>-2`, `-3`, ...);
   * the same frames are replayed for each */
  distinctRunIds?: boolean;
  /** POST /simulations number n gets runIds[n - 1] (then the default ID); GET, /arrival and
   * /ensemble (404, none requested) answer for these IDs, replaying the same frames */
  runIds?: string[];
}

/** Frames with every coordinate shifted by [dLat, dLng] (perimeter, cells, head, spot fires). */
export function translateFrames(frames: Fixture["frames"], [dLat, dLng]: [number, number]): Fixture["frames"] {
  const pt = <T extends { lat: number; lng: number }>(c: T): T => ({ ...c, lat: c.lat + dLat, lng: c.lng + dLng });
  return frames.map((f) => ({
    ...f,
    perimeter: Array.isArray(f.perimeter) ? (f.perimeter as number[][]).map(([la, ln]) => [la + dLat, ln + dLng]) : f.perimeter,
    burned_cells: Array.isArray(f.burned_cells) ? (f.burned_cells as Array<{ lat: number; lng: number }>).map(pt) : f.burned_cells,
    head: f.head ? pt(f.head as { lat: number; lng: number }) : f.head,
    spot_fires: Array.isArray(f.spot_fires) ? (f.spot_fires as Array<{ lat: number; lng: number }>).map(pt) : f.spot_fires,
    building_exposure: null,
    building_exposure_detail: null,
  }));
}

/** The recorded multi-day frames for a request of `days` days (the fixture has 3). */
export function recordedMultiDayFrames(days: number): Fixture["frames"] {
  return multidayFixture.frames.filter((f) => ((f as { day?: number }).day ?? 1) <= days);
}

/** Simulation ID the mocked POST /simulations/multiday returns */
export const MULTIDAY_ID = "fixture-multiday";

/**
 * Multi-day frames for a request of `days` days: the fixture's final perimeter every 6 h up to
 * days x 24 h, whole frames (no cell offsets), area 1000 + hours. Only the clock span and the
 * area matter to the tests.
 */
export function multiDayFrames(days: number): Fixture["frames"] {
  const last = fixture.frames[fixture.frames.length - 1];
  const out: Fixture["frames"] = [];
  for (let h = 6; h <= days * 24; h += 6) {
    out.push({
      ...last,
      time_hours: h,
      day: Math.ceil(h / 24),
      area_ha: 1000 + h,
      burned_cells: null,
      cells_offset: 0,
      building_exposure: null,
      building_exposure_detail: null,
    });
  }
  return out;
}

export interface MockState {
  posts: Array<Record<string, unknown>>;
  /** POST /simulations/perimeter-override bodies */
  overridePosts: Array<Record<string, unknown>>;
  wsConnections: number;
  polls: number;
  ensemblePolls: number;
  /** POST /simulations/multiday bodies */
  multidayPosts: Array<Record<string, unknown>>;
  /** Simulation ID of every GET /simulations/{id}/ensemble, in order */
  ensemblePollIds: string[];
  /** Simulation ID of every DELETE /simulations/{id}/ensemble (the app stopping an old ensemble) */
  ensembleCancels: string[];
  /** Simulation IDs the "server" no longer has (GET answers 404, as after a restart or deploy) */
  gone: Set<string>;
  /**
   * Simulation IDs the "server" serves from its run archive (after a restart or deploy):
   * GET answers the run's body with `archived: true`, as the API does (docs/run-archive.md)
   */
  archived: Set<string>;
  /** Simulation ID of every GET /simulations/{id} */
  getIds: string[];
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: typeof body === "string" ? body : JSON.stringify(body) });

export async function mockApi(page: Page, opts: MockOptions = {}): Promise<MockState> {
  const mode = opts.mode ?? "ws";
  const fx = opts.structure ? structureFixture : fixture;
  const frameDelayMs = opts.frameDelayMs ?? 40;
  const state: MockState = {
    posts: [], overridePosts: [], wsConnections: 0, polls: 0, ensemblePolls: 0, multidayPosts: [], ensemblePollIds: [],
    ensembleCancels: [], gone: new Set(), archived: new Set(), getIds: [],
  };
  const shift = (frames: Fixture["frames"]) => (opts.translate ? translateFrames(frames, opts.translate) : frames);
  const mdFrames = (n: number) => shift(opts.multidayRecorded ? recordedMultiDayFrames(n) : multiDayFrames(n));
  const fxFrames = shift(fx.frames);
  let multiDays = 3;
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
      const n = state.posts.length;
      const id = opts.runIds?.[n - 1] ?? (opts.distinctRunIds && n > 1 ? `${fx.simulation_id}-${n}` : fx.simulation_id);
      return json(route, { simulation_id: id, status: "running", config: fx.config, frames: [], error: null });
    }
    if (path === "/api/v1/simulations/perimeter-override" && req.method() === "POST") {
      // The restart replays the same fixture frames
      state.overridePosts.push(req.postDataJSON() as Record<string, unknown>);
      return json(route, { simulation_id: fx.simulation_id, status: "running", config: fx.config, frames: [], error: null });
    }
    if (path === "/api/v1/simulations/multiday" && req.method() === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.multidayPosts.push(body);
      multiDays = Array.isArray(body.days) ? body.days.length : 3;
      return json(route, { simulation_id: MULTIDAY_ID, status: "running", config: null, frames: [], error: null });
    }
    const ens = path.match(/^\/api\/v1\/simulations\/([^/]+)\/ensemble$/);
    if (ens && req.method() === "DELETE") {
      state.ensembleCancels.push(ens[1]);
      return json(route, { simulation_id: ens[1], status: "running", cancel_requested: true });
    }
    if (ens) state.ensemblePollIds.push(ens[1]);
    const one = path.match(/^\/api\/v1\/simulations\/([^/]+)$/);
    if (one && req.method() === "GET") {
      state.getIds.push(one[1]);
      if (state.gone.has(one[1])) return json(route, { detail: "Simulation not found" }, 404);
      if (state.archived.has(one[1])) {
        const body = one[1] === MULTIDAY_ID
          ? { simulation_id: MULTIDAY_ID, status: "completed", config: null, frames: mdFrames(multiDays), error: null }
          : { ...fx, simulation_id: one[1], frames: fxFrames };
        return json(route, { ...body, archived: true });
      }
      if (one[1] === MULTIDAY_ID) {
        return json(route, { simulation_id: MULTIDAY_ID, status: "completed", config: null, frames: mdFrames(multiDays), error: null });
      }
      if (opts.runIds?.includes(one[1])) {
        state.polls++;
        return json(route, { ...fx, simulation_id: one[1], frames: fxFrames });
      }
      if (opts.distinctRunIds && one[1].startsWith(`${fx.simulation_id}-`)) {
        state.polls++;
        return json(route, { ...fx, simulation_id: one[1], frames: fxFrames });
      }
    }
    const sub = path.match(/^\/api\/v1\/simulations\/([^/]+)\/(arrival|ensemble)$/);
    if (sub && opts.runIds?.includes(sub[1])) {
      return sub[2] === "arrival" ? json(route, arrival) : json(route, { detail: "No ensemble requested for this run" }, 404);
    }
    if (path === `/api/v1/simulations/${MULTIDAY_ID}/ensemble`) {
      return json(route, { detail: "No ensemble requested for this run" }, 404);
    }
    if (path === `/api/v1/simulations/${fx.simulation_id}/arrival`) return json(route, arrival);
    if (opts.distinctRunIds && path.startsWith(`/api/v1/simulations/${fx.simulation_id}-`) && path.endsWith("/arrival")) {
      return json(route, arrival);
    }
    if (path === `/api/v1/simulations/${fx.simulation_id}/ensemble`) {
      if (!opts.ensemble) return json(route, { detail: "No ensemble requested for this run" }, 404);
      const n = ++state.ensemblePolls;
      const total = ensembleFixture.total;
      if (n === 1) return json(route, { status: "pending", done: 0, total });
      if (opts.ensembleNeverCompletes) return json(route, { status: "running", done: 1, total, error: null });
      const done = (n - 1) * ensembleStep;
      if (done < total) return json(route, { status: "running", done, total, error: null });
      return json(route, ensembleBody);
    }
    if (path === `/api/v1/simulations/${fx.simulation_id}` && req.method() === "GET") {
      state.polls++;
      return json(route, opts.translate ? { ...fx, frames: fxFrames } : fx);
    }
    if (path === "/api/v1/health") return json(route, { status: "ok" });
    if (path === "/api/v1/version") return json(route, { version: "3.0.0", git_sha: "e2e0sha" });
    return json(route, { detail: `not mocked: ${req.method()} ${path}` }, 404);
  });

  await page.routeWebSocket(/\/api\/v1\/simulations\/ws\//, (ws) => {
    state.wsConnections++;
    if (mode === "poll") {
      ws.close({ code: 1011, reason: "mock: force polling" });
      return;
    }
    const simId = ws.url().split("/").pop();
    const frames = simId === MULTIDAY_ID ? mdFrames(multiDays) : fxFrames;
    let i = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const statuses: Array<[string, number | null]> = opts.phases
      ? [["loading", null], ["buildings", null], ["spread", 0], ["spread", 0.25], ["spread", 0.5], ["spread", 0.75], ["finishing", null]]
      : [];
    let s = 0;
    const next = () => {
      if (s < statuses.length) {
        const [phase, progress] = statuses[s++];
        ws.send(JSON.stringify({ type: "simulation.status", simulation_id: simId, phase, progress }));
        timer = setTimeout(next, opts.phaseDelayMs ?? 400);
        return;
      }
      if (i < frames.length) {
        ws.send(JSON.stringify({ type: "simulation.frame", simulation_id: simId, frame: frames[i++] }));
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
