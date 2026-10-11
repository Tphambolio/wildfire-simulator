import { describe, expect, it } from "vitest";
import { sourceTag } from "../components/CriticalAssetsPanel";
import type { SimulationFrame } from "../types/simulation";
import {
  ASSET_CATEGORIES,
  EDMONTON_LAYER_BOX,
  areaBox,
  assetReach,
  assetsFromGeoJSON,
  fireFromFrames,
  inEdmontonBox,
  outsideEdmonton,
  roadReach,
} from "./assets";

// Alberta layers served by area outside Edmonton (GET /api/v1/layers/assets|roads)

const HINTON = { lat: 53.405, lng: -117.565 };

describe("areaBox", () => {
  it("is about 28 km each way around an ignition outside Edmonton, snapped", () => {
    const b = areaBox(HINTON.lat, HINTON.lng)!;
    expect(b).not.toBeNull();
    expect((b.north - b.south) * 111.32).toBeCloseTo(56, 0);
    expect(b.north - b.south).toBeLessThanOrEqual(1.0); // API limit (FIRESIM_LAYERS_MAX_BOX_DEG)
    expect(b.east - b.west).toBeLessThanOrEqual(2.0);
    expect(b.south < HINTON.lat && HINTON.lat < b.north && b.west < HINTON.lng && HINTON.lng < b.east).toBe(true);
    // nearby ignitions share one request
    expect(areaBox(HINTON.lat + 0.01, HINTON.lng - 0.01)).toEqual(b);
  });

  it("is null deep inside the Edmonton layers' box (the bundled layers cover it)", () => {
    // Edmonton's box is ~42 km N-S: no 56 km box fits inside it, so only a smaller one is null
    expect(areaBox(53.53, -113.49, 10)).toBeNull();
    expect(areaBox(53.53, -113.49)).not.toBeNull();
  });
});

describe("outsideEdmonton", () => {
  it("drops Alberta assets inside the Edmonton box and road pieces wholly inside it", () => {
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", properties: { name: "In" }, geometry: { type: "Point", coordinates: [-113.5, 53.5] } },
        { type: "Feature", properties: { name: "Out" }, geometry: { type: "Point", coordinates: [-113.0, 53.5] } },
        { type: "Feature", properties: { name: "Site", anchor: [-113.5, 53.5] }, geometry: { type: "Polygon", coordinates: [[[-113.5, 53.5], [-113.49, 53.5], [-113.49, 53.51], [-113.5, 53.5]]] } },
        { type: "Feature", properties: { name: "Road in" }, geometry: { type: "LineString", coordinates: [[-113.5, 53.5], [-113.4, 53.5]] } },
        { type: "Feature", properties: { name: "Road across" }, geometry: { type: "LineString", coordinates: [[-113.3, 53.5], [-113.2, 53.5]] } },
      ],
    };
    expect(outsideEdmonton(fc)!.features.map((f) => f.properties!.name)).toEqual(["Out", "Road across"]);
    expect(inEdmontonBox(EDMONTON_LAYER_BOX.west + 0.01, EDMONTON_LAYER_BOX.south + 0.01)).toBe(true);
  });
});

describe("Alberta assets in the card", () => {
  const layer: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [HINTON.lng, HINTON.lat] },
        properties: {
          name: "Hinton Healthcare Centre", category: "hospital", source: "Statistics Canada ODHF v1.1",
          source_id: "odhf#1", licence: "Open Government Licence - Canada; ODbL 1.0 (c) OpenStreetMap contributors",
          sources: [{ source: "Statistics Canada ODHF v1.1", source_id: "odhf#1" }, { source: "OpenStreetMap", source_id: "osm:node/1" }],
        },
      },
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [HINTON.lng + 0.01, HINTON.lat] },
        properties: { name: "Hinton EMS", category: "ems", source: "OpenStreetMap", source_id: "osm:node/2", licence: "ODbL", sources: [{ source: "OpenStreetMap", source_id: "osm:node/2" }] },
      },
    ],
  };

  it("keeps the ICS category (EMS is a category) and tags the source", () => {
    const assets = assetsFromGeoJSON(layer, { idPrefix: "ab:" });
    expect(assets.map((a) => a.category)).toEqual(["hospital", "ems"]);
    expect(ASSET_CATEGORIES.ems.label).toBe("EMS stations");
    expect(sourceTag(assets[0])).toBe("ODHF · OSM");
    expect(sourceTag(assets[1])).toBe("OSM");
    expect(sourceTag({ ...assets[1], id: "edm:x" })).toBeNull(); // Edmonton rows unchanged
  });

  it("an outside-Edmonton run reaches the assets and roads near it", () => {
    const cell = (dLng: number, h: number) => ({ lat: HINTON.lat, lng: HINTON.lng + dLng, t: h * 60, intensity: 1000, fuel: "C2", fire_type: "surface" });
    const frames = [
      { time_hours: 1, area_ha: 0.75, perimeter: [], burned_cells: [cell(0, 0.5), cell(0.0007, 0.8), cell(0.0014, 1)] },
    ] as unknown as SimulationFrame[];
    const fire = fireFromFrames(frames)!;
    const reach = assetReach(fire, assetsFromGeoJSON(layer, { idPrefix: "ab:" }));
    expect(reach.get("ab:odhf#1")?.inside).toBe(0.5);
    expect(reach.has("ab:osm:node/2")).toBe(false); // ~670 m away: beyond 500 m
    const roads: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: { name: "Highway 16", highway: "trunk", ref: "16" }, geometry: { type: "LineString", coordinates: [[HINTON.lng - 0.01, HINTON.lat], [HINTON.lng + 0.01, HINTON.lat]] } }],
    };
    expect(roadReach(fire, roads).first.get("Highway 16")).toBe(0.5);
  });
});
