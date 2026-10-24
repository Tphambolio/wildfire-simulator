/**
 * EOC Command Console — full-viewport tabbed incident management page.
 *
 * Layout:
 *   Left 45%  : read-only MapLibre map (same simulation state, no click-to-ignite)
 *   Right 55% : sub-tabbed content — Situation / ICS Forms / Map (full-width)
 *
 * Sub-tabs:
 *   Situation  → existing EOCSummary component (conditions, metrics, advisories, exports)
 *   ICS Forms  → ICS-201, 202, 203, 204, 205, 206, 209, Full IAP — rendered in iframe
 *   Map        → full-width map (hides data panel)
 *
 * Map snapshot: captured from MapLibre canvas (preserveDrawingBuffer: true) before
 * any print or ICS form render — embedded as base64 PNG in form HTML. The capture waits at
 * most SNAPSHOT_WAIT_MS for a repaint (none comes while the page is hidden).
 *
 * Design: Stitch EOC Tactical Dark system — indigo-ember palette, no-line rule,
 * tonal layering. Reference: /tmp/stitch_export/stitch/stitch/eoc_tactical_dark/DESIGN.md
 */

import { useState, useRef, useCallback, useEffect } from "react";
import maplibregl from "maplibre-gl";
import MapView from "./MapView";
import EOCSummary from "./EOCSummary";
import InfoTip from "./InfoTip";
import { TIPS } from "../content/explanations";
import AnnotationSymbolPicker, { SymbolIcon } from "./AnnotationSymbolPicker";
import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";
import type { RunParams } from "./WeatherPanel";
import type { PlanningEvacZone } from "../utils/evacZones";
import type { CriticalReach } from "../utils/assets";
import type { Isochrone } from "../utils/isochrones";
import {
  buildICS201HTML,
  buildICS202HTML,
  buildICS203HTML,
  buildICS204HTML,
  buildICS205HTML,
  buildICS206HTML,
  buildICS214HTML,
  buildFullIAPHTML,
} from "../utils/icsForms";
import { ghostPerimeterPath } from "../utils/ghostPerimeter";
import { buildICS209HTML, modelVersionWithin, type ICS209RunContext } from "../utils/ics209";
import { getVersion } from "../services/api";
import type { SuppressionAdvisory } from "../utils/suppressionAdvisory";
import { buildSuppressionAdvisory } from "../utils/suppressionAdvisory";
import type { AnnotationLayer, ICSSymbolKey, IncidentAnnotation } from "../types/incident";
import { SYMBOL_DEFS } from "../types/incident";
import { normaliseIncidentName, UNTITLED_INCIDENT } from "../utils/storage";

// ── Props ─────────────────────────────────────────────────────────────────────

interface EOCConsoleProps {
  // Simulation data
  frames: SimulationFrame[];
  currentFrameIndex: number;
  burnProbabilityData?: BurnProbabilityResponse | null;
  showBurnProbView?: boolean;
  // Run context
  runParams: RunParams | null;
  ignitionPoint: { lat: number; lng: number } | null;
  fuelTypeLabel?: string;
  // Overlays
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayCommunitiesVisible?: boolean;
  /** Critical assets (map symbols) and major roads reached by the modelled fire */
  assetPoints?: GeoJSON.FeatureCollection | null;
  assetsVisible?: boolean;
  roadsReached?: GeoJSON.FeatureCollection | null;
  roadsReachedVisible?: boolean;
  assetAttribution?: string | null;
  /** Assets and roads reached by the modelled fire (model output, for the summary and forms) */
  criticalReach?: CriticalReach | null;
  // Evacuation status set by Planning
  evacZones?: PlanningEvacZone[];
  evacZonesVisible?: boolean;
  // Isochrones
  isochrones?: Isochrone[];
  isochronesVisible?: boolean;
  // Fuel grid
  fuelGridImage?: { image: string; bounds: [number, number, number, number]; legend?: Array<{ fuel: string; color: string }> } | null;
  fuelGridVisible?: boolean;
  // Incident annotation store (from useIncident)
  incidentAnnotations?: IncidentAnnotation[];
  onAddAnnotation?: (a: IncidentAnnotation) => void;
  onRemoveAnnotation?: (id: string) => void;
  onClearLayer?: (layer: AnnotationLayer) => void;
  onFetchFacilities?: () => Promise<number>;
  /** Previous operational period's final perimeter — shown as ghost on Day 2+ */
  ghostPerimeter?: [number, number][] | null; // [lat, lng] pairs (API frame order)
  incidentName?: string;
  /** Run ID, start time and ensemble, for the ICS 209-WF */
  run209?: ICS209RunContext | null;
  /** Active operational period, e.g. "Day 2 · 2026-10-11 08:00–20:00" (forms) */
  operationalPeriod?: string;
  /** Span the shown run models (beside the operational period in the forms; R11) */
  runSpan?: string | null;
  /** Active operational period's day (tag on new map annotations) */
  operationalDay?: number;
  /** The active period's saved run, when it is not the run shown (forms say it is not loaded) */
  periodRunId?: string | null;
  onIncidentNameChange?: (name: string) => void;
}

