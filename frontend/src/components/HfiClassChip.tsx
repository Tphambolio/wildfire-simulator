/** Head fire intensity class chip ("Class 4"); the class meaning, caveat and source are in its tip. */

import { HFI_CLASS_CAVEAT, HFI_CLASS_SOURCE, hfiClass } from "../utils/fireClasses";
import Badge from "./Badge";

export function hfiClassTip(hfi: number): string {
  const c = hfiClass(hfi);
  return `Class ${c.num} (${c.range}), ${c.title.toLowerCase()}: ${c.meaning} ${HFI_CLASS_CAVEAT} Source: ${HFI_CLASS_SOURCE}.`;
}

export default function HfiClassChip({ hfi }: { hfi: number }) {
  const c = hfiClass(hfi);
  return (
    <Badge className="hfi-chip" style={{ background: c.color, color: c.textColor }} tip={hfiClassTip(hfi)}>
      Class {c.num}
    </Badge>
  );
}
