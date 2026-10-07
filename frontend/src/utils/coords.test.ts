import { describe, expect, it } from "vitest";
import { parseCanadaCoordinate, parseCoordinate, parseCoordinatePair } from "./coords";

const ok = (r: { ok: boolean; value?: number }) => {
  expect(r.ok).toBe(true);
  return r.value as number;
};

describe("parseCoordinate: decimal degrees", () => {
  it("plain and signed", () => {
    expect(ok(parseCoordinate("53.4606", "lat"))).toBeCloseTo(53.4606, 6);
    expect(ok(parseCoordinate("-113.6597", "lng"))).toBeCloseTo(-113.6597, 6);
    expect(ok(parseCoordinate("+53.5", "lat"))).toBe(53.5);
    expect(ok(parseCoordinate(" −113.5 ", "lng"))).toBe(-113.5); // unicode minus
  });
  it("hemisphere letters before or after, either case", () => {
    expect(ok(parseCoordinate("53.4606 N", "lat"))).toBeCloseTo(53.4606, 6);
    expect(ok(parseCoordinate("N53.4606", "lat"))).toBeCloseTo(53.4606, 6);
    expect(ok(parseCoordinate("113.6597W", "lng"))).toBeCloseTo(-113.6597, 6);
    expect(ok(parseCoordinate("w 113.6597", "lng"))).toBeCloseTo(-113.6597, 6);
    expect(ok(parseCoordinate("53.4606°N", "lat"))).toBeCloseTo(53.4606, 6);
    expect(ok(parseCoordinate("-113.5 W", "lng"))).toBe(-113.5); // minus and W agree
    expect(ok(parseCoordinate("33.9 S", "lat"))).toBe(-33.9);
    expect(ok(parseCoordinate("151.2 E", "lng"))).toBe(151.2);
  });
});

describe("parseCoordinate: DMS and DM", () => {
  it("degrees, minutes, seconds with symbols", () => {
    expect(ok(parseCoordinate(`53°27'38"N`, "lat"))).toBeCloseTo(53 + 27 / 60 + 38 / 3600, 6);
    expect(ok(parseCoordinate(`113°39'35"W`, "lng"))).toBeCloseTo(-(113 + 39 / 60 + 35 / 3600), 6);
    expect(ok(parseCoordinate("53° 27′ 38″ N", "lat"))).toBeCloseTo(53.460556, 5); // prime symbols
    expect(ok(parseCoordinate("53º27'38''N", "lat"))).toBeCloseTo(53.460556, 5); // ordinal º and ''
    expect(ok(parseCoordinate(`53°27'38.4"N`, "lat"))).toBeCloseTo(53 + 27 / 60 + 38.4 / 3600, 6);
  });
  it("space-separated and letter style", () => {
    expect(ok(parseCoordinate("53 27 38 N", "lat"))).toBeCloseTo(53.460556, 5);
    expect(ok(parseCoordinate("-113 39 35", "lng"))).toBeCloseTo(-113.659722, 5);
    expect(ok(parseCoordinate("53d27m38s N", "lat"))).toBeCloseTo(53.460556, 5);
    expect(ok(parseCoordinate("113d39m35sW", "lng"))).toBeCloseTo(-113.659722, 5);
  });
  it("degrees and decimal minutes", () => {
    expect(ok(parseCoordinate("53°27.5'N", "lat"))).toBeCloseTo(53 + 27.5 / 60, 6);
    expect(ok(parseCoordinate("113 39.6 W", "lng"))).toBeCloseTo(-(113 + 39.6 / 60), 6);
  });
});

describe("parseCoordinate: bad input", () => {
  const bad = (text: string, axis: "lat" | "lng", msg: RegExp) => {
    const r = parseCoordinate(text, axis);
    expect(r.ok, text).toBe(false);
    if (!r.ok) expect(r.error).toMatch(msg);
  };
  it("rejects with a field-level message", () => {
    bad("", "lat", /required/);
    bad("abc", "lat", /decimal degrees/);
    bad("53.4.6", "lat", /decimal degrees/);
    bad("113.5 N", "lng", /E or W/); // wrong hemisphere for the axis
    bad("53.5 E", "lat", /N or S/);
    bad("53 N S", "lat", /more than one/);
    bad("-53.5 N", "lat", /contradict/);
    bad(`53°61'00"N`, "lat", /minutes/);
    bad(`53°27'60"N`, "lat", /seconds/);
    bad("53.5 27 N", "lat", /whole/);
    bad("95", "lat", /between -90 and 90/);
    bad("-190", "lng", /between -180 and 180/);
  });
  it("checks Canada bounds", () => {
    const r1 = parseCanadaCoordinate("33.9 S", "lat");
    expect(r1.ok).toBe(false);
    if (!r1.ok) expect(r1.error).toMatch(/Latitude must be between 41.6 and 83.2/);
    const r2 = parseCanadaCoordinate("113.5", "lng"); // forgot the minus / W
    expect(r2.ok).toBe(false);
    if (!r2.ok) expect(r2.error).toMatch(/west is negative/);
    expect(parseCanadaCoordinate("113.5 W", "lng").ok).toBe(true);
  });
});

describe("parseCoordinatePair: pasted pairs", () => {
  const pair = (text: string) => {
    const r = parseCoordinatePair(text);
    expect(r.ok, text).toBe(true);
    return r.ok ? [r.lat, r.lng] : [NaN, NaN];
  };
  it("DMS pair with trailing hemispheres", () => {
    const [lat, lng] = pair(`53°27'38"N 113°39'35"W`);
    expect(lat).toBeCloseTo(53.460556, 5);
    expect(lng).toBeCloseTo(-113.659722, 5);
  });
  it("DMS pair with leading hemispheres, longitude first, and comma-separated", () => {
    expect(pair("N53 27 38 W113 39 35")[1]).toBeCloseTo(-113.659722, 5);
    const [lat, lng] = pair(`113°39'35"W 53°27'38"N`);
    expect(lat).toBeCloseTo(53.460556, 5);
    expect(lng).toBeCloseTo(-113.659722, 5);
    expect(pair(`53°27'38"N, 113°39'35"W`)[0]).toBeCloseTo(53.460556, 5);
  });
  it("decimal pairs", () => {
    expect(pair("53.4606, -113.6597")).toEqual([53.4606, -113.6597]);
    expect(pair("53.4606 -113.6597")).toEqual([53.4606, -113.6597]);
    expect(pair("53.4606;-113.6597")).toEqual([53.4606, -113.6597]);
  });
  it("rejects a single value, an out-of-Canada point and nonsense", () => {
    expect(parseCoordinatePair("53.4606").ok).toBe(false);
    expect(parseCoordinatePair("-33.86, 151.21").ok).toBe(false);
    expect(parseCoordinatePair("hello world").ok).toBe(false);
  });
});
