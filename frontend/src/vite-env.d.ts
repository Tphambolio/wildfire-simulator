/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_MAPBOX_TOKEN: string;
  /** "1" turns exercise mode on (docs/exercise-mode-spec.md §10); also `?exercise=1` */
  readonly VITE_FIRESIM_EXERCISE?: string;
  /** Usage counting (docs/privacy.md): "none" (default) | "cloudflare" | "post" */
  readonly VITE_FIRESIM_TELEMETRY?: string;
  readonly VITE_FIRESIM_TELEMETRY_TOKEN?: string;
  readonly VITE_FIRESIM_TELEMETRY_ENDPOINT?: string;
  /** Shows the feedback dialog's "Email" option */
  readonly VITE_FIRESIM_FEEDBACK_EMAIL?: string;
}

/** Commit the bundle was built from (vite.config.ts define), "" locally */
declare const __FIRESIM_BUILD__: string;

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
