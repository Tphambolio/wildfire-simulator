import { describe, expect, it } from "vitest";
import neighbourhoodsRaw from "../../public/edmonton/neighbourhoods.geojson?raw";
import csdRaw from "../../public/alberta/census_subdivisions.geojson?raw";
import {
  CSD_SOURCE,
  EDMONTON_NEIGHBOURHOOD_SOURCE,
  communitySourceTag,
  featureName,
  mergeCommunities,
  neighbourhoodArrivals,
  arrivalsToGeoJSON,
} from "./evacZones";
import { frame, square } from "../test/frames";

const edmonton = JSON.parse(neighbourhoodsRaw) as GeoJSON.FeatureCollection;
const csd = JSON.parse(csdRaw) as GeoJSON.FeatureCollection;

describe("community units: Edmonton neighbourhoods + census subdivisions", () => {
  const merged = mergeCommunities(edmonton, csd)!;

  it("keeps every Edmonton neighbourhood and adds the Alberta CSDs except Edmonton's", () => {
    expect(merged.features.length).toBe(edmonton.features.length + csd.features.length);
    const names = merged.features.map(featureName);
    expect(new Set(names).size).toBe(names.length); // unique names (statuses are keyed by name)
    expect(names).toContain("St. Albert");
    expect(names).toContain("Hinton");
    expect(csd.features.some((f) => (f.properties as Record<string, unknown>).csduid === "4811061")).toBe(false);
  });

  it("labels each unit with its source", () => {
    const src = (n: string) =>
      (merged.features.find((f) => featureName(f) === n)!.properties as Record<string, unknown>).source;
    expect(src("Abbottsfield")).toBe(EDMONTON_NEIGHBOURHOOD_SOURCE);
    expect(src("St. Albert")).toBe(CSD_SOURCE);
    const hinton = merged.features.find((f) => featureName(f) === "Hinton");
    expect(communitySourceTag(hinton)).toBe("CSD");
    expect(communitySourceTag(merged.features[0])).toBeNull();
  });

  it("a fire in Hinton reaches the Hinton census subdivision, tagged CSD", () => {
    const fire = frame(1, square(0.004, 53.4053, -117.565));
    const res = neighbourhoodArrivals([fire], merged);
    const hinton = res.find((a) => a.name === "Hinton");
    expect(hinton).toBeDefined();
    expect(communitySourceTag(hinton!.feature)).toBe("CSD");
    expect(res.every((a) => communitySourceTag(a.feature) === "CSD")).toBe(true);
  });

  it("works with either layer missing", () => {
    expect(mergeCommunities(null, null)).toBeNull();
    expect(mergeCommunities(edmonton, null)!.features.length).toBe(edmonton.features.length);
  });

  it("a St. Albert fire reaching Sturgeon County marks the crossing point, not the whole county", () => {
    // Fire growing from north St. Albert towards its boundary with Sturgeon County
    const fires = [frame(1, square(0.004, 53.6716, -113.6)), frame(2, square(0.02, 53.6716, -113.6))];
    const res = neighbourhoodArrivals(fires, merged);
    const sturgeon = res.find((a) => a.name === "Sturgeon County");
    expect(sturgeon?.at).toBeDefined();
    const fc = arrivalsToGeoJSON(res, null);
    const f = fc.features.find((x) => x.properties?.neighbourhood === "Sturgeon County")!;
    expect(f.geometry.type).toBe("Point");
    const [lng, lat] = (f.geometry as GeoJSON.Point).coordinates;
    // the dot sits on the fire, near St. Albert, not at the county's centroid ~40 km north
    expect(Math.abs(lat - 53.6716)).toBeLessThan(0.03);
    expect(Math.abs(lng + 113.6)).toBeLessThan(0.05);
    // Edmonton neighbourhoods keep their outlines
    const edm = arrivalsToGeoJSON(neighbourhoodArrivals([frame(1, square(0.004, 53.5175, -113.4755))], merged), null);
    expect(edm.features.every((x) => x.geometry.type !== "Point")).toBe(true);
  });
});
