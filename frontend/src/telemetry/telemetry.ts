/**
 * Usage counting + feedback links (docs/privacy.md), mirroring Pyra's config/telemetry.js.
 *
 * Privacy by construction:
 *   - provider "none" (the default) sends nothing anywhere;
 *   - "cloudflare": loads Cloudflare Web Analytics' cookieless beacon with the build's site
 *     token; FireSim sends nothing of its own (Cloudflare counts page views and Web Vitals);
 *   - "post": ONE navigator.sendBeacon() per page load (and one per rating) to the build's
 *     endpoint with exactly ALLOWED_KEYS: record type, the tab opened, the build, a coarse
 *     viewport class, whether exercise mode is on, a random per-tab session id held in
 *     sessionStorage only, and the 1-5 rating;
 *   - never an ignition point, perimeter, run ID, incident name, weather, coordinates or
 *     anything typed by the user. The feedback text goes only into the GitHub issue or e-mail
 *     the user opens and sends themselves.
 *
 * Build settings (all optional): VITE_FIRESIM_TELEMETRY = none | cloudflare | post,
 * VITE_FIRESIM_TELEMETRY_TOKEN (cloudflare), VITE_FIRESIM_TELEMETRY_ENDPOINT (post),
 * VITE_FIRESIM_FEEDBACK_EMAIL (shows the "Email" option).
 */
import { REPO_URL } from "../content/explanations";

export type TelemetryProvider = "none" | "cloudflare" | "post";

export interface TelemetryConfig {
  provider: TelemetryProvider;
  token: string;
  endpoint: string;
  feedbackEmail: string;
}

/** The only fields a record may carry; anything else is dropped. */
export const ALLOWED_KEYS = ["type", "page", "build", "viewport", "exercise", "session", "rating"] as const;
type AllowedKey = (typeof ALLOWED_KEYS)[number];
export type TelemetryRecord = Partial<Record<AllowedKey, string | number | boolean>>;

export const ISSUES_URL = `${REPO_URL}/issues/new`;
export const CLOUDFLARE_BEACON = "https://static.cloudflareinsights.com/beacon.min.js";

/** Parse the build settings; unknown or incomplete settings fall back to "none". */
export function telemetryConfigFrom(env: Record<string, string | undefined>): TelemetryConfig {
  const raw = (env.VITE_FIRESIM_TELEMETRY ?? "").trim().toLowerCase();
  const token = (env.VITE_FIRESIM_TELEMETRY_TOKEN ?? "").trim();
  const endpoint = (env.VITE_FIRESIM_TELEMETRY_ENDPOINT ?? "").trim();
  const feedbackEmail = (env.VITE_FIRESIM_FEEDBACK_EMAIL ?? "").trim();
  let provider: TelemetryProvider = "none";
  if (raw === "cloudflare" && token) provider = "cloudflare";
  else if (raw === "post" && /^https:\/\//.test(endpoint)) provider = "post";
  return { provider, token, endpoint, feedbackEmail };
}

export function telemetryConfig(): TelemetryConfig {
  return telemetryConfigFrom(import.meta.env as Record<string, string | undefined>);
}

/** Short build id (Vercel / CI commit), "" in local builds. */
export function buildId(): string {
  return typeof __FIRESIM_BUILD__ === "string" ? __FIRESIM_BUILD__.slice(0, 7) : "";
}

/** Keep only ALLOWED_KEYS (a guard against a caller passing more than it should). */
export function sanitize(record: Record<string, unknown>): TelemetryRecord {
  const out: TelemetryRecord = {};
  for (const k of ALLOWED_KEYS) {
    const v = record[k];
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") out[k] = v;
  }
  return out;
}

export function viewportClass(width: number): "phone" | "desktop" {
  return width > 0 && width < 768 ? "phone" : "desktop";
}

function sessionId(): string {
  try {
    const existing = sessionStorage.getItem("firesim-session");
    if (existing) return existing;
    const bytes = new Uint8Array(8);
    crypto.getRandomValues(bytes);
    const s = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    sessionStorage.setItem("firesim-session", s);
    return s;
  } catch {
    return "na";
  }
}

export interface PageContext {
  page: string;
  exercise: boolean;
}

export function record(type: "pageview" | "rating", ctx: PageContext, extra: { rating?: number } = {}): TelemetryRecord {
  return sanitize({
    type,
    page: ctx.page,
    build: buildId(),
    viewport: viewportClass(typeof window !== "undefined" ? window.innerWidth : 0),
    exercise: ctx.exercise,
    session: sessionId(),
    ...extra,
  });
}

/** Send one record. Returns true only if something was handed to the network. */
export function send(cfg: TelemetryConfig, rec: TelemetryRecord): boolean {
  if (cfg.provider !== "post" || !cfg.endpoint) return false;
  const body = JSON.stringify(sanitize(rec));
  try {
    // text/plain: no CORS preflight
    if (navigator.sendBeacon) return navigator.sendBeacon(cfg.endpoint, new Blob([body], { type: "text/plain;charset=UTF-8" }));
    void fetch(cfg.endpoint, { method: "POST", body, keepalive: true, headers: { "Content-Type": "text/plain;charset=UTF-8" } }).catch(() => {});
    return true;
  } catch {
    return false;
  }
}

let started = false;

/** Once per page load: the Cloudflare beacon, or one "pageview" record. */
export function startTelemetry(cfg: TelemetryConfig, ctx: PageContext, doc: Document = document): boolean {
  if (started) return false;
  started = true;
  if (cfg.provider === "cloudflare") {
    const s = doc.createElement("script");
    s.defer = true;
    s.src = CLOUDFLARE_BEACON;
    s.setAttribute("data-cf-beacon", JSON.stringify({ token: cfg.token }));
    doc.head.appendChild(s);
    return true;
  }
  return send(cfg, record("pageview", ctx));
}

/** Tests only */
export function resetTelemetryForTests(): void {
  started = false;
}

/** 1-5, rounded and clamped; sent only with provider "post". */
export function sendRating(cfg: TelemetryConfig, ctx: PageContext, rating: number): boolean {
  const n = Math.max(1, Math.min(5, Math.round(rating)));
  return send(cfg, record("rating", ctx, { rating: n }));
}

function contextLines(ctx: PageContext, rating: number | null): string[] {
  return [
    `Tab: ${ctx.page}`,
    `Build: ${buildId() || "n/a"}`,
    `Exercise mode: ${ctx.exercise ? "on" : "off"}`,
    ...(rating ? [`Useful today: ${rating}/5`] : []),
  ];
}

/** A prefilled GitHub issue: tab, build, rating and the user's own text, nothing else. */
export function issueURL(ctx: PageContext, text: string, rating: number | null): string {
  const title = `Feedback: ${ctx.page}${buildId() ? ` (${buildId()})` : ""}`;
  const body = `${contextLines(ctx, rating).map((l) => `**${l.replace(": ", ":** ")}`).join(" · ")}\n\n${text.trim()}\n`;
  return `${ISSUES_URL}?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}&labels=feedback`;
}

export function mailURL(cfg: TelemetryConfig, ctx: PageContext, text: string, rating: number | null): string {
  if (!cfg.feedbackEmail) return "";
  const subject = `FireSim feedback: ${ctx.page}${buildId() ? ` (${buildId()})` : ""}`;
  const body = `${contextLines(ctx, rating).join("\n")}\n\n${text.trim()}\n`;
  return `mailto:${cfg.feedbackEmail}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
