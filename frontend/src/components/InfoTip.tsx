/**
 * Small "i" button whose text shows on hover or keyboard focus (role="tooltip", linked by
 * aria-describedby). For caveats and explanations that should not take panel space.
 */
import { useId, useState } from "react";

interface InfoTipProps {
  text: string;
  /** Accessible name of the button, e.g. "About house-to-house spread" */
  label: string;
  /** Open the tooltip toward the left (for controls near the right edge) */
  alignRight?: boolean;
}

export default function InfoTip({ text, label, alignRight = false }: InfoTipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span
      className={`info-tip${alignRight ? " info-tip-right" : ""}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="info-tip-btn"
        aria-label={label}
        aria-describedby={id}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
      >
        i
      </button>
      <span role="tooltip" id={id} className={`info-tip-text${open ? " open" : ""}`}>
        {text}
      </span>
    </span>
  );
}
