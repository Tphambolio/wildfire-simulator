/**
 * One legend dock for the map (owner request 2026-10-11): every visible layer's legend in one
 * place (bottom left), each one minimisable to a chip (click to expand again), plus "Hide all".
 * A legend is listed only while its layer is drawn (the caller passes only those). The minimised
 * set and "hide all" are remembered in this browser.
 */

import { useCallback, useEffect, useId, useState, type ReactNode } from "react";

export interface LegendItem {
  id: string;
  title: string;
  /** Next to the title, outside the toggle button (badges, InfoTip) */
  extra?: ReactNode;
  /** A few colours for the minimised chip */
  swatches?: string[];
  testId?: string;
  className?: string;
  body: ReactNode;
}

interface LegendPrefs {
  hidden: boolean;
  minimized: string[];
}

export const LEGEND_KEY = "firesim-v3-legends";

function loadPrefs(): LegendPrefs {
  try {
    const raw = localStorage.getItem(LEGEND_KEY);
    const p = raw ? (JSON.parse(raw) as Partial<LegendPrefs>) : null;
    return {
      hidden: p?.hidden === true,
      minimized: Array.isArray(p?.minimized) ? p!.minimized.filter((x): x is string => typeof x === "string") : [],
    };
  } catch {
    return { hidden: false, minimized: [] };
  }
}

function savePrefs(p: LegendPrefs): void {
  try {
    localStorage.setItem(LEGEND_KEY, JSON.stringify(p));
  } catch {
    // storage blocked: the dock state lasts for this session
  }
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false" className="legend-chev">
      <path d={open ? "M2.5 4.5 6 8l3.5-3.5" : "M4.5 2.5 8 6 4.5 9.5"} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function LegendDock({ items }: { items: LegendItem[] }) {
  const [prefs, setPrefs] = useState<LegendPrefs>(loadPrefs);
  useEffect(() => savePrefs(prefs), [prefs]);
  const uid = useId();

  const toggleItem = useCallback((id: string) => {
    setPrefs((p) => ({
      ...p,
      minimized: p.minimized.includes(id) ? p.minimized.filter((x) => x !== id) : [...p.minimized, id],
    }));
  }, []);
  const setHidden = useCallback((hidden: boolean) => setPrefs((p) => ({ ...p, hidden })), []);

  if (items.length === 0) return null;

  if (prefs.hidden) {
    return (
      <div className="legend-dock legend-dock-hidden">
        <button
          type="button"
          className="legend-dock-show"
          aria-expanded={false}
          aria-label={`Show legends (${items.length})`}
          onClick={() => setHidden(false)}
        >
          <span className="legend-dock-show-dots" aria-hidden="true">
            {items.slice(0, 4).map((it) => (
              <span key={it.id} style={{ background: it.swatches?.[0] ?? "var(--text-3)" }} />
            ))}
          </span>
          Legends <span className="legend-count">{items.length}</span>
        </button>
      </div>
    );
  }

  const open = items.filter((it) => !prefs.minimized.includes(it.id));
  const chips = items.filter((it) => prefs.minimized.includes(it.id));

  return (
    <section className="legend-dock" aria-label="Map legends">
      <div className="legend-dock-head">
        <span className="legend-dock-title">Legend</span>
        <button
          type="button"
          className="legend-dock-hide"
          aria-expanded={true}
          aria-label="Hide all legends"
          title="Hide all legends"
          onClick={() => setHidden(true)}
        >
          Hide all
        </button>
      </div>
      {chips.length > 0 && (
        <div className="legend-chips">
          {chips.map((it) => (
            <button
              key={it.id}
              type="button"
              className="legend-chip"
              data-testid={it.testId}
              aria-expanded={false}
              aria-label={`Expand legend: ${it.title}`}
              onClick={() => toggleItem(it.id)}
            >
              {it.swatches && it.swatches.length > 0 && (
                <span className="legend-chip-swatches" aria-hidden="true">
                  {it.swatches.slice(0, 4).map((c, i) => <span key={i} style={{ background: c }} />)}
                </span>
              )}
              {it.title}
            </button>
          ))}
        </div>
      )}
      <div className="legend-cards">
        {open.map((it) => {
          const bodyId = `${uid}-${it.id}`;
          return (
            <div key={it.id} className={`legend-card${it.className ? ` ${it.className}` : ""}`} data-testid={it.testId}>
              <div className="legend-card-head">
                <button
                  type="button"
                  className="legend-card-toggle"
                  aria-expanded={true}
                  aria-controls={bodyId}
                  aria-label={`Minimize legend: ${it.title}`}
                  onClick={() => toggleItem(it.id)}
                >
                  <Chevron open />
                  <span className="legend-card-title">{it.title}</span>
                </button>
                {it.extra}
              </div>
              <div id={bodyId} className="legend-card-body">
                {it.body}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
