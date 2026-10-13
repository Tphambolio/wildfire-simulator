import { describe, expect, it } from "vitest";
import { neighbourhoodArrivals } from "./evacZones";
import { assetReach, fireFromFrames, type Asset } from "./assets";
import { collection, frame, neighbourhood, square } from "../test/frames";

/** Owner bug 3: after a restart, "within 500 m by" keeps the chain's earliest time. */
describe("arrival times carried across perimeter restarts", () => {
  const town = collection([neighbourhood("Town", 0.02)]);
  // The restart's own fire reaches the town at T+1 (its start + 1 h)
  const restart = [frame(0, square(0.005)), frame(1, square(0.02))];

  it("without history the restart's own time is used", () => {
    expect(neighbourhoodArrivals(restart, town)[0].arrivalHours).toBe(1);
  });

  it("an earlier run's point near the town, before the restart, wins (negative hours)", () => {
    const carried = [{ lat: 53.5 + 0.02, lng: -113.5, h: -0.07 }];
    const a = neighbourhoodArrivals(restart, town, 500, carried);
    expect(a[0].arrivalHours).toBeCloseTo(-0.07);
  });

  it("assets keep the carried time too", () => {
    const asset: Asset = {
      id: "a1", name: "RCMP", category: "emergency", source: "test",
      geometry: { type: "Point", coordinates: [-113.5, 53.52] }, anchor: [-113.5, 53.52],
    } as unknown as Asset;
    const fire = fireFromFrames(restart, [{ lat: 53.52, lng: -113.5, h: -0.5 }]);
    expect(assetReach(fire, [asset]).get("a1")?.near).toBeCloseTo(-0.5);
  });
});
