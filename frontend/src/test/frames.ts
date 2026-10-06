/** Small synthetic frames and features for unit tests. */
import type { SimulationFrame } from "../types/simulation";

const LAT0 = 53.5;
const LNG0 = -113.5;

/** Square perimeter ([[lat, lng]], engine convention) of half-width `d` degrees around the origin. */
export function square(d: number, lat = LAT0, lng = LNG0): number[][] {
  return [
    [lat - d, lng - d],
    [lat - d, lng + d],
    [lat + d, lng + d],
    [lat + d, lng - d],
  ];
}

export function frame(time_hours: number, perimeter: number[][], extra: Partial<SimulationFrame> = {}): SimulationFrame {
  return {
    time_hours,
    perimeter,
    area_ha: 0,
    head_ros_m_min: 0,
    max_hfi_kw_m: 0,
    fire_type: "SURFACE",
    flame_length_m: 0,
    fuel_breakdown: {},
    spot_fires: null,
    burned_cells: null,
    num_fronts: 1,
    ...extra,
  };
}

/** A fire whose half-width grows by 0.01 degrees per hour, one frame per hour 1..hours. */
export function growingFire(hours: number): SimulationFrame[] {
  return Array.from({ length: hours }, (_, i) => frame(i + 1, square(0.01 * (i + 1))));
}

/** A small square neighbourhood polygon (GeoJSON [lng, lat]) centred `dLat` degrees north of the origin. */
export function neighbourhood(name: string, dLat: number, dLng = 0): GeoJSON.Feature {
  const lat = LAT0 + dLat;
  const lng = LNG0 + dLng;
  const h = 0.001;
  return {
    type: "Feature",
    properties: { name },
    geometry: {
      type: "Polygon",
      coordinates: [[
        [lng - h, lat - h],
        [lng + h, lat - h],
        [lng + h, lat + h],
        [lng - h, lat + h],
        [lng - h, lat - h],
      ]],
    },
  };
}

export function collection(features: GeoJSON.Feature[]): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features };
}
