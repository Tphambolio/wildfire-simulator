/**
 * EOC Command Console — full-viewport tabbed incident management page.
 *
 * Layout:
 *   Left 45%  : read-only MapLibre map with annotation overlay
 *   Right 55% : sub-tabbed content — Situation / ICS Forms / Map (full-width)
 */

import { useState, useRef, useCallback, useEffect } from "react";
import maplibregl from "maplibre-gl";
import MapView from "./MapView";
import AnnotationSymbolPicker, { SymbolIcon } from "./AnnotationSymbolPicker";
import SectionWorkspace from "./SectionWorkspace";
import {
  buildICS201HTML,
  buildICS202HTML,
  buildICS203HTML,
  buildICS204HTML,
  buildICS205HTML,
  buildICS206HTML,
  buildICS207HTML,
  buildICS208HTML,
  buildICS213HTML,
  buildICS214HTML,
  buildICS215HTML,
  buildICS215aHTML,
  buildFullIAPHTML,
} from "../utils/icsForms";
import type { AnnotationLayer, ICSSymbolKey, IncidentAnnotation, HazardType, HazardZone, IncidentResource, IncidentAgency, OperationalPeriod } from "../types/incident";
import { SYMBOL_DEFS } from "../types/incident";

// ── Props ─────────────────────────────────────────────────────────────────────

interface EOCConsoleProps {
  incidentLocation?: { lat: number; lng: number } | null;
  overlayRoads?: GeoJSON.FeatureCollection | null;
  overlayRoadsVisible?: boolean;
  overlayCommunities?: GeoJSON.FeatureCollection | null;
  overlayCommunitiesVisible?: boolean;
  overlayInfrastructure?: GeoJSON.FeatureCollection | null;
  overlayInfrastructureVisible?: boolean;
  incidentAnnotations?: IncidentAnnotation[];
  onAddAnnotation?: (a: IncidentAnnotation) => void;
  onRemoveAnnotation?: (id: string) => void;
  onClearLayer?: (layer: AnnotationLayer) => void;
  onFetchFacilities?: () => Promise<number>;
  incidentName?: string;
  onIncidentNameChange?: (name: string) => void;
  hazardType?: HazardType;
  incidentComplexity?: 1 | 2 | 3 | 4 | 5;
  hazardZones?: HazardZone[];
  resources?: IncidentResource[];
  agencies?: IncidentAgency[];
  activePeriod?: OperationalPeriod;
  onResourcesChange?: (r: IncidentResource[]) => void;
  onAgenciesChange?: (a: IncidentAgency[]) => void;
  initialConsoleTab?: ConsoleTab;
  onConsoleTabChange?: (tab: ConsoleTab) => void;
}

export type ConsoleTab = "situation" | "command" | "operations" | "planning" | "logistics" | "finance" | "iap" | "map";

export type ICSFormId =
  | "ics201" | "ics202" | "ics203" | "ics204" | "ics205" | "ics206"
  | "ics207" | "ics208" | "ics213" | "ics214" | "ics215" | "ics215a"
  | "full-iap";

const ICS_FORM_LABELS: Record<ICSFormId, string> = {
  ics201: "ICS-201 Briefing",
  ics202: "ICS-202 Objectives",
  ics203: "ICS-203 Organization",
  ics204: "ICS-204 Assignments",
  ics205: "ICS-205 Comms Plan",
  ics206: "ICS-206 Medical Plan",
  ics207: "ICS-207 Org Chart",
  ics208: "ICS-208 Safety Plan",
  ics213: "ICS-213 General Message",
  ics214: "ICS-214 Activity Log",
  ics215: "ICS-215 Resource Needs",
  ics215a: "ICS-215A Safety Analysis",
  "full-iap": "Full IAP Package",
};

