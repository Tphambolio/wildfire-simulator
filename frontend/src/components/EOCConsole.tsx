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
 * any print or ICS form render — embedded as base64 PNG in form HTML.
 *
 * Design: Stitch EOC Tactical Dark system — indigo-ember palette, no-line rule,
 * tonal layering. Reference: /tmp/stitch_export/stitch/stitch/eoc_tactical_dark/DESIGN.md
 */

import { useState, useRef, useCallback, useEffect } from "react";
import maplibregl from "maplibre-gl";
import MapView from "./MapView";
import EOCSummary from "./EOCSummary";
import AnnotationSymbolPicker, { SymbolIcon } from "./AnnotationSymbolPicker";
import type { SimulationFrame, BurnProbabilityResponse } from "../types/simulation";
import type { RunParams } from "./WeatherPanel";
import type { EvacZone } from "../utils/evacZones";
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
import { openICS209Report } from "../utils/ics209";
import type { SuppressionAdvisory } from "./EOCSummary";
import { buildSuppressionAdvisory } from "./EOCSummary";
import type { AnnotationLayer, ICSSymbolKey, IncidentAnnotation } from "../types/incident";
import { SYMBOL_DEFS } from "../types/incident";

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
  overlayRoads?: GeoJSON.FeatureCollection | null;
  overlayRoadsVisible?: boolean;
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayCommunitiesVisible?: boolean;
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
  overlayInfrastructureVisible?: boolean;
  atRiskCounts?: { roads: number; communities: number; infrastructure: number };
  // Evac zones
  evacZones?: EvacZone[];
  evacZonesVisible?: boolean;
  // Isochrones
  isochrones?: Isochrone[];
  isochronesVisible?: boolean;
  // Fuel grid
  fuelGridImage?: { image: string; bounds: [number, number, number, number] } | null;
  fuelGridVisible?: boolean;
  // Incident annotation store (from useIncident)
  incidentAnnotations?: IncidentAnnotation[];
  onAddAnnotation?: (a: IncidentAnnotation) => void;
  onRemoveAnnotation?: (id: string) => void;
  onClearLayer?: (layer: AnnotationLayer) => void;
  /** Previous operational period's final perimeter — shown as ghost on Day 2+ */
  ghostPerimeter?: [number, number][] | null;
  incidentName?: string;
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
  ics209: "ICS-209 Status Summary",
  ics214: "ICS-214 Activity Log",
  "full-iap": "Full IAP Package",
};

