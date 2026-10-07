/**
 * Coordinate entry for the ignition point (design spec §4.1, §7 2.1.1 / 3.3.1).
 *
 * Accepts decimal degrees ("53.4606", "-113.6597", "113.6597 W") and degrees-minutes(-seconds)
 * ("53°27'38\"N", "53 27 38 N", "113°39.6'W", "53d27m38s"), with N/S/E/W before or after the
 * number. W and S make the value negative. A pasted pair ("53°27'38\"N 113°39'35\"W",
 * "53.46, -113.66") is split into latitude and longitude. Values are checked against a box
 * around Canada so a swapped or mistyped coordinate is caught before a run.
 */

export type Axis = "lat" | "lng";

/** Bounding box of Canada (slightly padded), decimal degrees. */
export const CANADA_BOUNDS = { latMin: 41.6, latMax: 83.2, lngMin: -141.1, lngMax: -52.5 } as const;

export type ParseResult = { ok: true; value: number } | { ok: false; error: string };
export type PairResult = { ok: true; lat: number; lng: number } | { ok: false; error: string };

const AXIS_NAME: Record<Axis, string> = { lat: "Latitude", lng: "Longitude" };

/** Unify the many degree/minute/second symbols people paste. */
function normalise(text: string): string {
  let s = text
    .trim()
    .toUpperCase()
    .replace(/[−‒–—]/g, "-") // minus and dashes
    .replace(/[°º˚]/g, "°")
    .replace(/[″”“]|''/g, '"')
    .replace(/[′’‘´`]/g, "'");
  // Letter style "53D27M38S": D/M/S are units here, so the S is seconds, not south
  const letters = s.match(/^(.*?\d)\s*D\s*(\d+(?:[.,]\d+)?)\s*M(?:\s*(\d+(?:[.,]\d+)?)\s*S)?(.*)$/);
  if (letters) s = `${letters[1]}° ${letters[2]}'${letters[3] !== undefined ? ` ${letters[3]}"` : ""} ${letters[4]}`;
  else s = s.replace(/(\d)\s*D(?![A-Z])/, "$1°");
  return s.replace(/\s+/g, " ").trim();
}

/** Parse one coordinate for `axis`. */
export function parseCoordinate(text: string, axis: Axis): ParseResult {
  const name = AXIS_NAME[axis];
  if (!text.trim()) return { ok: false, error: `${name} is required` };
  let s = normalise(text);

  // Hemisphere letter, before or after the number
  const hemis = s.match(/[NSEW]/g) ?? [];
  if (hemis.length > 1) return { ok: false, error: `${name}: more than one of N, S, E, W` };
  const hemi = hemis[0] ?? null;
  if (hemi) {
    const okLetters = axis === "lat" ? "NS" : "EW";
    if (!okLetters.includes(hemi)) {
      return { ok: false, error: `${name} takes ${axis === "lat" ? "N or S" : "E or W"}, not ${hemi}` };
    }
    s = s.replace(/[NSEW]/, " ").trim();
  }

  let sign = 1;
  if (s.startsWith("-")) { sign = -1; s = s.slice(1).trim(); }
  else if (s.startsWith("+")) { s = s.slice(1).trim(); }

  // What remains: up to three numbers, optionally followed by ° ' "
  const m = s.match(/^(\d+(?:[.,]\d+)?)\s*°?\s*(?:(\d+(?:[.,]\d+)?)\s*'?\s*(?:(\d+(?:[.,]\d+)?)\s*"?)?)?$/);
  if (!m) return { ok: false, error: `${name}: enter decimal degrees (53.4606) or degrees, minutes, seconds (53°27'38"N)` };
  const num = (v: string | undefined) => (v === undefined ? 0 : Number(v.replace(",", ".")));
  const deg = num(m[1]);
  const min = num(m[2]);
  const sec = num(m[3]);
  if (m[2] !== undefined && !Number.isInteger(deg)) return { ok: false, error: `${name}: degrees must be whole when minutes are given` };
  if (m[3] !== undefined && !Number.isInteger(min)) return { ok: false, error: `${name}: minutes must be whole when seconds are given` };
  if (min >= 60) return { ok: false, error: `${name}: minutes must be below 60` };
  if (sec >= 60) return { ok: false, error: `${name}: seconds must be below 60` };

  if (hemi === "S" || hemi === "W") {
    // "-113 W" is still west; "-53 N" contradicts itself
    sign = -1;
  } else if (hemi && sign < 0) {
    return { ok: false, error: `${name}: a minus sign and ${hemi} contradict each other` };
  }
  const value = sign * (deg + min / 60 + sec / 3600);
  const limit = axis === "lat" ? 90 : 180;
  if (Math.abs(value) > limit) return { ok: false, error: `${name} must be between -${limit} and ${limit}` };
  return { ok: true, value };
}

/** Field-level message when the point is outside Canada (null when inside). */
export function canadaBoundsError(axis: Axis, value: number): string | null {
  const b = CANADA_BOUNDS;
  if (axis === "lat" && (value < b.latMin || value > b.latMax)) {
    return `Latitude must be between ${b.latMin} and ${b.latMax} (Canada)`;
  }
  if (axis === "lng" && (value < b.lngMin || value > b.lngMax)) {
    return `Longitude must be between ${b.lngMin} and ${b.lngMax} (Canada; west is negative)`;
  }
  return null;
}

/** Parse a coordinate and check it lies within Canada. */
export function parseCanadaCoordinate(text: string, axis: Axis): ParseResult {
  const r = parseCoordinate(text, axis);
  if (!r.ok) return r;
  const err = canadaBoundsError(axis, r.value);
  return err ? { ok: false, error: err } : r;
}

/**
 * Split a pasted pair into two parts, or null if `text` is a single coordinate. Splits on a
 * comma/semicolon, after the first hemisphere letter that follows a number ("53°N 113°W"),
 * before the second leading hemisphere letter ("N53 W113"), or between two plain numbers.
 */
export function splitPair(text: string): [string, string] | null {
  const t = text.trim();
  const sep = t.split(/\s*[;,]\s*(?=[-+NSEWnsew\d−])/);
  if (sep.length === 2 && sep[0] && sep[1]) return [sep[0], sep[1]];
  // trailing hemisphere: "53°27'38"N 113°39'35"W"
  const trailing = t.match(/^(.*?\d[^NSEWnsew]*[NSns])\s*([-+]?\d.*[EWew])$/);
  if (trailing) return [trailing[1], trailing[2]];
  const trailingLngFirst = t.match(/^(.*?\d[^NSEWnsew]*[EWew])\s*([-+]?\d.*[NSns])$/);
  if (trailingLngFirst) return [trailingLngFirst[1], trailingLngFirst[2]];
  // leading hemisphere: "N53 27 38 W113 39 35"
  const leading = t.match(/^([NSEWnsew]\s*\d[^NSEWnsew]*?)\s+([NSEWnsew]\s*\d.*)$/);
  if (leading) return [leading[1], leading[2]];
  // two plain decimal numbers: "53.46 -113.66"
  const plain = t.match(/^([-+−]?\d+(?:\.\d+)?)\s+([-+−]?\d+(?:\.\d+)?)$/);
  if (plain) return [plain[1], plain[2]];
  return null;
}

/** Parse a pasted "lat, lng" pair (any supported format), within Canada. Order follows N/S/E/W letters when present. */
export function parseCoordinatePair(text: string): PairResult {
  const parts = splitPair(text);
  if (!parts) return { ok: false, error: "Enter latitude and longitude, e.g. 53.4606, -113.6597 or 53°27'38\"N 113°39'35\"W" };
  let [a, b] = parts;
  if (/[EWew]/.test(a) && /[NSns]/.test(b)) [a, b] = [b, a];
  const lat = parseCanadaCoordinate(a, "lat");
  if (!lat.ok) return lat;
  const lng = parseCanadaCoordinate(b, "lng");
  if (!lng.ok) return lng;
  return { ok: true, lat: lat.value, lng: lng.value };
}

/** "53.4606" — 4 decimals (about 10 m), the precision shown in summaries. */
export function formatDecimal(value: number): string {
  return value.toFixed(4);
}
