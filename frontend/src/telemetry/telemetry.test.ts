import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ALLOWED_KEYS,
  CLOUDFLARE_BEACON,
  issueURL,
  mailURL,
  record,
  resetTelemetryForTests,
  sanitize,
  sendRating,
  startTelemetry,
  telemetryConfigFrom,
  viewportClass,
  type TelemetryConfig,
} from "./telemetry";

const NONE: TelemetryConfig = { provider: "none", token: "", endpoint: "", feedbackEmail: "" };
const POST: TelemetryConfig = { provider: "post", token: "", endpoint: "https://count.example/fs", feedbackEmail: "" };
const CTX = { page: "simulation", exercise: false };

describe("telemetry config", () => {
  it("defaults to none, and falls back to none when a provider lacks its token or endpoint", () => {
    expect(telemetryConfigFrom({}).provider).toBe("none");
    expect(telemetryConfigFrom({ VITE_FIRESIM_TELEMETRY: "cloudflare" }).provider).toBe("none");
    expect(telemetryConfigFrom({ VITE_FIRESIM_TELEMETRY: "post" }).provider).toBe("none");
    expect(telemetryConfigFrom({ VITE_FIRESIM_TELEMETRY: "post", VITE_FIRESIM_TELEMETRY_ENDPOINT: "http://plain.example" }).provider).toBe("none");
    expect(telemetryConfigFrom({ VITE_FIRESIM_TELEMETRY: "bogus", VITE_FIRESIM_TELEMETRY_TOKEN: "t" }).provider).toBe("none");
  });

  it("accepts cloudflare with a token and post with an https endpoint", () => {
    expect(telemetryConfigFrom({ VITE_FIRESIM_TELEMETRY: "Cloudflare", VITE_FIRESIM_TELEMETRY_TOKEN: "abc" }).provider).toBe("cloudflare");
    expect(telemetryConfigFrom({ VITE_FIRESIM_TELEMETRY: "post", VITE_FIRESIM_TELEMETRY_ENDPOINT: "https://x.example/c" }).provider).toBe("post");
  });
});

describe("records carry only the allowed fields", () => {
  it("sanitize drops everything outside ALLOWED_KEYS", () => {
    const out = sanitize({ type: "pageview", page: "eoc", lat: 53.5, lng: -113.5, simulation_id: "abc", incident: "Terwillegar", rating: 4 });
    expect(Object.keys(out).sort()).toEqual(["page", "rating", "type"]);
  });

  it("a page view has exactly the documented keys", () => {
    const r = record("pageview", { page: "eoc", exercise: true });
    expect(Object.keys(r).every((k) => (ALLOWED_KEYS as readonly string[]).includes(k))).toBe(true);
    expect(r).toMatchObject({ type: "pageview", page: "eoc", exercise: true });
    expect(typeof r.session).toBe("string");
  });

  it("viewport is a coarse class only", () => {
    expect(viewportClass(375)).toBe("phone");
    expect(viewportClass(1440)).toBe("desktop");
  });
});

describe("sending", () => {
  let beacon: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    resetTelemetryForTests();
    beacon = vi.fn(() => true);
    Object.defineProperty(navigator, "sendBeacon", { value: beacon, configurable: true });
    document.head.innerHTML = "";
  });
  afterEach(() => {
    resetTelemetryForTests();
  });

  it("provider none sends nothing and loads no script", () => {
    expect(startTelemetry(NONE, CTX)).toBe(false);
    expect(sendRating(NONE, CTX, 5)).toBe(false);
    expect(beacon).not.toHaveBeenCalled();
    expect(document.head.querySelector("script")).toBeNull();
  });

  it("post sends one page view per load and a clamped rating", async () => {
    expect(startTelemetry(POST, CTX)).toBe(true);
    expect(startTelemetry(POST, CTX)).toBe(false);
    expect(sendRating(POST, CTX, 9)).toBe(true);
    expect(beacon).toHaveBeenCalledTimes(2);
    const bodies = await Promise.all(beacon.mock.calls.map((c) => (c[1] as Blob).text()));
    const [view, rating] = bodies.map((b) => JSON.parse(b));
    expect(view.type).toBe("pageview");
    expect(rating).toMatchObject({ type: "rating", rating: 5 });
    for (const b of [view, rating]) expect(Object.keys(b).every((k) => (ALLOWED_KEYS as readonly string[]).includes(k))).toBe(true);
  });

  it("cloudflare loads the cookieless beacon with the token and sends nothing itself", () => {
    expect(startTelemetry({ ...NONE, provider: "cloudflare", token: "tok" }, CTX)).toBe(true);
    const s = document.head.querySelector("script")!;
    expect(s.src).toBe(CLOUDFLARE_BEACON);
    expect(JSON.parse(s.getAttribute("data-cf-beacon")!)).toEqual({ token: "tok" });
    expect(beacon).not.toHaveBeenCalled();
  });
});

describe("feedback links", () => {
  it("the GitHub issue carries the tab, rating and the user's note only", () => {
    const u = new URL(issueURL({ page: "eoc", exercise: true }, "  Needs a 24x rate  ", 4));
    expect(u.origin + u.pathname).toBe("https://github.com/Tphambolio/wildfire-simulator/issues/new");
    expect(u.searchParams.get("labels")).toBe("feedback");
    const body = u.searchParams.get("body")!;
    expect(body).toContain("eoc");
    expect(body).toContain("4/5");
    expect(body).toContain("Exercise mode:** on");
    expect(body).toContain("Needs a 24x rate");
    expect(body).not.toMatch(/-?\d{2,3}\.\d{3,}/); // no coordinates
  });

  it("e-mail is offered only when the build sets an address", () => {
    expect(mailURL(NONE, CTX, "x", null)).toBe("");
    const m = mailURL({ ...NONE, feedbackEmail: "ops@example.org" }, CTX, "hello", 2);
    expect(m.startsWith("mailto:ops@example.org?subject=")).toBe(true);
    expect(decodeURIComponent(m)).toContain("Useful today: 2/5");
  });
});
