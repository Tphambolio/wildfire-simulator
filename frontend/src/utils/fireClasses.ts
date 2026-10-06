/**
 * Head fire intensity (HFI) classes, the one table used across the app.
 *
 * Limits (10 / 500 / 2,000 / 4,000 / 10,000 kW/m): Cole & Alexander (1995) and the CWFIS
 * head fire intensity map (GeoServer layer public:hfi, which also breaks at 30,000 kW/m).
 * Meanings: paraphrased from Cole & Alexander (1995), written for FBP fuel type C-2 on level to
 * gently undulating terrain. Their class 5 is everything above 4,000 kW/m; its "explosive"
 * upper portion above 10,000 kW/m is class 6 here. Class 2-5 equipment also follows Alexander
 * (2001, Table 1). Copies of the sources: ~/dev/wildfire/references/hfi-classes/.
 */

export interface HfiClass {
  num: 1 | 2 | 3 | 4 | 5 | 6;
  min: number; // kW/m, inclusive
  max: number; // kW/m, exclusive (Infinity for class 6)
  range: string;
  title: string; // short label
  meaning: string; // generalised fire behaviour and suppression implication
  equipment: string[]; // what the source says can work at the head
  directAttackAtHead: boolean;
  color: string; // inferno ramp (colour-vision-deficiency safe), see design spec §6.2
  textColor: string;
}

export const HFI_CLASSES: readonly HfiClass[] = [
  {
    num: 1, min: 0, max: 10, range: "< 10 kW/m", title: "Smouldering / self-limiting",
    meaning: "New ignitions rarely sustain themselves or spread far from their origin; control is easy. Mop-up may still be needed where fuel is dry enough to smoulder.",
    equipment: ["Hand tools for mop-up"],
    directAttackAtHead: true, color: "#f3f68a", textColor: "#1b1f24",
  },
  {
    num: 2, min: 10, max: 500, range: "10–500 kW/m", title: "Creeping to gentle surface fire",
    meaning: "Surface fire with flames under about 1.3 m. Control is fairly easy if attended to promptly; direct attack around the whole perimeter is possible.",
    equipment: ["Ground crews with hand tools", "Backpack pumps", "Light helicopter with bucket"],
    directAttackAtHead: true, color: "#fcae12", textColor: "#1b1f24",
  },
  {
    num: 3, min: 500, max: 2000, range: "500–2,000 kW/m", title: "Vigorous surface fire, torching",
    meaning: "Vigorous surface fire with flames to about 1.5 m and possible torching. Hand-built guard is likely to be challenged; effective action at the head needs water under pressure or machinery.",
    equipment: ["Pumps and hose lays", "Heavy machinery (e.g. dozers)", "Intermediate helicopter with bucket"],
    directAttackAtHead: true, color: "#eb6628", textColor: "#1b1f24",
  },
  {
    num: 4, min: 2000, max: 4000, range: "2,000–4,000 kW/m", title: "Intermittent crowning",
    meaning: "Intermittent crowning and short-range spotting; control is very difficult. Ground forces can attack the head only in the first minutes after ignition; otherwise attack at the head needs aircraft, and may fail.",
    equipment: ["Medium or heavy helicopters with buckets", "Airtankers with long-term retardant"],
    directAttackAtHead: false, color: "#bc3754", textColor: "#ffffff",
  },
  {
    num: 5, min: 4000, max: 10000, range: "4,000–10,000 kW/m", title: "Crown fire",
    meaning: "Crowning is common; control is extremely difficult. No direct attack on the head except right after ignition; work the flanks and back, or indirect attack.",
    equipment: ["Action on the flanks and back", "Indirect attack (e.g. aerial ignition) where available"],
    directAttackAtHead: false, color: "#84206b", textColor: "#ffffff",
  },
  {
    num: 6, min: 10000, max: Infinity, range: "> 10,000 kW/m", title: "Explosive",
    meaning: "Extreme fire behaviour: rapid spread, continuous crowning, medium- to long-range spotting, fire whirls. Control is virtually impossible until conditions ease; the only safe action is at the back and up the flanks.",
    equipment: ["No action at the head", "Back and flanks only, when safe"],
    directAttackAtHead: false, color: "#10092d", textColor: "#ffffff",
  },
] as const;

export const HFI_CLASS_SOURCE =
  "Cole & Alexander (1995), HFI class graph for FBP fuel type C-2; limits as on the CWFIS HFI map";

export const HFI_CLASS_CAVEAT =
  "Generalised interpretations written for C-2 on level ground. Local fuels, terrain and crew " +
  "safety decide; not a guide to firefighter safety.";

/** The HFI class of a head fire intensity (kW/m). */
export function hfiClass(hfi: number): HfiClass {
  const v = Number.isFinite(hfi) && hfi > 0 ? hfi : 0;
  return HFI_CLASSES.find((c) => v >= c.min && v < c.max) ?? HFI_CLASSES[HFI_CLASSES.length - 1];
}
