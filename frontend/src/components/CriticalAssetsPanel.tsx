/**
 * Critical assets card (Situation panel): when the modelled fire reaches each asset and major
 * road, in clock time, like the Neighbourhoods card. With an ensemble, the worst-credible (P10)
 * time leads and the single run is beside it. Model output only: no recommendation is drawn
 * from it (Travis, 2026-10-08).
 */

import { memo, useId } from "react";
import {
  ASSET_CATEGORIES,
  CATEGORY_ORDER,
  assetReachPhrases,
  groupRows,
  roadReachPhrase,
  type Asset,
  type AssetCategory,
  type AssetRow,
  type RoadRow,
} from "../utils/assets";
import { ARRIVAL_BUFFER_M } from "../utils/evacZones";
import AssetSymbol from "./AssetSymbol";

interface CriticalAssetsPanelProps {
  assets: Asset[];
  rows: AssetRow[];
  roads: RoadRow[];
  hasEnsemble: boolean;
  scenarioStart: Date | null;
  hasRun: boolean;
  /** Categories shown (card and map) */
  shown: ReadonlySet<AssetCategory>;
  onToggleCategory: (c: AssetCategory) => void;
  onShowAll: () => void;
  onFocus: (asset: Asset) => void;
  assetsVisible: boolean;
  onAssetsVisible: (v: boolean) => void;
  roadsVisible: boolean;
  onRoadsVisible: (v: boolean) => void;
  /** Source lines for the footer (bundled data and user layers) */
  sources: string[];
  /** Loading state of the bundled Edmonton layers */
  loadNote?: string | null;
}

function CriticalAssetsPanel({
  assets,
  rows,
  roads,
  hasEnsemble,
  scenarioStart,
  hasRun,
  shown,
  onToggleCategory,
  onShowAll,
  onFocus,
  assetsVisible,
  onAssetsVisible,
  roadsVisible,
  onRoadsVisible,
  sources,
  loadNote = null,
}: CriticalAssetsPanelProps) {
  const id = useId();
  const counts = new Map<AssetCategory, number>();
  for (const a of assets) counts.set(a.category, (counts.get(a.category) ?? 0) + 1);
  const reachedBy = new Map<AssetCategory, number>();
  for (const r of rows) reachedBy.set(r.asset.category, (reachedBy.get(r.asset.category) ?? 0) + 1);
  const categories = CATEGORY_ORDER.filter((c) => counts.has(c));
  const visibleRows = rows.filter((r) => shown.has(r.asset.category));
  const groups = groupRows(visibleRows);
  const allShown = categories.every((c) => shown.has(c));

  return (
    <section className="assets-card" aria-labelledby={`${id}-h`} data-testid="critical-assets">
      <h3 id={`${id}-h`} className="assets-card-h">Critical assets</h3>
      <p className="hint-sm">
        When the modelled fire comes within {ARRIVAL_BUFFER_M} m of each asset, and inside it, for this run
        {hasEnsemble ? ": worst-credible (P10 of the ensemble) first, the single run beside it" : ""}. Model output, not
        an instruction.
      </p>

      {assets.length === 0 ? (
        <p className="hint-sm assets-empty">{loadNote ?? "No asset layer loaded. Add one under Setup, Assets, roads & isochrones."}</p>
      ) : (
        <>
          <div className="assets-filter" role="group" aria-label="Show categories">
            {categories.map((c) => (
              <button
                key={c}
                type="button"
                className="assets-chip"
                aria-pressed={shown.has(c)}
                onClick={() => onToggleCategory(c)}
                title={ASSET_CATEGORIES[c].label}
              >
                <AssetSymbol category={c} size={18} />
                <span className="assets-chip-label">{ASSET_CATEGORIES[c].label}</span>
                <span className="assets-chip-count">
                  {hasRun ? `${reachedBy.get(c) ?? 0}/${counts.get(c)}` : counts.get(c)}
                </span>
              </button>
            ))}
            {!allShown && (
              <button type="button" className="assets-chip assets-chip-all" onClick={onShowAll}>
                Show all
              </button>
            )}
          </div>

          {!hasRun ? (
            <p className="hint-sm assets-empty">Run a simulation to see when the modelled fire reaches each asset.</p>
          ) : groups.length === 0 ? (
            <p className="hint-sm assets-empty" data-testid="assets-none">
              {rows.length === 0
                ? `No asset within ${ARRIVAL_BUFFER_M} m of the modelled fire.`
                : "Reached assets are in hidden categories."}
            </p>
          ) : (
            groups.map((g) => (
              <div key={g.category} className="assets-group">
                <h4 className="assets-group-h">
                  <AssetSymbol category={g.category} reached size={18} />
                  {ASSET_CATEGORIES[g.category].label} <span className="assets-group-n">({g.rows.length})</span>
                </h4>
                <ul className="assets-list">
                  {g.rows.map((r) => (
                    <li key={r.asset.id}>
                      <button
                        type="button"
                        className="asset-row"
                        onClick={() => onFocus(r.asset)}
                        aria-label={`${r.asset.name}: ${assetReachPhrases(r, scenarioStart, hasEnsemble).join("; ")}. Show on the map`}
                      >
                        <span className="asset-row-name">{r.asset.name}</span>
                        {assetReachPhrases(r, scenarioStart, hasEnsemble).map((t) => (
                          <span key={t} className={`asset-row-time${t.startsWith("Inside") ? " asset-row-inside" : ""}`}>
                            {t}
                          </span>
                        ))}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </>
      )}

      <h4 className="assets-group-h assets-roads-h">
        <span className="road-swatch" aria-hidden="true" /> Major roads reached
      </h4>
      {!hasRun ? (
        <p className="hint-sm assets-empty">Run a simulation to see when the modelled fire reaches each major road.</p>
      ) : roads.length === 0 ? (
        <p className="hint-sm assets-empty" data-testid="roads-none">No major road reached by the modelled fire.</p>
      ) : (
        <ul className="assets-list roads-list" aria-label="Major roads reached by the modelled fire">
          {roads.map((r) => (
            <li key={r.name} className="road-row">
              <span className="asset-row-name">{r.name}</span>
              <span className="asset-row-time">{roadReachPhrase(r, scenarioStart, hasEnsemble)}</span>
            </li>
          ))}
        </ul>
      )}

      <label>
        <input type="checkbox" checked={assetsVisible} onChange={(e) => onAssetsVisible(e.target.checked)} />
        Show assets on the map
      </label>
      <label>
        <input type="checkbox" checked={roadsVisible} onChange={(e) => onRoadsVisible(e.target.checked)} />
        Show reached roads on the map
      </label>
      {sources.length > 0 && (
        <p className="hint-sm assets-sources">
          Sources: {sources.join(" · ")}
        </p>
      )}
    </section>
  );
}

export default memo(CriticalAssetsPanel);
