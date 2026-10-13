/** Optional label for the next run (saved-runs list; plain text, at most 80 characters). */

import { memo, useId } from "react";
import { LABEL_MAX } from "../services/savedRuns";

interface RunLabelFieldProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

function RunLabelField({ value, onChange, disabled }: RunLabelFieldProps) {
  const id = useId();
  return (
    <label className="field run-label-field" htmlFor={id}>
      Run label (optional)
      <input
        id={id}
        type="text"
        value={value}
        maxLength={LABEL_MAX}
        placeholder="e.g. Day 2 west flank, wind shift"
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </label>
  );
}

export default memo(RunLabelField);
