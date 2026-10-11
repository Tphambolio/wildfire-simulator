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
});
