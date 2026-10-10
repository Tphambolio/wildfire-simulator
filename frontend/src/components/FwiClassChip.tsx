/** FWI class chip ("Very High"): the CWFIS FWI map class, with "not an official danger rating" in its tip. */

import { TIPS } from "../content/explanations";
import { fwiClass, fwiClassColor, fwiClassTextColor } from "../utils/fwiClass";
import Badge from "./Badge";

export default function FwiClassChip({ fwi, label, className = "" }: { fwi: number; label?: string; className?: string }) {
  return (
    <Badge
      className={`fwi-class-chip ${className}`.trim()}
      style={{ background: fwiClassColor(fwi), color: fwiClassTextColor(fwi) }}
      tip={TIPS.fwiClass}
    >
      {label ?? fwiClass(fwi)}
    </Badge>
  );
}