type ConsoleTab = "situation" | "ics-forms" | "map";

type ICSFormId =
  | "ics201" | "ics202" | "ics203" | "ics204" | "ics205" | "ics206"
  | "ics209" | "ics214" | "full-iap";

const ICS_FORM_LABELS: Record<ICSFormId, string> = {
  ics201: "ICS-201 Briefing",
  ics202: "ICS-202 Objectives",
  ics203: "ICS-203 Organization",
  ics204: "ICS-204 Assignments",
  ics205: "ICS-205 Comms Plan",
  ics206: "ICS-206 Medical Plan",
  ics209: "ICS 209-WF Status Summary",
  ics214: "ICS-214 Activity Log",
  "full-iap": "Full IAP Package",
};

/** Longest wait for a map repaint before the snapshot is taken from the last drawn frame. */
const SNAPSHOT_WAIT_MS = 1500;

/**
 * PNG data URL of the map canvas with the markup SVG on top (map only if the overlay fails),
 * or undefined if the canvas cannot be read. The map pixels are copied synchronously, so this
 * can run inside the map's 'render' event or, with a preserved drawing buffer, at any time.
 */
function composeMapSnapshot(glCanvas: HTMLCanvasElement, svg: SVGSVGElement | null): Promise<string | undefined> {
  try {
    const rect = glCanvas.getBoundingClientRect();
    const w = Math.round(rect.width);
    const h = Math.round(rect.height);
    if (w <= 0 || h <= 0) return Promise.resolve(undefined);
    const offscreen = document.createElement("canvas");
    offscreen.width = w;
    offscreen.height = h;
    const ctx = offscreen.getContext("2d");
    if (!ctx) return Promise.resolve(undefined);
    // The WebGL map layer, scaled from physical to CSS pixels
    ctx.drawImage(glCanvas, 0, 0, w, h);
    const png = () => {
      try {
        return offscreen.toDataURL("image/png");
      } catch {
        return undefined;
      }
    };
    if (!svg || !svg.childElementCount) return Promise.resolve(png());
    // Composite the SVG annotation overlay on top
    const clone = svg.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(w));
    clone.setAttribute("height", String(h));
    const svgStr = new XMLSerializer().serializeToString(clone);
    const url = URL.createObjectURL(new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" }));
    return new Promise((resolve) => {
      const img = new Image();
      const done = () => {
        URL.revokeObjectURL(url);
        resolve(png());
      };
      img.onload = () => {
        ctx.drawImage(img, 0, 0);
        done();
      };
      img.onerror = done; // overlay failed: map-only snapshot
      img.src = url;
    });
  } catch {
    return Promise.resolve(undefined);
  }
}

