/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_MAPBOX_TOKEN: string;
  /** "1" turns exercise mode on (docs/exercise-mode-spec.md §10); also `?exercise=1` */
  readonly VITE_FIRESIM_EXERCISE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