export default function EOCConsole({
  incidentLocation = null,
  overlayRoads = null,
  overlayRoadsVisible = true,
  overlayCommunities = null,
  overlayCommunitiesVisible = true,
  overlayInfrastructure = null,
  overlayInfrastructureVisible = true,
  incidentAnnotations = [],
  onAddAnnotation,
  onRemoveAnnotation,
  onClearLayer,
  onFetchFacilities,
  incidentName: incidentNameProp,
  onIncidentNameChange,
  hazardType,
  incidentComplexity,
  hazardZones,
  resources,
  agencies,
  activePeriod,
  onResourcesChange,
  onAgenciesChange,
  initialConsoleTab,
  onConsoleTabChange,
}: EOCConsoleProps) {
  const [consoleTab, setConsoleTabState] = useState<ConsoleTab>(initialConsoleTab ?? "situation");
  const setConsoleTab = useCallback((tab: ConsoleTab) => {
    setConsoleTabState(tab);
    onConsoleTabChange?.(tab);
  }, [onConsoleTabChange]);
  const [localIncidentName, setLocalIncidentName] = useState("Untitled Incident");
  const incidentName = incidentNameProp ?? localIncidentName;
  const setIncidentName = (name: string) => {
    setLocalIncidentName(name);
    onIncidentNameChange?.(name);
  };
  const [editingName, setEditingName] = useState(false);
  const [activeLayer, setActiveLayer] = useState<AnnotationLayer>("situation");
  const [activeSymbolKey, setActiveSymbolKey] = useState<ICSSymbolKey | null>(null);
  const [activeColor, setActiveColor] = useState<string | null>(null);
  const [showSymbolPicker, setShowSymbolPicker] = useState(false);
  const [selectedForm, setSelectedForm] = useState<ICSFormId>("ics201");
  const [formHtml, setFormHtml] = useState<string>("");
  const [mapSnapshot, setMapSnapshot] = useState<string | undefined>(undefined);
  const consoleMapRef = useRef<maplibregl.Map | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const [isFetchingFacilities, setIsFetchingFacilities] = useState(false);
  const [fetchFacilitiesMsg, setFetchFacilitiesMsg] = useState<string | null>(null);

  // ── Map markup state ─────────────────────────────────────────────────────
  type MarkupTool = "pen" | "text" | null;
  type GeoPoint = { lng: number; lat: number };

  const [markupTool, setMarkupTool] = useState<MarkupTool>(null);
  const [penPaths, setPenPaths] = useState<GeoPoint[][]>([]);
  const [currentPenPath, setCurrentPenPath] = useState<GeoPoint[]>([]);
  const [textMarkers, setTextMarkers] = useState<Array<{ geo: GeoPoint; text: string }>>([]);
  const [pendingTextPos, setPendingTextPos] = useState<{ x: number; y: number } | null>(null);
  const [mapRenderKey, setMapRenderKey] = useState(0);
  const isPenDownRef = useRef(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const textInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (pendingTextPos) textInputRef.current?.focus(); }, [pendingTextPos]);

  const getSvgCoords = useCallback((e: React.MouseEvent): { x: number; y: number } => {
    const rect = svgRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }, []);

  const pixelToGeo = useCallback((x: number, y: number): GeoPoint => {
    const map = consoleMapRef.current;
    if (!map) return { lng: 0, lat: 0 };
    const ll = map.unproject([x, y]);
    return { lng: ll.lng, lat: ll.lat };
  }, []);

  const geoToPixel = useCallback((geo: GeoPoint): { x: number; y: number } => {
    const map = consoleMapRef.current;
    if (!map) return { x: 0, y: 0 };
    const p = map.project([geo.lng, geo.lat]);
    return { x: p.x, y: p.y };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapRenderKey]);

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
        setPendingTextPos({ x, y });
      } else if (symDef?.type === "path") {
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
          operationalDay: 1,
          createdAt: new Date().toISOString(),
        });
      }
    } else if (markupTool === "pen") {
      isPenDownRef.current = true;
      setCurrentPenPath([pixelToGeo(x, y)]);
    } else if (markupTool === "text") {
      setPendingTextPos({ x, y });
    }
  }, [activeSymbolKey, activeLayer, onAddAnnotation, markupTool, getSvgCoords, pixelToGeo]);

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
        if (onAddAnnotation) {
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
            operationalDay: 1,
            createdAt: new Date().toISOString(),
          });
        } else {
          setTextMarkers(prev => [...prev, { geo, text }]);
        }
      }
      setPendingTextPos(null);
    } else if (e.key === "Escape") {
      setPendingTextPos(null);
    }
  }, [pendingTextPos, pixelToGeo, onAddAnnotation, activeLayer, activeSymbolKey]);

  const clearMarkup = useCallback(() => {
    setPenPaths([]);
    setTextMarkers([]);
    setCurrentPenPath([]);
  }, []);

  const handleMapRefCallback = useCallback((m: maplibregl.Map) => {
    consoleMapRef.current = m;
    const bump = () => setMapRenderKey(k => k + 1);
    m.on("move", bump);
    m.on("zoom", bump);
    m.on("rotate", bump);
  }, []);

  // ── Map snapshot capture ──────────────────────────────────────────────────

  const captureMapSnapshot = useCallback((): Promise<string | undefined> => {
    const map = consoleMapRef.current;
    if (!map) return Promise.resolve(undefined);

    return new Promise((resolve) => {
      map.once("render", () => {
        try {
          const glCanvas = map.getCanvas();
          const rect = glCanvas.getBoundingClientRect();
          const w = Math.round(rect.width);
          const h = Math.round(rect.height);

          const offscreen = document.createElement("canvas");
          offscreen.width = w;
          offscreen.height = h;
          const ctx = offscreen.getContext("2d");
          if (!ctx) { resolve(undefined); return; }

          ctx.drawImage(glCanvas, 0, 0, w, h);

          const svg = svgRef.current;
          if (!svg || !svg.childElementCount) {
            const dataUrl = offscreen.toDataURL("image/png");
            setMapSnapshot(dataUrl);
            resolve(dataUrl);
            return;
          }

          const clone = svg.cloneNode(true) as SVGSVGElement;
          clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
          clone.setAttribute("width", String(w));
          clone.setAttribute("height", String(h));
          const svgStr = new XMLSerializer().serializeToString(clone);
          const blob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
          const url = URL.createObjectURL(blob);
          const img = new Image();
          img.onload = () => {
            ctx.drawImage(img, 0, 0);
            URL.revokeObjectURL(url);
            const dataUrl = offscreen.toDataURL("image/png");
            setMapSnapshot(dataUrl);
            resolve(dataUrl);
          };
          img.onerror = () => {
            URL.revokeObjectURL(url);
            const dataUrl = offscreen.toDataURL("image/png");
            setMapSnapshot(dataUrl);
            resolve(dataUrl);
          };
          img.src = url;
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
    incidentLocation,
    mapSnapshotDataUrl: snapshot ?? mapSnapshot,
    annotations: incidentAnnotations,
    weather: activePeriod?.weather,
    hazardType,
    incidentComplexity,
    hazardZones,
    resources,
    agencies,
    period: activePeriod,
  }), [incidentName, incidentLocation, mapSnapshot, incidentAnnotations, activePeriod, hazardType, incidentComplexity, hazardZones, resources, agencies]);

  // ── Form rendering ────────────────────────────────────────────────────────

  const renderForm = useCallback((formId: ICSFormId, snapshot?: string) => {
    const opts = buildFormOptions(snapshot);
    let html = "";
    if (formId === "ics201") html = buildICS201HTML(opts);
    else if (formId === "ics202") html = buildICS202HTML(opts);
    else if (formId === "ics203") html = buildICS203HTML(opts);
    else if (formId === "ics204") html = buildICS204HTML(opts);
    else if (formId === "ics205") html = buildICS205HTML(opts);
    else if (formId === "ics206") html = buildICS206HTML(opts);
    else if (formId === "ics207") html = buildICS207HTML(opts);
    else if (formId === "ics208") html = buildICS208HTML(opts);
    else if (formId === "ics213") html = buildICS213HTML(opts);
    else if (formId === "ics214") html = buildICS214HTML(opts);
    else if (formId === "ics215") html = buildICS215HTML(opts);
    else if (formId === "ics215a") html = buildICS215aHTML(opts);
    else if (formId === "full-iap") html = buildFullIAPHTML(opts);
    setFormHtml(html);
    setSelectedForm(formId);
  }, [buildFormOptions]);

  const handleFormSelect = useCallback(async (formId: ICSFormId) => {
    const snap = await captureMapSnapshot();
    setConsoleTab("iap");
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

  // Count of annotations by layer for the situation panel
  const annotationsByLayer = incidentAnnotations.reduce((acc, a) => {
    acc[a.layer] = (acc[a.layer] ?? 0) + 1;
    return acc;
  }, {} as Record<AnnotationLayer, number>);

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
        </div>
        <div className="eoc-header-right">
          <button className="eoc-action-btn" onClick={async () => { await captureMapSnapshot(); setConsoleTab("situation"); }} title="Capture map snapshot">
            🖨 Print
          </button>
          <button className="eoc-action-btn" onClick={() => handleFormSelect("full-iap")} title="Generate Full IAP Package">
            Full IAP
          </button>
        </div>
      </div>

      {/* ── Sub-tabs ───────────────────────────────────────────────── */}
      <div className="eoc-subtabs">
        {(["situation", "command", "operations", "planning", "logistics", "finance", "iap", "map"] as ConsoleTab[]).map((tab) => (
          <button
            key={tab}
            className={`eoc-subtab${consoleTab === tab ? " active" : ""}`}
            onClick={() => setConsoleTab(tab)}
          >
            {tab === "situation" ? "Situation"
              : tab === "command" ? "Command"
              : tab === "operations" ? "Ops"
              : tab === "planning" ? "Plans"
              : tab === "logistics" ? "Logs"
              : tab === "finance" ? "Finance"
              : tab === "iap" ? "IAP Forms"
              : "Map"}
          </button>
        ))}
      </div>

      {/* ── Main body ─────────────────────────────────────────────── */}
      <div className={`eoc-body${isMapFullWidth ? " eoc-body--map-full" : ""}`}>

        {/* Left: read-only map + markup overlay */}
        <div className={`eoc-map-panel${isMapFullWidth ? " eoc-map-panel--full" : ""}`}>
          <MapView
            onMapClick={() => {}}
            ignitionPoint={incidentLocation}
            overlayRoads={overlayRoads}
            overlayRoadsVisible={overlayRoadsVisible}
            overlayCommunities={overlayCommunities}
            overlayCommunitiesVisible={overlayCommunitiesVisible}
            overlayInfrastructure={overlayInfrastructure}
            overlayInfrastructureVisible={overlayInfrastructureVisible}
            readOnly
            mapRefCallback={handleMapRefCallback}
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
            {/* Incident annotations — dimmed if not on active layer */}
            {incidentAnnotations.map((ann) => {
              const isActive = ann.layer === activeLayer;
              const opacity = isActive ? 1 : 0.3;
              const symDef = SYMBOL_DEFS.find(s => s.key === ann.symbolKey);
              const color = ann.color ?? symDef?.color ?? "#ffffff";
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
            <button className="eoc-markup-tool" onClick={() => consoleMapRef.current?.zoomIn()} title="Zoom in">+</button>
            <button className="eoc-markup-tool" onClick={() => consoleMapRef.current?.zoomOut()} title="Zoom out">−</button>
            <div className="eoc-markup-divider" />
            <span className="eoc-markup-label">MARK</span>
            <button
              className={`eoc-markup-tool${showSymbolPicker ? " active" : ""}`}
              onClick={() => setShowSymbolPicker(v => !v)}
              title="ICS symbol palette"
            >⊕</button>
            <button
              className={`eoc-markup-tool${isFetchingFacilities ? " active" : ""}`}
              title={!incidentLocation ? "Set an incident location first to fetch nearby resources" : "Fetch nearby emergency facilities from OpenStreetMap"}
              disabled={isFetchingFacilities || !incidentLocation || !onFetchFacilities}
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
              title="Freehand draw on active layer"
            >✏</button>
            <button
              className={`eoc-markup-tool${activeSymbolKey === "text_label" ? " active" : ""}`}
              onClick={() => setActiveSymbolKey(k => k === "text_label" ? null : "text_label")}
              title="Place text label on active layer"
            >T</button>
            <button
              className="eoc-markup-tool"
              onClick={clearMarkup}
              title="Clear freehand markup"
              disabled={penPaths.length === 0 && textMarkers.length === 0 && currentPenPath.length === 0}
            >⌫</button>
          </div>

          {fetchFacilitiesMsg && (
            <div className="eoc-fetch-msg">{fetchFacilitiesMsg}</div>
          )}

          {mapSnapshot && (
            <img className="eoc-print-map" src={mapSnapshot} alt="Map snapshot" />
          )}
        </div>

        {/* Right: content panel (hidden in full-map mode) */}
        {!isMapFullWidth && (
          <div className="eoc-data-panels">

            {/* ── Situation tab ───────────────────────────── */}
            {consoleTab === "situation" && (
              <div className="eoc-situation-panel">
                <div className="eoc-section-header">Incident Status</div>
                <div className="eoc-kpi-grid">
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Incident</span>
                    <span className="eoc-kpi-value">{incidentName}</span>
                  </div>
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Hazard Type</span>
                    <span className="eoc-kpi-value" style={{ textTransform: "capitalize" }}>
                      {hazardType ? hazardType.replace("_", " ") : "Not set"}
                    </span>
                  </div>
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Complexity</span>
                    <span className="eoc-kpi-value">
                      {incidentComplexity ? `Type ${incidentComplexity}` : "—"}
                    </span>
                  </div>
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Location</span>
                    <span className="eoc-kpi-value">
                      {incidentLocation
                        ? `${incidentLocation.lat.toFixed(4)}, ${incidentLocation.lng.toFixed(4)}`
                        : "Not set — click map"}
                    </span>
                  </div>
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Hazard Zones</span>
                    <span className="eoc-kpi-value">{hazardZones?.length ?? 0}</span>
                  </div>
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Resources</span>
                    <span className="eoc-kpi-value">{resources?.length ?? 0}</span>
                  </div>
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Agencies</span>
                    <span className="eoc-kpi-value">{agencies?.length ?? 0}</span>
                  </div>
                  <div className="eoc-kpi">
                    <span className="eoc-kpi-label">Annotations</span>
                    <span className="eoc-kpi-value">{incidentAnnotations.length}</span>
                  </div>
                  {Object.entries(annotationsByLayer).map(([layer, count]) => (
                    <div key={layer} className="eoc-kpi">
                      <span className="eoc-kpi-label">{layer}</span>
                      <span className="eoc-kpi-value">{count} markers</span>
                    </div>
                  ))}
                </div>
                <div className="eoc-section-header" style={{ marginTop: 16 }}>Map Controls</div>
                <div style={{ padding: "8px 0", fontSize: 12, color: "#a0b0c0" }}>
                  Click map to set incident location. Use the ⊕ button to annotate with ICS symbols. Use 📡 to auto-fetch nearby emergency facilities.
                </div>
              </div>
            )}

            {/* ── Section workspace tabs ──────────────────── */}
            {(["command", "operations", "planning", "logistics", "finance"] as const).map((section) =>
              consoleTab === section ? (
                <div key={section} className="eoc-section-workspace">
                  <SectionWorkspace
                    section={section}
                    resources={resources ?? []}
                    agencies={agencies ?? []}
                    onResourcesChange={onResourcesChange ?? (() => {})}
                    onAgenciesChange={onAgenciesChange ?? (() => {})}
                    onGenerateForm={(formId) => {
                      setConsoleTab("iap");
                      handleFormSelect(formId);
                    }}
                  />
                </div>
              ) : null
            )}

            {/* ── IAP Forms tab ───────────────────────────── */}
            {consoleTab === "iap" && (
              <div className="eoc-forms-panel">
                <div className="eoc-forms-header">
                  <span className="eoc-forms-title">IAP FORMS</span>
                  <span className="eoc-forms-subtitle">NIMS Incident Action Plan</span>
                </div>

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

                <div className="eoc-form-group">
                  <span className="eoc-form-group-label">Safety &amp; Org</span>
                  <div className="eoc-form-btns">
                    {(["ics207", "ics208", "ics215a"] as ICSFormId[]).map((id) => (
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

                <div className="eoc-form-group">
                  <span className="eoc-form-group-label">Resources &amp; Messages</span>
                  <div className="eoc-form-btns">
                    {(["ics215", "ics213", "ics214"] as ICSFormId[]).map((id) => (
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

                <div className="eoc-form-group">
                  <div className="eoc-form-btns">
                    <button
                      className={`eoc-form-btn eoc-form-btn--primary${selectedForm === "full-iap" ? " active" : ""}`}
                      onClick={() => handleFormSelect("full-iap")}
                    >
                      ⬇ Generate Full IAP (201–215A)
                    </button>
                  </div>
                </div>

                {formHtml && (
                  <div className="eoc-form-viewer">
                    <div className="eoc-form-viewer-toolbar">
                      <span className="eoc-form-viewer-name">{ICS_FORM_LABELS[selectedForm]}</span>
                      <button className="eoc-form-action" onClick={handlePrintForm} title="Print this form">🖨 Print</button>
                      <button className="eoc-form-action" onClick={handleOpenInNewWindow} title="Open in new window">↗ New window</button>
                    </div>
                    <iframe
                      ref={iframeRef}
                      className="eoc-form-iframe"
                      srcDoc={formHtml}
                      title={ICS_FORM_LABELS[selectedForm]}
                    />
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