export default function EOCConsole({
  frames,
  currentFrameIndex,
  burnProbabilityData = null,
  showBurnProbView = false,
  runParams,
  ignitionPoint,
  fuelTypeLabel,
  overlayCommunities = null,
  overlayCommunitiesVisible = true,
  assetPoints = null,
  assetsVisible = true,
  roadsReached = null,
  roadsReachedVisible = true,
  assetAttribution = null,
  criticalReach = null,
  evacZones = [],
  evacZonesVisible = true,
  isochrones = [],
  isochronesVisible = false,
  fuelGridImage = null,
  fuelGridVisible = true,
  incidentAnnotations = [],
  onAddAnnotation,
  onRemoveAnnotation,
  onClearLayer,
  onFetchFacilities,
  ghostPerimeter = null,
  incidentName: incidentNameProp,
  run209 = null,
  operationalPeriod,
  runSpan = null,
  operationalDay = 1,
  periodRunId = null,
  onIncidentNameChange,
}: EOCConsoleProps) {
  const [consoleTab, setConsoleTab] = useState<ConsoleTab>("situation");
  // Use incident name from prop if provided (incident store), else local state
  const [localIncidentName, setLocalIncidentName] = useState(UNTITLED_INCIDENT);
  const incidentName = incidentNameProp ?? localIncidentName;
  // Name being edited (null = not editing): may be empty while typing; blur/Enter stores an
  // empty name as "Untitled incident" (E9), Esc cancels
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const commitName = () => {
    if (nameDraft === null) return;
    const name = normaliseIncidentName(nameDraft);
    setLocalIncidentName(name);
    onIncidentNameChange?.(name);
    setNameDraft(null);
  };
  const [activeLayer, setActiveLayer] = useState<AnnotationLayer>("situation");
  const [activeSymbolKey, setActiveSymbolKey] = useState<ICSSymbolKey | null>(null);
  const [activeColor, setActiveColor] = useState<string | null>(null);
  const [showSymbolPicker, setShowSymbolPicker] = useState(false);
  const [selectedForm, setSelectedForm] = useState<ICSFormId>("ics201");
  const [renderedForm, setRenderedForm] = useState<{ html: string; period?: string; frames: SimulationFrame[] }>({ html: "", frames: [] });
  // A form generated for another operational period or run is not shown (QA 2026-10-11: after
  // switching periods the viewer kept the previous period's form); select it again to refresh
  const formHtml = renderedForm.period === operationalPeriod && renderedForm.frames === frames ? renderedForm.html : "";
  const [mapSnapshot, setMapSnapshot] = useState<string | undefined>(undefined);
  const consoleMapRef = useRef<maplibregl.Map | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // ── Layer visibility ─────────────────────────────────────────────────────
  const [spotFiresVisible, setSpotFiresVisible] = useState(true);
  const [isFetchingFacilities, setIsFetchingFacilities] = useState(false);
  const [fetchFacilitiesMsg, setFetchFacilitiesMsg] = useState<string | null>(null);

  // ── Map markup state ─────────────────────────────────────────────────────
  // All markup is stored in geographic (LngLat) coordinates so it stays
  // anchored to the map when panning or zooming.
  type MarkupTool = "pen" | "text" | null;
  type GeoPoint = { lng: number; lat: number };

  const [markupTool, setMarkupTool] = useState<MarkupTool>(null);
  const [penPaths, setPenPaths] = useState<GeoPoint[][]>([]); // each path = array of LngLat
  const [currentPenPath, setCurrentPenPath] = useState<GeoPoint[]>([]);
  const [textMarkers, setTextMarkers] = useState<Array<{ geo: GeoPoint; text: string }>>([]);
  const [pendingTextPos, setPendingTextPos] = useState<{ x: number; y: number } | null>(null);
  const [mapRenderKey, setMapRenderKey] = useState(0); // bumped on map move → forces geo→pixel recalc
  const isPenDownRef = useRef(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const textInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (pendingTextPos) textInputRef.current?.focus(); }, [pendingTextPos]);

  const getSvgCoords = useCallback((e: React.MouseEvent): { x: number; y: number } => {
    const rect = svgRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }, []);

  // Convert SVG pixel coords → geographic LngLat
  const pixelToGeo = useCallback((x: number, y: number): GeoPoint => {
    const map = consoleMapRef.current;
    if (!map) return { lng: 0, lat: 0 };
    const ll = map.unproject([x, y]);
    return { lng: ll.lng, lat: ll.lat };
  }, []);

  // Convert geographic LngLat → SVG pixel coords (depends on mapRenderKey so it refreshes on move)
  const geoToPixel = useCallback((geo: GeoPoint): { x: number; y: number } => {
    const map = consoleMapRef.current;
    if (!map) return { x: 0, y: 0 };
    const p = map.project([geo.lng, geo.lat]);
    return { x: p.x, y: p.y };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapRenderKey]); // intentionally depends on render key so pixels refresh after map moves

  // Build an SVG path string from an array of geo points
  const geoPathToSvgD = useCallback((points: GeoPoint[]): string => {
    if (points.length === 0) return "";
    return points.map((geo, i) => {
      const { x, y } = geoToPixel(geo);
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");
  }, [geoToPixel]);

  const handleSvgMouseDown = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    e.preventDefault();
    const { x, y } = getSvgCoords(e);
    if (activeSymbolKey) {
      const symDef = SYMBOL_DEFS.find(s => s.key === activeSymbolKey);
      const geo = pixelToGeo(x, y);
      if (activeSymbolKey === "text_label" || activeSymbolKey === "generic_point") {
        // Show label prompt — committed in handleTextSubmit
        setPendingTextPos({ x, y });
      } else if (symDef?.type === "path") {
        // Path symbols: drag to draw, committed in handleSvgMouseUp
        isPenDownRef.current = true;
        setCurrentPenPath([geo]);
      } else if (onAddAnnotation) {
        onAddAnnotation({
          id: crypto.randomUUID(),
          layer: activeLayer,
          type: "symbol",
          symbolKey: activeSymbolKey,
          coordinates: [[geo.lng, geo.lat]],
          label: symDef?.label ?? activeSymbolKey,
          color: activeColor ?? undefined,
          properties: {},
          operationalDay,
          createdAt: new Date().toISOString(),
        });
      }
    } else if (markupTool === "pen") {
      isPenDownRef.current = true;
      setCurrentPenPath([pixelToGeo(x, y)]);
    } else if (markupTool === "text") {
      setPendingTextPos({ x, y });
    }
  }, [activeSymbolKey, activeLayer, onAddAnnotation, markupTool, getSvgCoords, pixelToGeo, operationalDay, activeColor]);

  const handleSvgMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    if (!isPenDownRef.current) return;
    if (markupTool !== "pen" && !activeSymbolKey) return;
    const { x, y } = getSvgCoords(e);
    setCurrentPenPath(prev => [...prev, pixelToGeo(x, y)]);
  }, [markupTool, activeSymbolKey, getSvgCoords, pixelToGeo]);

  const handleSvgMouseUp = useCallback(() => {
    if (!isPenDownRef.current) return;
    isPenDownRef.current = false;
    setCurrentPenPath(prev => {
      if (prev.length > 0) {
        if (activeSymbolKey && onAddAnnotation) {
          // Commit path-type symbol annotation
          const symDef = SYMBOL_DEFS.find(s => s.key === activeSymbolKey);
          const annotation: IncidentAnnotation = {
            id: crypto.randomUUID(),
            layer: activeLayer,
            type: "path",
            symbolKey: activeSymbolKey,
            coordinates: prev.map(g => [g.lng, g.lat]),
            label: symDef?.label ?? activeSymbolKey,
            color: activeColor ?? undefined,
            properties: {},
            operationalDay,
            createdAt: new Date().toISOString(),
          };
          onAddAnnotation(annotation);
        } else if (markupTool === "pen") {
          setPenPaths(paths => [...paths, prev]);
        }
      }
      return [];
    });
  }, [activeSymbolKey, activeLayer, onAddAnnotation, markupTool, operationalDay, activeColor]);

  const handleTextSubmit = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && pendingTextPos) {
      const text = e.currentTarget.value.trim();
      if (text) {
        const geo = pixelToGeo(pendingTextPos.x, pendingTextPos.y);
        if (onAddAnnotation) {
          // Commit as a layer-aware IncidentAnnotation
          const symKey = (activeSymbolKey === "generic_point") ? "generic_point" : "text_label";
          onAddAnnotation({
            id: crypto.randomUUID(),
            layer: activeLayer,
            type: activeSymbolKey === "generic_point" ? "symbol" : "text",
            symbolKey: symKey,
            coordinates: [[geo.lng, geo.lat]],
            label: text,
            color: activeColor ?? undefined,
            properties: {},
            operationalDay,
            createdAt: new Date().toISOString(),
          });
        } else {
          // No incident active — fall back to session-only text marker
          setTextMarkers(prev => [...prev, { geo, text }]);
        }
      }
      setPendingTextPos(null);
    } else if (e.key === "Escape") {
      setPendingTextPos(null);
    }
  }, [pendingTextPos, pixelToGeo, onAddAnnotation, activeLayer, activeSymbolKey, operationalDay, activeColor]);

  const clearMarkup = useCallback(() => {
    setPenPaths([]);
    setTextMarkers([]);
    setCurrentPenPath([]);
  }, []);

  const handleMapRefCallback = useCallback((m: maplibregl.Map) => {
    consoleMapRef.current = m;
    // Re-render markup SVG whenever the map view changes
    const bump = () => setMapRenderKey(k => k + 1);
    m.on("move", bump);
    m.on("zoom", bump);
    m.on("rotate", bump);
  }, []);

  // ── Map snapshot capture ──────────────────────────────────────────────────
  // The console map keeps its drawing buffer (MapView `canvasContextAttributes`), so the
  // snapshot is taken at the next repaint when there is one, else from the last drawn frame.
  // A page that draws no frames (hidden tab, occluded window, locked screen) never fires the
  // map's 'render' event: waiting for it left every ICS form unopened (field test 2026-10-18,
  // Issue 1). The wait is now bounded (SNAPSHOT_WAIT_MS) and skipped for a hidden page.

  const captureMapSnapshot = useCallback((): Promise<string | undefined> => {
    const map = consoleMapRef.current;
    if (!map) return Promise.resolve(undefined);

    return new Promise((resolve) => {
      let settled = false;
      const wait: { timer?: ReturnType<typeof setTimeout> } = {};
      const grab = () => {
        if (settled) return;
        settled = true;
        clearTimeout(wait.timer);
        map.off("render", grab);
        composeMapSnapshot(map.getCanvas(), svgRef.current).then((dataUrl) => {
          if (dataUrl) setMapSnapshot(dataUrl);
          resolve(dataUrl);
        });
      };
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        grab();
        return;
      }
      map.on("render", grab);
      wait.timer = setTimeout(grab, SNAPSHOT_WAIT_MS);
      map.triggerRepaint();
    });
  }, []);

  // ── Build ICS form options ────────────────────────────────────────────────

  const buildFormOptions = useCallback((snapshot?: string) => ({
    incidentName,
    frames,
    runParams,
    ignitionPoint,
    fuelTypeLabel,
    criticalReach,
    evacZones,
    mapSnapshotDataUrl: snapshot ?? mapSnapshot,
    annotations: incidentAnnotations,
    operationalPeriod,
    runSpan: frames.length > 0 ? runSpan : null,
    runId: frames.length > 0
      ? (run209?.simulationId ?? null)
      : periodRunId ? `${periodRunId} (not loaded: use "Load run" above the console)` : null,
  }), [incidentName, frames, runParams, ignitionPoint, fuelTypeLabel, criticalReach, evacZones, mapSnapshot, incidentAnnotations, operationalPeriod, runSpan, run209, periodRunId]);

  // ── Suppression advisory for ICS-209 ─────────────────────────────────────

  const getSuppressionAdvisory = useCallback((): SuppressionAdvisory | null => {
    if (frames.length === 0) return null;
    const final = frames[frames.length - 1];
    let peakHfi = 0, maxSpotDist = 0, spotCount = 0;
    for (const f of frames) {
      if (f.max_hfi_kw_m > peakHfi) peakHfi = f.max_hfi_kw_m;
      for (const s of f.spot_fires ?? []) {
        if (s.distance_m > maxSpotDist) maxSpotDist = s.distance_m;
        spotCount++;
      }
    }
    return buildSuppressionAdvisory({
      peakHfiKwM: peakHfi,
      maxSpotDistM: maxSpotDist,
      spotCount,
      fireType: final.fire_type,
    });
  }, [frames]);

  // ── Form rendering ────────────────────────────────────────────────────────

  // A form request newer than the one being prepared wins (the 209 waits for the model version)
  const formRequestRef = useRef(0);

  const renderForm = useCallback(async (formId: ICSFormId, snapshot?: string) => {
    const request = ++formRequestRef.current;
    let html = "";
    if (formId === "ics209") {
      // Previewed in the console like the other forms. It used to open a pop-up window that
      // called window.print() on load: the print dialog blocks the app's tab (the pop-up
      // shares its event loop), which froze the tab for minutes when nobody could answer the
      // dialog (field test 2026-10-18, Issue 1). Print and New Window are on the viewer.
      const modelVersion = await modelVersionWithin(getVersion);
      if (request !== formRequestRef.current) return;
      html = buildICS209HTML({
        frames, burnProbData: burnProbabilityData, runParams, ignitionPoint, fuelTypeLabel, criticalReach, evacZones,
        suppAdvisory: getSuppressionAdvisory(), run: run209, incidentName, modelVersion,
      });
    } else {
      const opts = buildFormOptions(snapshot);
      if (formId === "ics201") html = buildICS201HTML(opts);
      else if (formId === "ics202") html = buildICS202HTML(opts);
      else if (formId === "ics203") html = buildICS203HTML(opts);
      else if (formId === "ics204") html = buildICS204HTML(opts);
      else if (formId === "ics205") html = buildICS205HTML(opts);
      else if (formId === "ics206") html = buildICS206HTML(opts);
      else if (formId === "ics214") html = buildICS214HTML(opts);
      else if (formId === "full-iap") html = buildFullIAPHTML(opts);
    }
    setRenderedForm({ html, period: operationalPeriod, frames });
    setSelectedForm(formId);
  }, [buildFormOptions, frames, burnProbabilityData, runParams, ignitionPoint, fuelTypeLabel, criticalReach, evacZones, getSuppressionAdvisory, run209, incidentName, operationalPeriod]);

  const handleFormSelect = useCallback(async (formId: ICSFormId) => {
    setConsoleTab("ics-forms");
    setSelectedForm(formId);
    // The 209-WF embeds no map: no snapshot to wait for
    const snap = formId === "ics209" ? undefined : await captureMapSnapshot();
    await renderForm(formId, snap);
  }, [captureMapSnapshot, renderForm]);

  const handlePrintForm = useCallback(() => {
    iframeRef.current?.contentWindow?.print();
  }, []);

  const handleOpenInNewWindow = useCallback(() => {
    if (!formHtml) return;
    const w = window.open("", "_blank");
    // No automatic print: a print dialog opened from script blocks the app's tab until it is
    // answered (field test 2026-10-18, Issue 1); the window has its own Print
    if (w) { w.document.write(formHtml); w.document.close(); }
  }, [formHtml]);

  // ── Render ────────────────────────────────────────────────────────────────

  const isMapFullWidth = consoleTab === "map";

  return (
    <div className="eoc-console">
      {/* ── Console header ─────────────────────────────────────────── */}
      <div className="eoc-console-header">
        <div className="eoc-header-left">
          {/* With an incident open, its name and ACTIVE status are in the period strip above */}
          {incidentNameProp !== undefined ? null : nameDraft !== null ? (
            <input
              className="eoc-incident-name-input"
              value={nameDraft}
              placeholder="Incident name"
              aria-label="Incident name"
              autoFocus
              onChange={(e) => setNameDraft(e.target.value)}
              onBlur={commitName}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitName();
                else if (e.key === "Escape") setNameDraft(null);
              }}
            />
          ) : (
            <button className="eoc-incident-name-btn" onClick={() => setNameDraft(incidentName)} title="Click to edit incident name">
              {incidentName}
              <span className="eoc-edit-icon">✎</span>
            </button>
          )}
          {incidentNameProp === undefined && frames.length > 0 && <span className="eoc-status-badge">● ACTIVE</span>}
        </div>
        <div className="eoc-header-right">
          <button className="eoc-action-btn" onClick={async () => { await captureMapSnapshot(); setConsoleTab("situation"); }} title="Print EOC Console">
            🖨 Print
          </button>
          <button className="eoc-action-btn" onClick={() => handleFormSelect("ics209")} title="Open ICS Canada 209-WF">
            ICS 209-WF
          </button>
          <button className="eoc-action-btn" onClick={() => handleFormSelect("full-iap")} title="Generate Full IAP Package">
            Full IAP
          </button>
        </div>
      </div>

      {/* ── Sub-tabs ───────────────────────────────────────────────── */}
      <div className="eoc-subtabs">
        {(["situation", "ics-forms", "map"] as ConsoleTab[]).map((tab) => (
          <button
            key={tab}
            className={`eoc-subtab${consoleTab === tab ? " active" : ""}`}
            onClick={() => setConsoleTab(tab)}
          >
            {tab === "situation" ? "Situation" : tab === "ics-forms" ? "ICS Forms" : "Map"}
          </button>
        ))}
      </div>

      {/* ── Main body ─────────────────────────────────────────────── */}
      <div className={`eoc-body${isMapFullWidth ? " eoc-body--map-full" : ""}`}>

        {/* Left: read-only map + markup overlay */}
        <div className={`eoc-map-panel${isMapFullWidth ? " eoc-map-panel--full" : ""}`}>
          <MapView
            frames={frames}
            currentFrameIndex={currentFrameIndex}
            onMapClick={() => {}}
            ignitionPoint={ignitionPoint}
            burnProbabilityData={burnProbabilityData}
            showBurnProbView={showBurnProbView}
            overlayCommunities={overlayCommunities}
            overlayCommunitiesVisible={overlayCommunitiesVisible}
            assetPoints={assetPoints}
            assetsVisible={assetsVisible}
            roadsReached={roadsReached}
            roadsReachedVisible={roadsReachedVisible}
            assetAttribution={assetAttribution}
            evacZones={evacZones}
            evacZonesVisible={evacZonesVisible}
            isochrones={isochrones}
            isochronesVisible={isochronesVisible}
            fuelGridImage={fuelGridImage}
            fuelGridVisible={fuelGridVisible}
            readOnly
            mapRefCallback={handleMapRefCallback}
            spotFiresVisible={spotFiresVisible}
          />

          {/* SVG markup overlay */}
          <svg
            ref={svgRef}
            className={`eoc-markup-svg${activeSymbolKey ? ` active${activeSymbolKey === "text_label" || activeSymbolKey === "generic_point" ? " text-mode" : ""}` : ""}`}
            onMouseDown={handleSvgMouseDown}
            onMouseMove={handleSvgMouseMove}
            onMouseUp={handleSvgMouseUp}
            onMouseLeave={handleSvgMouseUp}
          >
            {/* Ghost perimeter from previous operational period */}
            {ghostPerimeter && ghostPerimeter.length > 0 && (() => {
              // Stored as [lat, lng] (the API frame order); see utils/ghostPerimeter.ts
              const d = ghostPerimeterPath(ghostPerimeter, ([lng, lat]) => geoToPixel({ lng, lat }));
              return d ? <path d={d} className="eoc-ghost-perimeter" /> : null;
            })()}

            {/* Incident annotations — dimmed if not on active layer */}
            {incidentAnnotations.map((ann) => {
              const isActive = ann.layer === activeLayer;
              const opacity = isActive ? 1 : 0.3;
              const symDef = SYMBOL_DEFS.find(s => s.key === ann.symbolKey);
              const color = ann.color ?? symDef?.color ?? "#ffffff";
              // OSM-fetched annotations render as small reference dots — no label clutter.
              // Their data appears in ICS form tables; the map stays tactically readable.
              const isOsm = ann.properties.source === "osm";

              if (ann.type === "path" && ann.coordinates.length > 1) {
                const d = ann.coordinates.map(([lng, lat], i) => {
                  const { x, y } = geoToPixel({ lng, lat });
                  return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
                }).join(" ");
                return (
                  <g key={ann.id} opacity={opacity}>
                    <path d={d} fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
                  </g>
                );
              }

              if (ann.type === "text" && ann.coordinates.length > 0) {
                const [lng, lat] = ann.coordinates[0];
                const { x, y } = geoToPixel({ lng, lat });
                return (
                  <g key={ann.id} opacity={opacity}
                    style={{ cursor: isActive ? "pointer" : "default" }}
                    onClick={() => isActive && onRemoveAnnotation?.(ann.id)}
                  >
                    {/* Outline stroke for legibility */}
                    <text x={x} y={y} fill="none" stroke="#000" strokeWidth={3}
                      strokeLinejoin="round" fontSize={13} fontWeight="600"
                      style={{ userSelect: "none", pointerEvents: "none" }}
                    >{ann.label}</text>
                    <text x={x} y={y} fill={color} fontSize={13} fontWeight="600"
                      style={{ userSelect: "none" }}
                    >{ann.label}</text>
                  </g>
                );
              }

              if (ann.type === "symbol" && ann.coordinates.length > 0) {
                const [lng, lat] = ann.coordinates[0];
                const { x, y } = geoToPixel({ lng, lat });

                // OSM reference dot — small, no label, shows category color
                if (isOsm) {
                  return (
                    <circle key={ann.id} cx={x} cy={y} r={3} fill={color}
                      opacity={isActive ? 0.6 : 0.2}
                      style={{ cursor: isActive ? "pointer" : "default" }}
                      onClick={() => isActive && onRemoveAnnotation?.(ann.id)}
                    />
                  );
                }

                return (
                  <g key={ann.id} opacity={opacity}
                    transform={`translate(${x - 10},${y - 10})`}
                    style={{ cursor: isActive ? "pointer" : "default" }}
                    onClick={() => isActive && onRemoveAnnotation?.(ann.id)}
                  >
                    <SymbolIcon symbolKey={ann.symbolKey} color={color} size={20} />
                    {ann.label && ann.symbolKey !== "text_label" && (
                      <g style={{ pointerEvents: "none" }}>
                        <text x={10} y={26} textAnchor="middle" fontSize={9}
                          fill="none" stroke="#000" strokeWidth={2.5} strokeLinejoin="round"
                        >{ann.label}</text>
                        <text x={10} y={26} textAnchor="middle" fontSize={9} fill={color}
                        >{ann.label}</text>
                      </g>
                    )}
                  </g>
                );
              }
              return null;
            })}

            {/* Legacy freehand pen paths */}
            {penPaths.map((points, i) => {
              const d = geoPathToSvgD(points);
              return d ? <path key={i} d={d} className="eoc-markup-path" /> : null;
            })}
            {currentPenPath.length > 0 && (() => {
              const symDef = activeSymbolKey ? SYMBOL_DEFS.find(s => s.key === activeSymbolKey) : null;
              return symDef
                ? <path d={geoPathToSvgD(currentPenPath)} fill="none" stroke={activeColor ?? symDef.color} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" strokeDasharray="6 3" opacity={0.7} />
                : <path d={geoPathToSvgD(currentPenPath)} className="eoc-markup-path eoc-markup-path--live" />;
            })()}
            {textMarkers.map((m, i) => {
              const { x, y } = geoToPixel(m.geo);
              return <text key={i} x={x} y={y} className="eoc-markup-text">{m.text}</text>;
            })}
          </svg>

          {/* Floating text input when placing a label */}
          {pendingTextPos && (
            <input
              ref={textInputRef}
              className="eoc-markup-text-input"
              style={{ left: pendingTextPos.x, top: pendingTextPos.y }}
              placeholder="Label…"
              onKeyDown={handleTextSubmit}
              onBlur={() => setPendingTextPos(null)}
            />
          )}

          {/* Symbol picker flyout */}
          {showSymbolPicker && (
            <div className="eoc-symbol-picker-flyout">
              <AnnotationSymbolPicker
                activeLayer={activeLayer}
                activeSymbol={activeSymbolKey}
                onLayerChange={(layer) => { setActiveLayer(layer); setActiveSymbolKey(null); setMarkupTool(null); }}
                onSymbolSelect={(key) => { setActiveSymbolKey(prev => prev === key ? null : key); setMarkupTool(null); }}
                activeColor={activeColor}
                onColorChange={setActiveColor}
              />
              {incidentAnnotations.filter(a => a.layer === activeLayer).length > 0 && (
                <button
                  className="eoc-clear-layer-btn"
                  onClick={() => onClearLayer?.(activeLayer)}
                  title={`Clear all ${activeLayer} layer annotations`}
                >
                  Clear layer
                </button>
              )}
            </div>
          )}

          {/* Markup toolbar */}
          <div className="eoc-markup-toolbar">
            <button
              className="eoc-markup-tool"
              onClick={() => consoleMapRef.current?.zoomIn()}
              title="Zoom in"
              aria-label="Zoom in"
            >+</button>
            <button
              className="eoc-markup-tool"
              onClick={() => consoleMapRef.current?.zoomOut()}
              title="Zoom out"
              aria-label="Zoom out"
            >−</button>
            <div className="eoc-markup-divider" />
            <button
              className={`eoc-markup-tool${spotFiresVisible ? " active" : ""}`}
              onClick={() => setSpotFiresVisible(v => !v)}
              title={spotFiresVisible ? "Spot fires ON — click to hide" : "Spot fires OFF — click to show"}
              aria-label="Spot fires"
              aria-pressed={spotFiresVisible}
            >✦</button>
            <div className="eoc-markup-divider" />
            <span className="eoc-markup-label">MARK</span>
            <button
              className={`eoc-markup-tool${showSymbolPicker ? " active" : ""}`}
              onClick={() => setShowSymbolPicker(v => !v)}
              title="ICS symbol palette"
              aria-label="ICS symbol palette"
              aria-pressed={showSymbolPicker}
            >⊕</button>
            <button
              className={`eoc-markup-tool${isFetchingFacilities ? " active" : ""}`}
              title={!ignitionPoint ? "Set an ignition point first to fetch nearby resources" : "Fetch nearby emergency facilities from OpenStreetMap"}
              aria-label="Fetch nearby emergency facilities from OpenStreetMap"
              disabled={isFetchingFacilities || !ignitionPoint || !onFetchFacilities}
              onClick={async () => {
                if (!onFetchFacilities) return;
                setIsFetchingFacilities(true);
                setFetchFacilitiesMsg("Fetching OSM resources…");
                try {
                  const count = await onFetchFacilities();
                  setFetchFacilitiesMsg(count > 0 ? `+${count} facilities added` : "No new facilities found");
                } catch {
                  setFetchFacilitiesMsg("Fetch failed — check connection");
                } finally {
                  setIsFetchingFacilities(false);
                  setTimeout(() => setFetchFacilitiesMsg(null), 4000);
                }
              }}
            >{isFetchingFacilities ? "…" : "📡"}</button>
            <button
              className={`eoc-markup-tool${activeSymbolKey === "freehand_path" ? " active" : ""}`}
              onClick={() => setActiveSymbolKey(k => k === "freehand_path" ? null : "freehand_path")}
              title="Freehand draw on active layer (click active to pan)"
              aria-label="Freehand draw"
              aria-pressed={activeSymbolKey === "freehand_path"}
            >✏</button>
            <button
              className={`eoc-markup-tool${activeSymbolKey === "text_label" ? " active" : ""}`}
              onClick={() => setActiveSymbolKey(k => k === "text_label" ? null : "text_label")}
              title="Place text label on active layer (click active to pan)"
              aria-label="Text label"
              aria-pressed={activeSymbolKey === "text_label"}
            >T</button>
            <button
              className="eoc-markup-tool"
              onClick={clearMarkup}
              title="Clear freehand markup"
              aria-label="Clear freehand markup"
              disabled={penPaths.length === 0 && textMarkers.length === 0 && currentPenPath.length === 0}
            >⌫</button>
          </div>

          {/* OSM fetch status chip */}
          {fetchFacilitiesMsg && (
            <div className="eoc-fetch-msg">{fetchFacilitiesMsg}</div>
          )}

          {/* Print-only map snapshot */}
          {mapSnapshot && (
            <img className="eoc-print-map" src={mapSnapshot} alt="Map snapshot" />
          )}
        </div>

        {/* Right: content panel (hidden in full-map mode) */}
        {!isMapFullWidth && (
          <div className="eoc-data-panels">

            {/* ── Situation tab ───────────────────────────── */}
            {consoleTab === "situation" && (
              <EOCSummary
                frames={frames}
                burnProbData={burnProbabilityData}
                runParams={runParams}
                ignitionPoint={ignitionPoint}
                fuelTypeLabel={fuelTypeLabel}
                criticalReach={criticalReach}
                evacZones={evacZones}
                run209={run209}
                incidentName={incidentName}
              />
            )}

            {/* ── ICS Forms tab ───────────────────────────── */}
            {consoleTab === "ics-forms" && (
              <div className="eoc-forms-panel">
                <div className="eoc-forms-header">
                  <span className="eoc-forms-title">ICS FORMS</span>
                </div>

                {/* Initial forms */}
                <div className="eoc-form-group">
                  <span className="eoc-form-group-label">Initial Briefing</span>
                  <div className="eoc-form-btns">
                    {(["ics201"] as ICSFormId[]).map((id) => (
                      <button
                        key={id}
                        className={`eoc-form-btn${selectedForm === id ? " active" : ""}`}
                        onClick={() => handleFormSelect(id)}
                      >
                        {ICS_FORM_LABELS[id]}
                      </button>
                    ))}
                  </div>
                </div>

                {/* IAP package */}
                <div className="eoc-form-group">
                  <span className="eoc-form-group-label">IAP Package</span>
                  <div className="eoc-form-btns">
                    {(["ics202", "ics203", "ics204", "ics205", "ics206"] as ICSFormId[]).map((id) => (
                      <button
                        key={id}
                        className={`eoc-form-btn${selectedForm === id ? " active" : ""}`}
                        onClick={() => handleFormSelect(id)}
                      >
                        {ICS_FORM_LABELS[id]}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Status & logs */}
                <div className="eoc-form-group">
                  <span className="eoc-form-group-label">Status & Logs</span>
                  <div className="eoc-form-btns">
                    {(["ics209", "ics214"] as ICSFormId[]).map((id) => (
                      <button
                        key={id}
                        className={`eoc-form-btn${selectedForm === id ? " active" : ""}`}
                        onClick={() => handleFormSelect(id)}
                      >
                        {ICS_FORM_LABELS[id]}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Full IAP */}
                <div className="eoc-form-group">
                  <div className="eoc-form-btns">
                    <button
                      className={`eoc-form-btn eoc-form-btn--primary${selectedForm === "full-iap" ? " active" : ""}`}
                      onClick={() => handleFormSelect("full-iap")}
                    >
                      ⬇ Generate Full IAP (201–206)
                    </button>
                  </div>
                </div>

                {/* Form viewer */}
                {formHtml && (
                  <div className="eoc-form-viewer">
                    <div className="eoc-form-viewer-toolbar">
                      <span className="eoc-form-viewer-name">{ICS_FORM_LABELS[selectedForm]}</span>
                      <div className="eoc-form-viewer-actions">
                        <button className="eoc-action-btn" onClick={handlePrintForm}>🖨 Print</button>
                        <button className="eoc-action-btn" onClick={handleOpenInNewWindow}>↗ New Window</button>
                      </div>
                    </div>
                    <iframe
                      ref={iframeRef}
                      srcDoc={formHtml}
                      className="eoc-form-iframe"
                      title={ICS_FORM_LABELS[selectedForm]}
                      sandbox="allow-same-origin allow-scripts allow-modals"
                    />
                  </div>
                )}

                {!formHtml && (
                  <div className="eoc-forms-empty">
                    Select a form above to generate and preview it. <InfoTip label="About the form maps" text={TIPS.eocSnapshot} />
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
