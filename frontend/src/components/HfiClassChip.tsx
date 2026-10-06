/** Head fire intensity class chip ("Class 4"), with the class meaning as a tooltip. */

import { HFI_CLASS_CAVEAT, HFI_CLASS_SOURCE, hfiClass } from "../utils/fireClasses";

export default function HfiClassChip({ hfi }: { hfi: number }) {
  const c = hfiClass(hfi);
  return (
    <span
      className="hfi-chip"
      style={{ background: c.color, color: c.textColor }}
      title={`Class ${c.num} (${c.range}): ${c.meaning}\n${HFI_CLASS_CAVEAT}\nSource: ${HFI_CLASS_SOURCE}`}
    >
      Class {c.num}
    </span>
  );
}
