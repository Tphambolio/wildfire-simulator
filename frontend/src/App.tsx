/** AIMS Console — All-Hazards Incident Management System */

import { useCallback, useState, useEffect, useRef } from "react";
import MapView from "./components/MapView";
import OverlayPanel from "./components/OverlayPanel";
import type { OverlayLayers, LayerType } from "./components/OverlayPanel";
import EOCConsole from "./components/EOCConsole";
import OperationalPeriodPanel from "./components/OperationalPeriodPanel";
import IncidentPanel from "./components/IncidentPanel";
import IncidentSetupPanel from "./components/IncidentSetupPanel";
import HazardZonePanel from "./components/HazardZonePanel";
import TeamSummaryPanel from "./components/TeamSummaryPanel";
import NextStepCard from "./components/NextStepCard";
import { useIncident } from "./hooks/useIncident";
import type { HazardType, HazardZone, IncidentResource } from "./types/incident";
import type { ConsoleTab } from "./components/EOCConsole";
import type { BriefingData } from "./components/InitBriefingPanel";

// ── Cloud Sync Panel (inline — shows share code or share button) ─────────────

interface SyncPanelProps {
  shareCode?: string;
  onShare: () => Promise<string>;
}

function SyncPanel({ shareCode, onShare }: SyncPanelProps) {
  const [syncing, setSyncing] = useState(false);
  const [localCode, setLocalCode] = useState(shareCode ?? "");

  const handleShare = async () => {
    setSyncing(true);
    try {
      const code = await onShare();
      if (code) {
        setLocalCode(code);
        const url = `${window.location.origin}?incident=${code}`;
        await navigator.clipboard.writeText(url).catch(() => {/* silent */});
      }
    } finally {
      setSyncing(false);
    }
  };

  const handleCopyLink = async () => {
    const code = localCode || shareCode;
    if (!code) return;
    const url = `${window.location.origin}?incident=${code}`;
    await navigator.clipboard.writeText(url).catch(() => {/* silent */});
  };

  const effectiveCode = localCode || shareCode;

  return (
    <div className="sync-panel">
      {!effectiveCode ? (
        <button className="sync-btn" onClick={handleShare} disabled={syncing}>
          {syncing ? "Uploading…" : "☁ Share Incident"}
        </button>
      ) : (
        <div className="sync-active">
          <span className="sync-code">🔗 {effectiveCode}</span>
          <button className="sync-btn" onClick={handleCopyLink}>Copy Link</button>
          <span className="sync-indicator">Syncing</span>
        </div>
      )}
    </div>
  );
}

// ── Sidebar: no active incident ──────────────────────────────────────────────

interface NoIncidentPanelProps {
  incidents: import("./types/incident").IncidentSession[];
  onCreate: (name: string) => void;
  onLoad: (id: string) => void;
  onImport: (file: File) => Promise<import("./types/incident").IncidentSession>;
}

