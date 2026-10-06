import { describe, expect, it } from "vitest";
import { HFI_CLASSES, hfiClass } from "./fireClasses";
import { RPAS_NOTE, buildSuppressionAdvisory } from "./suppressionAdvisory";

describe("hfiClass (Cole & Alexander 1995; CWFIS HFI map limits)", () => {
  it.each([
    [0, 1], [9.99, 1], [10, 2], [499.9, 2], [500, 3], [1999, 3], [2000, 4],
    [3999, 4], [4000, 5], [9999, 5], [10000, 6], [60000, 6],
  ])("%s kW/m is class %s", (hfi, num) => {
    expect(hfiClass(hfi).num).toBe(num);
  });

  it("treats missing or negative intensity as class 1", () => {
    expect(hfiClass(NaN).num).toBe(1);
    expect(hfiClass(-5).num).toBe(1);
  });

  it("has contiguous classes 1-6 with meanings and accessible chip text", () => {
    HFI_CLASSES.forEach((c, i) => {
      expect(c.num).toBe(i + 1);
      if (i > 0) expect(c.min).toBe(HFI_CLASSES[i - 1].max);
      expect(c.meaning.length).toBeGreaterThan(40);
      expect(c.textColor === "#ffffff" || c.textColor === "#1b1f24").toBe(true);
    });
    expect(HFI_CLASSES.filter((c) => c.directAttackAtHead).map((c) => c.num)).toEqual([1, 2, 3]);
  });
});

describe("buildSuppressionAdvisory", () => {
  const base = { fireType: "surface", spotCount: 0, maxSpotDistM: 0 };

  it("uses the shared class table, not the old I-V scheme", () => {
    const adv = buildSuppressionAdvisory({ ...base, peakHfiKwM: 3000 });
    expect(adv.intensityClass).toBe(4);
    expect(adv.intensityLabel).toBe("Class 4 (2,000–4,000 kW/m)");
    expect(adv.suppressionFeasible).toBe(false);
    expect(adv.resources.join(" ")).not.toMatch(/IHC|Type [1-5]|SEAT|VLAT/);
    expect(adv.source).toMatch(/Cole & Alexander \(1995\)/);
  });

  it("gives no RPAS stand-off distance, only the generic authorization reminder", () => {
    const crown = buildSuppressionAdvisory({
      peakHfiKwM: 12000, fireType: "active_crown", spotCount: 3, maxSpotDistM: 800,
    });
    expect(crown).not.toHaveProperty("rpasStandoffM");
    expect(crown.rpasNotes[0]).toBe(RPAS_NOTE);
    expect(crown.rpasNotes[0]).not.toMatch(/\d|stand-?off/i); // no distances in the reminder
    expect(crown.intensityClass).toBe(6);
  });
});
