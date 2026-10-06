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
 * The RPAS stand-off is a FireSim rule of thumb (500 m, or 1.5 x the longest modelled spot
 * distance, plus 1 km for crown fire). It has no published source; the IC or air operations
 * set the actual stand-off.
 */
export interface SuppressionAdvisory {
  intensityClass: HfiClass["num"];
  intensityLabel: string;
  color: string;
  textColor: string;
  strategy: string;
  strategyDetail: string;
  resources: string[];
  suppressionFeasible: boolean; // direct attack at the head
  rpasStandoffM: number;
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
  const isCrownFire = spread.fireType.toLowerCase().includes("crown");
  const hasSpotting = spread.spotCount > 0;
  const maxSpotM = spread.maxSpotDistM;

  const baseStandoffM = hasSpotting ? Math.max(500, maxSpotM * 1.5) : 500;
  const rpasStandoffM = isCrownFire ? baseStandoffM + 1000 : baseStandoffM;
  const rpasNotes: string[] = [
    `Suggested stand-off ${rpasStandoffM.toFixed(0)} m from the active perimeter (FireSim rule of thumb, not a published standard)`,
    "IC / air operations authorization required before any RPAS flight near active fire",
    "Maintain visual line of sight; assign a dedicated observer",
  ];
  if (isCrownFire) rpasNotes.push("Crown fire: strong smoke-column turbulence; higher loss-of-control risk");
  if (hasSpotting) rpasNotes.push(`Modelled spotting up to ${maxSpotM.toFixed(0)} m: check for new ignitions beyond the perimeter before flying`);

  return {
    intensityClass: cls.num,
    intensityLabel: `Class ${cls.num} (${cls.range})`,
    color: cls.color,
    textColor: cls.textColor,
    strategy: cls.title,
    strategyDetail: `${cls.meaning} ${HFI_CLASS_CAVEAT}`,
    resources: cls.equipment,
    suppressionFeasible: cls.directAttackAtHead,
    rpasStandoffM,
    rpasNotes,
    source: HFI_CLASS_SOURCE,
  };
}
