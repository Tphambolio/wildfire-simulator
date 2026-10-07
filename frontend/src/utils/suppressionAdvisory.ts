/** Suppression and RPAS advisory, shared by the EOC summary, the ICS-209 and the ICS forms. */

import type { HfiClass } from "./fireClasses";
import { HFI_CLASS_CAVEAT, HFI_CLASS_SOURCE, hfiClass } from "./fireClasses";

/**
 * Suppression advisory from the head fire intensity class of the peak HFI.
 *
 * Classes, meanings and equipment come from the shared table (utils/fireClasses.ts:
 * Cole & Alexander 1995; CWFIS HFI map limits). These are generalised interpretations,
 * not a resource order.
 *
 * There is no RPAS stand-off distance: FireSim used to print one (500 m, 1.5 x spot distance,
 * +1 km for crown fire) that had no source and sat inside the regulatory restrictions on
 * operating near forest fires. RPAS notes are a generic reminder only.
 */

/** Generic RPAS reminder for outputs (no distances: those come from the SFOC and CARs). */
export const RPAS_NOTE =
  "RPAS near an active wildfire: operate only with the fire authority's authorization and " +
  "within the applicable SFOC and Canadian Aviation Regulations restrictions on forest fire " +
  "areas; coordinate with air operations.";
export interface SuppressionAdvisory {
  intensityClass: HfiClass["num"];
  intensityLabel: string;
  color: string;
  textColor: string;
  strategy: string;
  strategyDetail: string;
  resources: string[];
  suppressionFeasible: boolean; // direct attack at the head
  rpasNotes: string[];
  source: string;
}

/** The run summary the advisory needs (callers may pass their full spread summary). */
export interface AdvisoryInput {
  peakHfiKwM: number;
  fireType: string;
  spotCount: number;
  maxSpotDistM: number;
}

export function buildSuppressionAdvisory(spread: AdvisoryInput): SuppressionAdvisory {
  const cls = hfiClass(spread.peakHfiKwM);
  const hasSpotting = spread.spotCount > 0;
  const rpasNotes: string[] = [RPAS_NOTE];
  if (hasSpotting) {
    rpasNotes.push(`Modelled spotting up to ${spread.maxSpotDistM.toFixed(0)} m: new ignitions may appear beyond the perimeter`);
  }

  return {
    intensityClass: cls.num,
    intensityLabel: `Class ${cls.num} (${cls.range})`,
    color: cls.color,
    textColor: cls.textColor,
    strategy: cls.title,
    strategyDetail: `${cls.meaning} ${HFI_CLASS_CAVEAT}`,
    resources: cls.equipment,
    suppressionFeasible: cls.directAttackAtHead,
    rpasNotes,
    source: HFI_CLASS_SOURCE,
  };
}
