import { describe, expect, it } from "vitest";
import neighbourhoodsRaw from "../../public/edmonton/neighbourhoods.geojson?raw";
import csdRaw from "../../public/alberta/census_subdivisions.geojson?raw";
import placesRaw from "../../public/alberta/places.geojson?raw";
import {
  CSD_SOURCE,
  communityLevel,
  communityParent,
  labelPoint,
  EDMONTON_NEIGHBOURHOOD_SOURCE,
  communitySourceTag,
  featureName,
  mergeCommunities,
  neighbourhoodArrivals,
  arrivalsToGeoJSON,
} from "./evacZones";
import { frame, square } from "../test/frames";
import { fixture } from "../test/fixture";

const edmonton = JSON.parse(neighbourhoodsRaw) as GeoJSON.FeatureCollection;
const csd = JSON.parse(csdRaw) as GeoJSON.FeatureCollection;
const places = JSON.parse(placesRaw) as GeoJSON.FeatureCollection;

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
    // Fire growing from north St. Albert towards its boundary with Sturgeon County (census
    // subdivisions only: the fallback when no hamlet or locality is at the crossing)
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

describe("hamlets and localities: the most specific named place", () => {
  const before = mergeCommunities(edmonton, csd)!;
  const merged = mergeCommunities(edmonton, csd, places)!;
  const names = (fc: GeoJSON.FeatureCollection) => fc.features.map(featureName);
  const byName = (n: string) => merged.features.find((f) => featureName(f) === n)!;
  // Perimeter frames growing 0.012 degrees (about 1.3 km north-south) per hour
  const growing = (lat: number, lng: number, hours: number) =>
    Array.from({ length: hours }, (_, i) => frame(i + 1, square(0.012 * (i + 1), lat, lng)));

  it("adds hamlets and localities with a level, a source and their census subdivision", () => {
    expect(merged.features.length).toBe(before.features.length + places.features.length);
    const all = names(merged);
    expect(new Set(all).size).toBe(all.length); // statuses are keyed by name
    const calahoo = byName("Calahoo (hamlet, Sturgeon County)");
    expect(communityLevel(calahoo)).toBe("hamlet");
    expect(communityParent(calahoo)).toBe("Sturgeon County");
    expect(communitySourceTag(calahoo)).toBeNull(); // the name says it all
    expect(calahoo.geometry.type).toMatch(/Polygon/);
    expect(communityLevel(byName("Sturgeon County"))).toBe("csd");
    expect(communityLevel(byName("Abbottsfield"))).toBe("neighbourhood");
    const levels = new Set(places.features.map((f) => communityLevel(f)));
    expect([...levels].sort()).toEqual(["hamlet", "locality"]);
    // Every place has a parent CSD in the CSD layer, and only names (no personal data)
    const csdNames = new Set(csd.features.map(featureName));
    for (const f of places.features) {
      expect(csdNames.has(communityParent(f)!)).toBe(true);
      expect(Object.keys(f.properties!).sort()).toEqual(
        expect.arrayContaining(["kind", "level", "name", "parent", "place", "source"]),
      );
      expect(Object.keys(f.properties!).every((k) =>
        ["name", "place", "level", "kind", "parent", "parent_csduid", "source", "radius_m", "cgndb_id"].includes(k),
      )).toBe(true);
    }
  });

  it("a fire west of St. Albert names Villeneuve and Calahoo, not just Sturgeon County", () => {
    const res = neighbourhoodArrivals(growing(53.69, -113.87, 6), merged);
    const got = res.map((a) => a.name);
    expect(got).toContain("Villeneuve (hamlet, Sturgeon County)");
    expect(got).toContain("Calahoo (hamlet, Sturgeon County)");
    expect(res.find((a) => a.name.startsWith("Villeneuve"))!.arrivalHours)
      .toBeLessThan(res.find((a) => a.name.startsWith("Calahoo"))!.arrivalHours);
    // The ignition is in open county, away from any place: the county dot stays (fallback)
    const county = res.find((a) => a.name === "Sturgeon County");
    expect(county?.arrivalHours).toBe(1);
    expect(arrivalsToGeoJSON(res, null).features.find((f) => f.properties?.neighbourhood === "Sturgeon County")!
      .geometry.type).toBe("Point");
    // Census subdivisions alone named only the counties
    expect(neighbourhoodArrivals(growing(53.69, -113.87, 6), before).map((a) => a.name))
      .toEqual(["Sturgeon County", "Parkland County"]);
  });

  it("a fire at Villeneuve names the hamlet instead of the county", () => {
    const res = neighbourhoodArrivals(growing(53.656, -113.80, 3), merged);
    expect(res[0].name).toBe("Villeneuve (hamlet, Sturgeon County)");
    expect(res[0].arrivalHours).toBe(1);
    expect(res.some((a) => a.name === "Sturgeon County")).toBe(false);
    // Hamlets keep their outline on the map
    const fc = arrivalsToGeoJSON(res, null);
    expect(fc.features[0].geometry.type).toMatch(/Polygon/);
  });

  it("a fire north of St. Albert names the subdivisions at the county line", () => {
    const fires = [frame(1, square(0.004, 53.6716, -113.6)), frame(2, square(0.02, 53.6716, -113.6))];
    const res = neighbourhoodArrivals(fires, merged);
    const got = res.map((a) => a.name);
    expect(got[0]).toBe("St. Albert"); // still the city it starts in
    expect(got).toContain("Upper Manor Estates (locality, Sturgeon County)");
    // Before, the same run said only "St. Albert" and "Sturgeon County"
    expect(neighbourhoodArrivals(fires, before).map((a) => a.name)).toEqual(["St. Albert", "Sturgeon County"]);
  });

  it("a run inside a city still names the city", () => {
    const res = neighbourhoodArrivals([frame(1, square(0.004, 53.633, -113.62))], merged);
    expect(res.map((a) => a.name)).toEqual(["St. Albert"]);
    const hinton = neighbourhoodArrivals([frame(1, square(0.004, 53.4053, -117.565))], merged);
    expect(hinton.map((a) => a.name)).toContain("Hinton");
    expect(hinton.every((a) => communityLevel(a.feature) === "csd")).toBe(true);
  });

  it("a run in Fort McMurray names the urban service area, not Wood Buffalo", () => {
    const res = neighbourhoodArrivals([frame(1, square(0.004, 56.726, -111.38))], merged);
    expect(res.map((a) => a.name)).toEqual(["Fort McMurray (urban service area, Wood Buffalo)"]);
  });

  it("Edmonton is unchanged", () => {
    const edm = merged.features.filter((f) => communityLevel(f) === "neighbourhood");
    expect(edm.length).toBe(edmonton.features.length);
    expect(edm.map(featureName)).toEqual(edmonton.features.map(featureName));
    for (const [lat, lng] of [[53.5175, -113.4755], [53.45, -113.55], [53.6, -113.42]]) {
      const fires = growing(lat, lng, 2);
      const a = neighbourhoodArrivals(fires, merged).map((x) => [x.name, x.arrivalHours]);
      const b = neighbourhoodArrivals(fires, before).map((x) => [x.name, x.arrivalHours]);
      expect(a).toEqual(b);
    }
  });

  it("a locality point counts as a disc of radius_m: reached within 500 m of it", () => {
    const loc: GeoJSON.Feature = {
      type: "Feature",
      geometry: { type: "Point", coordinates: [-112, 54] },
      properties: { name: "Testville (locality, Test County)", level: "locality", parent: "Test County", radius_m: 250 },
    };
    const fc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [loc] };
    const dLat = (m: number) => m / 111_320;
    // A small fire whose northern edge is 700 m (reached) or 800 m (not) south of the point
    const at = (m: number) => neighbourhoodArrivals([frame(1, square(0.0005, 54 - dLat(m) - 0.0005, -112))], fc);
    expect(at(700).map((a) => a.name)).toEqual(["Testville (locality, Test County)"]);
    expect(at(800)).toEqual([]);
    expect(labelPoint(loc)).toEqual([-112, 54]);
    // On the map it is a dot where the fire crossed (no outline exists)
    expect(arrivalsToGeoJSON(at(700), null).features[0].geometry.type).toBe("Point");
  });

  it("stays fast on the recorded run (median of 5)", () => {
    const time = (fc: GeoJSON.FeatureCollection) => {
      const ms: number[] = [];
      let n = 0;
      for (let i = 0; i < 5; i++) {
        const t = performance.now();
        n = neighbourhoodArrivals(fixture.frames, fc).length;
        ms.push(performance.now() - t);
      }
      return { ms: ms.sort((x, y) => x - y)[2], n };
    };
    const a = time(before);
    const b = time(merged);
    console.log(`[perf] neighbourhoodArrivals, Terwillegar fixture: CSDs ${a.ms.toFixed(1)} ms (${a.n}), with places ${b.ms.toFixed(1)} ms (${b.n})`);
    expect(b.n).toBe(a.n); // nothing outside Edmonton within reach
    expect(b.ms).toBeLessThan(500);
  });
});