function NoIncidentPanel({ incidents, onCreate, onLoad, onImport }: NoIncidentPanelProps) {
  const [mode, setMode] = useState<"idle" | "new" | "open">("idle");
  const [name, setName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleCreate = () => {
    const n = name.trim();
    if (!n) return;
    onCreate(n);
    setName("");
    setMode("idle");
  };

  return (
    <div className="no-incident-panel">
      {mode === "idle" && (
        <div className="no-incident-actions">
          <button className="no-incident-btn no-incident-btn--primary" onClick={() => setMode("new")}>
            + New Incident
          </button>
          {incidents.length > 0 && (
            <button className="no-incident-btn no-incident-btn--secondary" onClick={() => setMode("open")}>
              Open Incident
            </button>
          )}
          <button
            className="no-incident-btn no-incident-btn--ghost"
            onClick={() => fileInputRef.current?.click()}
          >
            Import JSON
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,application/json"
            style={{ display: "none" }}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file) { await onImport(file).catch(() => {}); }
              e.target.value = "";
            }}
          />
        </div>
      )}

      {mode === "new" && (
        <div className="no-incident-form">
          <label className="no-incident-label">Incident name</label>
          <input
            className="no-incident-input"
            type="text"
            placeholder="e.g. River Valley Flood 2026"
            value={name}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            maxLength={60}
          />
          <div className="no-incident-form-actions">
            <button className="no-incident-btn no-incident-btn--primary" onClick={handleCreate} disabled={!name.trim()}>
              Start
            </button>
            <button className="no-incident-btn no-incident-btn--ghost" onClick={() => { setMode("idle"); setName(""); }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {mode === "open" && (
        <div className="no-incident-list">
          <div className="no-incident-list-header">
            <span>Recent Incidents</span>
            <button className="no-incident-btn no-incident-btn--ghost" style={{ padding: "2px 8px", fontSize: "0.8em" }} onClick={() => setMode("idle")}>✕</button>
          </div>
          {incidents.map((inc) => (
            <button
              key={inc.id}
              className="no-incident-list-item"
              onClick={() => { onLoad(inc.id); setMode("idle"); }}
            >
              <span className="no-incident-list-name">{inc.name}</span>
              <span className="no-incident-list-meta">
                {inc.operationalPeriods.length} day{inc.operationalPeriods.length !== 1 ? "s" : ""}
                {" · "}{inc.status === "active" ? "active" : "closed"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── EOC start screen — shown when no incident is active ──────────────────────

interface EocStartScreenProps {
  incidents: import("./types/incident").IncidentSession[];
  onCreate: (name: string) => void;
  onLoad: (id: string) => void;
  onImport: (file: File) => Promise<import("./types/incident").IncidentSession>;
}

function EocStartScreen({ incidents, onCreate, onLoad, onImport }: EocStartScreenProps) {
  const [mode, setMode] = useState<"choose" | "new">("choose");
  const [name, setName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const submit = () => {
    const n = name.trim();
    if (n) { onCreate(n); setName(""); }
  };

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString("en-CA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  return (
    <div className="eoc-start-screen">
      <div className="eoc-start-card">
        <div className="eoc-start-icon">🚨</div>
        <h2 className="eoc-start-title">AIMS Console</h2>
        <p className="eoc-start-hint">All-Hazards Incident Management</p>

        {mode === "choose" && (
          <>
            <button className="eoc-start-btn" onClick={() => setMode("new")}>
              + New Incident
            </button>

            {incidents.length > 0 && (
              <>
                <div className="eoc-start-divider">or resume</div>
                <div className="eoc-start-list">
                  {incidents.slice(0, 6).map((inc) => (
                    <button
                      key={inc.id}
                      className="eoc-start-list-item"
                      onClick={() => onLoad(inc.id)}
                    >
                      <span className="eoc-start-list-name">{inc.name}</span>
                      <span className="eoc-start-list-meta">
                        {inc.operationalPeriods.length} day{inc.operationalPeriods.length !== 1 ? "s" : ""}
                        {" · "}{formatDate(inc.updatedAt)}
                      </span>
                    </button>
                  ))}
                </div>
              </>
            )}

            <button
              className="eoc-start-import-btn"
              onClick={() => fileInputRef.current?.click()}
            >
              Import from JSON
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              style={{ display: "none" }}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) await onImport(file).catch(() => {});
                e.target.value = "";
              }}
            />
          </>
        )}

        {mode === "new" && (
          <>
            <input
              className="eoc-start-input"
              type="text"
              placeholder="e.g. River Valley Flood 2026"
              value={name}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
              maxLength={60}
            />
            <button className="eoc-start-btn" onClick={submit} disabled={!name.trim()}>
              Set Location on Map →
            </button>
            <button className="eoc-start-import-btn" onClick={() => { setMode("choose"); setName(""); }}>
              ← Back
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ── Default overlay state ────────────────────────────────────────────────────

const DEFAULT_OVERLAY_LAYERS: OverlayLayers = {
  roads: { data: null, visible: true },
  communities: { data: null, visible: true },
  infrastructure: { data: null, visible: true },
};

export default function App() {
  const [incidentLocation, setIncidentLocation] = useState<{ lat: number; lng: number } | null>(null);
  const [overlayLayers, setOverlayLayers] = useState<OverlayLayers>(DEFAULT_OVERLAY_LAYERS);
  const [activeTab, setActiveTab] = useState<"map" | "eoc">("eoc");
  const [eocConsoleTab, setEocConsoleTab] = useState<ConsoleTab>("situation");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const handleOpenSection = useCallback((section: "command" | "operations" | "planning" | "logistics" | "finance") => {
    setActiveTab("eoc");
    setEocConsoleTab(section);
  }, []);

  const handleNavigate = useCallback((tab: "map" | "eoc", eocTab?: ConsoleTab) => {
    setActiveTab(tab);
    if (eocTab) setEocConsoleTab(eocTab);
  }, []);

  // Zone drawing state
  const [drawingZone, setDrawingZone] = useState(false);
  const [drawingZonePoints, setDrawingZonePoints] = useState<[number, number][]>([]);
  const [drawingZoneName, setDrawingZoneName] = useState("");
  const [drawingZoneColor, setDrawingZoneColor] = useState("#f44336");

  // ── Incident store (multi-day operational periods) ────────
  const {
    incident,
    activeIncidentId,
    incidents,
    activePeriod,
    createIncident,
    loadIncident,
    closeIncident,
    deleteIncident,
    advancePeriod,
    setActivePeriodIndex,
    addAnnotation,
    removeAnnotation,
    clearLayerAnnotations,
    fetchAndPlaceFacilities,
    updateIncidentField,
    updatePeriodField,
    addHazardZone,
    removeHazardZone,
    clearHazardZones,
    exportIncident,
    importIncident,
    shareIncident,
    joinIncident,
    addResource,
  } = useIncident();

  const handleBriefingComplete = useCallback((data: BriefingData) => {
    // Persist IC name, jurisdiction, and completion timestamp on the incident
    updateIncidentField("incidentCommanderName", data.icName);
    updateIncidentField("ics201CompletedAt", new Date().toISOString());
    if (data.jurisdiction) updateIncidentField("jurisdiction", data.jurisdiction);
    // Persist situation narrative and objectives on the active period
    updatePeriodField("situationNarrative", data.narrative);
    if (data.objectives.length > 0) updatePeriodField("objectives", data.objectives);
    // Auto-add IC as a Command resource so ICS-207 / section workspaces see them
    if (data.icName) {
      const icResource: IncidentResource = {
        id: crypto.randomUUID(),
        kind: "person",
        name: data.icName,
        icsSection: "command",
        icsPosition: "Incident Commander",
        agency: data.jurisdiction || "",
        status: "assigned",
      };
      addResource(icResource);
    }
  }, [updateIncidentField, updatePeriodField, addResource]);

  // ── Join incident from URL param on mount ─────────────────
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("incident");
    if (code && !activeIncidentId) {
      joinIncident(code).catch(() => {/* silent */});
    }
    // Only run on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleOverlayLoad = useCallback((type: LayerType, data: GeoJSON.FeatureCollection) => {
    setOverlayLayers((prev) => ({ ...prev, [type]: { ...prev[type], data } }));
  }, []);

  const handleOverlayToggle = useCallback((type: LayerType, visible: boolean) => {
    setOverlayLayers((prev) => ({ ...prev, [type]: { ...prev[type], visible } }));
  }, []);

  const handleOverlayClear = useCallback((type: LayerType) => {
    setOverlayLayers((prev) => ({ ...prev, [type]: { data: null, visible: true } }));
  }, []);

  const handleMapClick = useCallback((lat: number, lng: number) => {
    const isFirstLocation = !incidentLocation;
    setIncidentLocation({ lat, lng });
    // Auto-advance to EOC Console the first time a location is set
    if (isFirstLocation) {
      setActiveTab("eoc");
    }
  }, [incidentLocation]);

  const handleClearLocation = useCallback(() => {
    setIncidentLocation(null);
  }, []);

  const handleZoneDrawStart = useCallback((name: string, color: string) => {
    setDrawingZoneName(name);
    setDrawingZoneColor(color);
    setDrawingZonePoints([]);
    setDrawingZone(true);
  }, []);

  const handleZonePoint = useCallback((lng: number, lat: number) => {
    setDrawingZonePoints((prev) => [...prev, [lng, lat]]);
  }, []);

  const handleZoneClose = useCallback(() => {
    setDrawingZone(false);
    setDrawingZonePoints((pts) => {
      if (pts.length >= 3) {
        const zone: HazardZone = {
          id: crypto.randomUUID(),
          name: drawingZoneName,
          color: drawingZoneColor,
          polygon: pts,
          createdAt: new Date().toISOString(),
        };
        addHazardZone(zone);
      }
      return [];
    });
  }, [drawingZoneName, drawingZoneColor, addHazardZone]);

  const handleZoneCancel = useCallback(() => {
    setDrawingZone(false);
    setDrawingZonePoints([]);
  }, []);

  return (
    <div className={`app${!incident ? " app--no-sidebar" : ""}`}>
      {/* ── Sidebar backdrop — tap outside to close on mobile ── */}
      {sidebarOpen && (
        <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />
      )}

      {/* ── Fixed sidebar ───────────────────────────────────── */}
      <aside className={`sidebar${sidebarOpen ? " sidebar--open" : ""}`} style={!incident ? { display: "none" } : {}}>
        {/* Mobile close button — only visible when drawer is open */}
        <button
          className="sidebar-close-btn"
          onClick={() => setSidebarOpen(false)}
          aria-label="Close sidebar"
        >
          ✕
        </button>
        <div className="sidebar-brand">
          <h1>AIMS CONSOLE</h1>
          <span className="sidebar-subtitle">All-Hazards Incident Management</span>
        </div>
        {incident && (
          <SyncPanel
            shareCode={incident.shareCode}
            onShare={shareIncident}
          />
        )}
        <div className="sidebar-content">
          {!incident ? (
            <NoIncidentPanel
              incidents={incidents}
              onCreate={createIncident}
              onLoad={loadIncident}
              onImport={importIncident}
            />
          ) : (
            <>
              <IncidentPanel
                incidents={incidents}
                activeIncidentId={activeIncidentId}
                onCreate={createIncident}
                onLoad={loadIncident}
                onClose={closeIncident}
                onDelete={deleteIncident}
                onExport={exportIncident}
                onImport={importIncident}
              />
              <IncidentSetupPanel
                hazardType={incident.hazardType}
                onHazardTypeChange={(h: HazardType) => updateIncidentField("hazardType", h)}
                incidentComplexity={incident.incidentComplexity}
                onComplexityChange={(c) => updateIncidentField("incidentComplexity", c)}
                weather={activePeriod?.weather ?? { wind_speed: 20, wind_direction: 180, temperature: 15, relative_humidity: 50, precipitation: 0 }}
                onWeatherChange={(w) => updatePeriodField("weather", w)}
                incidentLocation={incidentLocation}
                onFetchFacilities={incidentLocation
                  ? async () => fetchAndPlaceFacilities(incidentLocation.lat, incidentLocation.lng)
                  : undefined}
              />
              <OverlayPanel
                layers={overlayLayers}
                onLayerLoad={handleOverlayLoad}
                onLayerToggle={handleOverlayToggle}
                onLayerClear={handleOverlayClear}
              />
              <HazardZonePanel
                hazardType={incident.hazardType}
                zones={activePeriod?.hazardZones ?? []}
                isDrawing={drawingZone}
                drawingPoints={drawingZonePoints}
                onDrawStart={handleZoneDrawStart}
                onDrawCancel={handleZoneCancel}
                onDrawClose={handleZoneClose}
                onRemoveZone={removeHazardZone}
                onClearAll={clearHazardZones}
              />
              <NextStepCard incident={incident} onNavigate={handleNavigate} />
              <TeamSummaryPanel
                resources={incident.resources ?? []}
                agencies={incident.agencies ?? []}
                onOpenSection={handleOpenSection}
              />
            </>
          )}
        </div>
        <footer className="sidebar-footer">
          <button className="sidebar-footer-btn">⚙ Settings</button>
          <button className="sidebar-footer-btn">? Support</button>
        </footer>
      </aside>

      {/* ── Fixed top bar ───────────────────────────────────── */}
      <header className="top-bar">
        <div className="top-bar-left">
          {/* Hamburger only shown after EOC is unlocked — no distraction during map setup */}
          {incident && incidentLocation && (
            <button className="mobile-menu-btn" onClick={() => setSidebarOpen(v => !v)} aria-label="Toggle sidebar">
              ☰
            </button>
          )}
          <span className="top-bar-title">AIMS Console</span>
          <nav className="top-bar-nav">
            <button
              className={`nav-link${activeTab === "map" ? " active" : ""}`}
              onClick={() => setActiveTab("map")}
            >
              Map
            </button>
            <button
              className={`nav-link${activeTab === "eoc" ? " active" : ""}${incident && !incidentLocation ? " nav-link--locked" : ""}`}
              onClick={() => incidentLocation || !incident ? setActiveTab("eoc") : undefined}
              title={incident && !incidentLocation ? "Set incident location on the map first" : undefined}
            >
              <span className="nav-label-full">EOC Console</span>
              <span className="nav-label-short">EOC</span>
              {incident && !incidentLocation && <span className="nav-lock-icon">🔒</span>}
            </button>
          </nav>
        </div>
        <div className="top-bar-right">
          <button className="btn-emergency">
            <span className="btn-emergency-full">Emergency Alert</span>
            <span className="btn-emergency-short">⚠ Alert</span>
          </button>
        </div>
      </header>

      {/* ── EOC Console tab ─────── */}
      {activeTab === "eoc" && !incident && (
        <div className="eoc-tab-wrapper">
          <EocStartScreen
            incidents={incidents}
            onCreate={(name) => { createIncident(name); setActiveTab("map"); }}
            onLoad={(id) => { loadIncident(id); setActiveTab("eoc"); }}
            onImport={importIncident}
          />
        </div>
      )}
      {activeTab === "eoc" && incident && (
        <div className="eoc-tab-wrapper">
          <OperationalPeriodPanel
            incident={incident}
            activePeriod={activePeriod}
            onPeriodSelect={setActivePeriodIndex}
            onAdvancePeriod={advancePeriod}
            onUpdateName={(name) => updateIncidentField("name", name)}
          />
          <EOCConsole
            incidentLocation={incidentLocation}
            overlayRoads={overlayLayers.roads.data}
            overlayRoadsVisible={overlayLayers.roads.visible}
            overlayCommunities={overlayLayers.communities.data}
            overlayCommunitiesVisible={overlayLayers.communities.visible}
            overlayInfrastructure={overlayLayers.infrastructure.data}
            overlayInfrastructureVisible={overlayLayers.infrastructure.visible}
            incidentAnnotations={activePeriod?.annotations ?? []}
            onAddAnnotation={addAnnotation}
            onRemoveAnnotation={removeAnnotation}
            onClearLayer={clearLayerAnnotations}
            onFetchFacilities={incidentLocation
              ? async () => fetchAndPlaceFacilities(incidentLocation.lat, incidentLocation.lng)
              : undefined}
            incidentName={incident?.name}
            onIncidentNameChange={(name) => updateIncidentField("name", name)}
            hazardType={incident?.hazardType}
            incidentComplexity={incident?.incidentComplexity}
            hazardZones={activePeriod?.hazardZones}
            resources={incident?.resources}
            agencies={incident?.agencies}
            activePeriod={activePeriod ?? undefined}
            onResourcesChange={(r) => updateIncidentField("resources", r)}
            onAgenciesChange={(a) => updateIncidentField("agencies", a)}
            initialConsoleTab={eocConsoleTab}
            onConsoleTabChange={setEocConsoleTab}
            ics201CompletedAt={incident?.ics201CompletedAt}
            onBriefingComplete={handleBriefingComplete}
          />
        </div>
      )}

      {/* ── Map area — always mounted so MapLibre doesn't reinitialize on tab switch ─── */}
      <main className="map-area" style={activeTab === "eoc" ? { display: "none" } : {}}>
        {/* Location prompt — shown when incident active but no location set yet */}
        {incident && !incidentLocation && (
          <div className="map-location-prompt">
            <span className="map-location-prompt-icon">📍</span>
            <span>Tap the map or use <strong>⊕</strong> to set the incident location</span>
            <span className="map-location-prompt-sub">Required before opening the EOC Console</span>
          </div>
        )}
        <MapView
          onMapClick={handleMapClick}
          onClearIgnition={handleClearLocation}
          ignitionPoint={incidentLocation}
          overlayRoads={overlayLayers.roads.data}
          overlayRoadsVisible={overlayLayers.roads.visible}
          overlayCommunities={overlayLayers.communities.data}
          overlayCommunitiesVisible={overlayLayers.communities.visible}
          overlayInfrastructure={overlayLayers.infrastructure.data}
          overlayInfrastructureVisible={overlayLayers.infrastructure.visible}
          hazardZones={activePeriod?.hazardZones ?? []}
          drawingZone={drawingZone}
          drawingZonePoints={drawingZonePoints}
          onZonePoint={handleZonePoint}
          onZoneClose={handleZoneClose}
        />
      </main>
    </div>
  );
}