export default function EOCConsole({
  frames,
  currentFrameIndex,
  burnProbabilityData = null,
  showBurnProbView = false,
  runParams,
  ignitionPoint,
  fuelTypeLabel,
  overlayRoads = null,
  overlayRoadsVisible = true,
  overlayCommunities = null,
  overlayCommunitiesVisible = true,
  overlayInfrastructure = null,
  overlayInfrastructureVisible = true,
  atRiskCounts,
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
  ghostPerimeter = null,
  incidentName: incidentNameProp,
  onIncidentNameChange,
}: EOCConsoleProps) {
  const [consoleTab, setConsoleTab] = useState<ConsoleTab>("situation");
  // Use incident name from prop if provided (incident store), else local state
  const [localIncidentName, setLocalIncidentName] = useState("Untitled Incident");
  const incidentName = incidentNameProp ?? localIncidentName;
  const setIncidentName = (name: string) => {
    setLocalIncidentName(name);
    onIncidentNameChange?.(name);
  };
  const [editingName, setEditingName] = useState(false);
  const [activeLayer, setActiveLayer] = useState<AnnotationLayer>("situation");
  const [activeSymbolKey, setActiveSymbolKey] = useState<ICSSymbolKey | null>(null);
  const [showSymbolPicker, setShowSymbolPicker] = useState(false);
  const [selectedForm, setSelectedForm] = useState<ICSFormId>("ics201");
  const [formHtml, setFormHtml] = useState<string>("");
  const [mapSnapshot, setMapSnapshot] = useState<string | undefined>(undefined);
  const consoleMapRef = useRef<maplibregl.Map | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // ── Layer visibility ─────────────────────────────────────────────────────
  const [spotFiresVisible, setSpotFiresVisible] = useState(true);

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
    if (activeSymbolKey && onAddAnnotation) {
      // Place a symbol annotation at click point
      const geo = pixelToGeo(x, y);
      const symDef = SYMBOL_DEFS.find(s => s.key === activeSymbolKey);
      if (symDef?.type === "path") {
        // Path symbols start a pen-style drag — handled in mouse move/up
        isPenDownRef.current = true;
        setCurrentPenPath([geo]);
      } else {
        const annotation: IncidentAnnotation = {
          id: crypto.randomUUID(),
          layer: activeLayer,
          type: "symbol",
          symbolKey: activeSymbolKey,
          coordinates: [[geo.lng, geo.lat]],
          label: symDef?.label ?? activeSymbolKey,
          properties: {},
          operationalDay: 1,
          createdAt: new Date().toISOString(),
        };
        onAddAnnotation(annotation);
      }
    } else if (markupTool === "pen") {
      isPenDownRef.current = true;
      setCurrentPenPath([pixelToGeo(x, y)]);
    } else if (markupTool === "text") {
      setPendingTextPos({ x, y });
    }
  }, [activeSymbolKey, activeLayer, onAddAnnotation, markupTool, getSvgCoords, pixelToGeo]);

  const handleSvgMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    if (markupTool !== "pen" || !isPenDownRef.current) return;
    const { x, y } = getSvgCoords(e);
    setCurrentPenPath(prev => [...prev, pixelToGeo(x, y)]);
  }, [markupTool, getSvgCoords, pixelToGeo]);

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
            properties: {},
            operationalDay: 1,
            createdAt: new Date().toISOString(),
          };
          onAddAnnotation(annotation);
        } else if (markupTool === "pen") {
          setPenPaths(paths => [...paths, prev]);
        }
      }
      return [];
    });
  }, [activeSymbolKey, activeLayer, onAddAnnotation, markupTool]);

  const handleTextSubmit = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && pendingTextPos) {
      const text = e.currentTarget.value.trim();
      if (text) {
        const geo = pixelToGeo(pendingTextPos.x, pendingTextPos.y);
        setTextMarkers(prev => [...prev, { geo, text }]);
      }
      setPendingTextPos(null);
    } else if (e.key === "Escape") {
      setPendingTextPos(null);
    }
  }, [pendingTextPos, pixelToGeo]);

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
  // WebGL doesn't preserve the drawing buffer between frames, so we must
  // trigger a repaint and capture inside the 'render' event callback.

  const captureMapSnapshot = useCallback((): Promise<string | undefined> => {
    const map = consoleMapRef.current;
    if (!map) return Promise.resolve(undefined);
    return new Promise((resolve) => {
      map.once("render", () => {
        try {
          const dataUrl = map.getCanvas().toDataURL("image/png");
          setMapSnapshot(dataUrl);
          resolve(dataUrl);
        } catch {
          resolve(undefined);
        }
      });
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
    atRiskCounts,
    evacZones,
    mapSnapshotDataUrl: snapshot ?? mapSnapshot,
    annotations: incidentAnnotations,
  }), [incidentName, frames, runParams, ignitionPoint, fuelTypeLabel, atRiskCounts, evacZones, mapSnapshot, incidentAnnotations]);

  // ── Suppression advisory for ICS-209 ─────────────────────────────────────

  const getSuppressionAdvisory = useCallback((): SuppressionAdvisory | null => {
    if (frames.length === 0) return null;
    const final = frames[frames.length - 1];
    const perimeterLengthKm = (perim: number[][]): number => {
      let t = 0;
      for (let i = 0; i < perim.length; i++) {
        const a = perim[i], b = perim[(i + 1) % perim.length];
        const dLat = ((b[0] - a[0]) * Math.PI) / 180;
        const dLng = ((b[1] - a[1]) * Math.PI) / 180;
        const x = Math.sin(dLat / 2) ** 2 +
          Math.cos((a[0] * Math.PI) / 180) * Math.cos((b[0] * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
        t += 6371 * 2 * Math.asin(Math.sqrt(x));
      }
      return t;
    };
    let peakRos = 0, peakHfi = 0, maxSpotDist = 0, spotCount = 0;
    for (const f of frames) {
      if (f.head_ros_m_min > peakRos) peakRos = f.head_ros_m_min;
      if (f.max_hfi_kw_m > peakHfi) peakHfi = f.max_hfi_kw_m;
      for (const s of f.spot_fires ?? []) {
        if (s.distance_m > maxSpotDist) maxSpotDist = s.distance_m;
        spotCount++;
      }
    }
    return buildSuppressionAdvisory({
      peakRosMMmin: peakRos,
      peakHfiKwM: peakHfi,
      finalAreaHa: final.area_ha,
      perimeterKm: perimeterLengthKm(final.perimeter ?? []),
      maxSpotDistM: maxSpotDist,
      spotCount,
      fireType: final.fire_type,
      flameLengthM: final.flame_length_m,
    });
  }, [frames]);

  // ── Form rendering ────────────────────────────────────────────────────────

  const renderForm = useCallback((formId: ICSFormId, snapshot?: string) => {
    const opts = buildFormOptions(snapshot);
    if (formId === "ics209") {
      openICS209Report({ frames, burnProbData: burnProbabilityData, runParams, ignitionPoint, fuelTypeLabel, atRiskCounts, evacZones, suppAdvisory: getSuppressionAdvisory() });
      return; // ICS-209 opens in new window, not iframe
    }
    let html = "";
    if (formId === "ics201") html = buildICS201HTML(opts);
    else if (formId === "ics202") html = buildICS202HTML(opts);
    else if (formId === "ics203") html = buildICS203HTML(opts);
    else if (formId === "ics204") html = buildICS204HTML(opts);
    else if (formId === "ics205") html = buildICS205HTML(opts);
    else if (formId === "ics206") html = buildICS206HTML(opts);
    else if (formId === "ics214") html = buildICS214HTML(opts);
    else if (formId === "full-iap") html = buildFullIAPHTML(opts);
    setFormHtml(html);
    setSelectedForm(formId);
  }, [buildFormOptions, frames, burnProbabilityData, runParams, ignitionPoint, fuelTypeLabel, atRiskCounts, overlayRoads, overlayCommunities, overlayInfrastructure, evacZones, getSuppressionAdvisory]);

  const handleFormSelect = useCallback(async (formId: ICSFormId) => {
    const snap = await captureMapSnapshot();
    if (formId === "ics209") {
      renderForm(formId, snap);
      return;
    }
    setConsoleTab("ics-forms");
    renderForm(formId, snap);
  }, [captureMapSnapshot, renderForm]);

  const handlePrintForm = useCallback(() => {
    iframeRef.current?.contentWindow?.print();
  }, []);

  const handleOpenInNewWindow = useCallback(() => {
    if (!formHtml) return;
    const w = window.open("", "_blank");
    if (w) { w.document.write(formHtml); w.document.close(); w.print(); }
  }, [formHtml]);

  // ── Render ────────────────────────────────────────────────────────────────

  const isMapFullWidth = consoleTab === "map";

  return (
    <div className="eoc-console">
      {/* ── Console header ─────────────────────────────────────────── */}
      <div className="eoc-console-header">
        <div className="eoc-header-left">
          {editingName ? (
            <input
              className="eoc-incident-name-input"
              value={incidentName}
              autoFocus
              onChange={(e) => setIncidentName(e.target.value)}
              onBlur={() => setEditingName(false)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === "Escape") setEditingName(false); }}
            />
          ) : (
            <button className="eoc-incident-name-btn" onClick={() => setEditingName(true)} title="Click to edit incident name">
              {incidentName}
              <span className="eoc-edit-icon">✎</span>
            </button>
          )}
          {frames.length > 0 && <span className="eoc-status-badge">● ACTIVE</span>}
        </div>
        <div className="eoc-header-right">
          <button className="eoc-action-btn" onClick={async () => { await captureMapSnapshot(); setConsoleTab("situation"); }} title="Print EOC Console">
            🖨 Print
          </button>
          <button className="eoc-action-btn" onClick={() => handleFormSelect("ics209")} title="Open ICS-209">
            ICS-209
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
            overlayRoads={overlayRoads}
            overlayRoadsVisible={overlayRoadsVisible}
            overlayCommunities={overlayCommunities}
            overlayCommunitiesVisible={overlayCommunitiesVisible}
            overlayInfrastructure={overlayInfrastructure}
            overlayInfrastructureVisible={overlayInfrastructureVisible}
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
            className={`eoc-markup-svg${(markupTool || activeSymbolKey) ? ` active${markupTool === "text" ? " text-mode" : ""}` : ""}`}
            onMouseDown={handleSvgMouseDown}
            onMouseMove={handleSvgMouseMove}
            onMouseUp={handleSvgMouseUp}
            onMouseLeave={handleSvgMouseUp}
          >
            {/* Ghost perimeter from previous operational period */}
            {ghostPerimeter && ghostPerimeter.length > 0 && (() => {
              const d = ghostPerimeter.map(([lng, lat], i) => {
                const { x, y } = geoToPixel({ lng, lat });
                return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
              }).join(" ") + " Z";
              return <path d={d} className="eoc-ghost-perimeter" />;
            })()}

            {/* Incident annotations — dimmed if not on active layer */}
            {incidentAnnotations.map((ann) => {
              const isActive = ann.layer === activeLayer;
              const opacity = isActive ? 1 : 0.3;
              const symDef = SYMBOL_DEFS.find(s => s.key === ann.symbolKey);
              const color = symDef?.color ?? "#ffffff";

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

              if ((ann.type === "symbol" || ann.type === "text") && ann.coordinates.length > 0) {
                const [lng, lat] = ann.coordinates[0];
                const { x, y } = geoToPixel({ lng, lat });
                return (
                  <g key={ann.id} opacity={opacity}
                    transform={`translate(${x - 10},${y - 10})`}
                    style={{ cursor: isActive ? "pointer" : "default" }}
                    onClick={() => isActive && onRemoveAnnotation?.(ann.id)}
                  >
                    <SymbolIcon symbolKey={ann.symbolKey} color={color} size={20} />
                    {ann.label && (
                      <text x={10} y={26} textAnchor="middle" fontSize={9} fill={color}
                        style={{ pointerEvents: "none", textShadow: "0 0 3px #000" }}
                      >
                        {ann.label}
                      </text>
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
            {currentPenPath.length > 0 && (
              <path d={geoPathToSvgD(currentPenPath)} className="eoc-markup-path eoc-markup-path--live" />
            )}
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
            >+</button>
            <button
              className="eoc-markup-tool"
              onClick={() => consoleMapRef.current?.zoomOut()}
              title="Zoom out"
            >−</button>
            <div className="eoc-markup-divider" />
            <button
              className={`eoc-markup-tool${spotFiresVisible ? " active" : ""}`}
              onClick={() => setSpotFiresVisible(v => !v)}
              title={spotFiresVisible ? "Spot fires ON — click to hide" : "Spot fires OFF — click to show"}
            >✦</button>
            <div className="eoc-markup-divider" />
            <span className="eoc-markup-label">MARK</span>
            <button
              className={`eoc-markup-tool${showSymbolPicker ? " active" : ""}`}
              onClick={() => setShowSymbolPicker(v => !v)}
              title="ICS symbol palette"
            >⊕</button>
            <button
              className={`eoc-markup-tool${markupTool === "pen" && !activeSymbolKey ? " active" : ""}`}
              onClick={() => { setMarkupTool(t => t === "pen" ? null : "pen"); setActiveSymbolKey(null); }}
              title="Freehand draw (click active to pan)"
            >✏</button>
            <button
              className={`eoc-markup-tool${markupTool === "text" ? " active" : ""}`}
              onClick={() => { setMarkupTool(t => t === "text" ? null : "text"); setActiveSymbolKey(null); }}
              title="Place text label (click active to pan)"
            >T</button>
            <button
              className="eoc-markup-tool"
              onClick={clearMarkup}
              title="Clear freehand markup"
              disabled={penPaths.length === 0 && textMarkers.length === 0 && currentPenPath.length === 0}
            >⌫</button>
          </div>

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
                atRiskCounts={atRiskCounts}
                overlayRoads={overlayRoads}
                overlayCommunities={overlayCommunities}
                overlayInfrastructure={overlayInfrastructure}
                evacZones={evacZones}
              />
            )}

            {/* ── ICS Forms tab ───────────────────────────── */}
            {consoleTab === "ics-forms" && (
              <div className="eoc-forms-panel">
                <div className="eoc-forms-header">
                  <span className="eoc-forms-title">ICS FORMS</span>
                  <span className="eoc-forms-subtitle">NIMS Incident Action Plan</span>
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
                    Select a form above to generate and preview it.<br />
                    The current map state will be captured as a snapshot for embedded maps.
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
