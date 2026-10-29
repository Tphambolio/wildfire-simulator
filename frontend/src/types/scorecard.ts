/**
 * Validation scorecard served by GET /api/v1/scorecard: the latest dated
 * docs/scorecard/<date>.json built by scripts/make_scorecard.py (docs/validation-scorecard.md).
 * Read-only in the app; every number carries its report number and source file.
 */

export type ScorecardStatus =
  | "validated"
  | "partly_validated"
  | "not_validated"
  | "failed_test"
  | "pass_opt_in"
  | "pending";

export interface ScorecardSource {
  report: string;
  kind: "file" | "doc" | "report text";
  file: string;
  path?: string;
  sha256?: string;
  note?: string;
}

export interface ScorecardDelta {
  vs: string;
  mean: number | null;
  ci95: [number | null, number | null];
  verdict: string;
  source: ScorecardSource;
}

export interface ScorecardComparator {
  label: string;
  value: number | number[] | null;
  display: string;
  source: ScorecardSource;
}

export interface ScorecardVersion {
  version: string;
  test: string;
  verdict: string;
  detail: string;
  passed: boolean | null;
  source: ScorecardSource;
}

export interface ScorecardMetric {
  id: string;
  group: "equations" | "spread" | "ensemble" | "structures" | "embers" | string;
  label: string;
  status: ScorecardStatus;
  kind: "verification" | "validation";
  value: number | null;
  /** Pre-formatted value as the record quotes it ("0.246", "49 %", "κ 0.472") */
  display: string;
  unit: string;
  ci95: [number, number] | null;
  ci95_note?: string;
  /** A CI the script computed from the per-fire-day file (labelled derived; not in the report) */
  ci95_derived?: { lo: number; hi: number; method: string; file: string };
  n?: number;
  n_note?: string;
  delta?: ScorecardDelta | null;
  comparators?: ScorecardComparator[];
  reference: { label: string; value: number | number[] | null; note: string };
  /** One-line plain-English reading (shown after the value) */
  gloss: string;
  source: ScorecardSource;
  versions?: ScorecardVersion[];
}

export interface Scorecard {
  schema_version: number;
  title: string;
  date: string;
  git_commit: string;
  engine_version: string;
  report: string | null;
  framing: string;
  fuel_grid: Record<string, string>;
  data: Record<string, string>;
  metrics: ScorecardMetric[];
  unsourced_from_files: string[];
  /** Added by the API: the file served and every dated scorecard on the server */
  file?: string;
  available_dates?: string[];
}
