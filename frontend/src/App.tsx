/** AIMS Console — All-Hazards Incident Management System */

import { useCallback, useState } from "react";
import MapView from "./components/MapView";
import OverlayPanel from "./components/OverlayPanel";
import type { OverlayLayers, LayerType } from "./components/OverlayPanel";
import EOCConsole from "./components/EOCConsole";
import OperationalPeriodPanel from "./components/OperationalPeriodPanel";
import IncidentPanel from "./components/IncidentPanel";
import { useIncident } from "./hooks/useIncident";

// ── EOC start screen — shown when no incident is active ──────────────────────

function EocStartScreen({ onCreate }: { onCreate: (name: string) => void }) {
  const [name, setName] = useState("");
  const submit = () => {
    const n = name.trim();
    if (n) { onCreate(n); setName(""); }
  };
  return (
    <div className="eoc-start-screen">
      <div className="eoc-start-card">
        <div className="eoc-start-icon">🚨</div>
        <h2 className="eoc-start-title">Start a New Incident</h2>
        <p className="eoc-start-hint">
          Name the incident before opening the EOC Console.<br />
          You can rename it at any time from the period strip.
        </p>
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
          Open EOC Console
        </button>
        <p className="eoc-start-hint" style={{ marginTop: 8 }}>
          Or resume an existing incident from the <strong>Incidents</strong> panel in the sidebar.
        </p>
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
  const [activeTab, setActiveTab] = useState<"map" | "eoc">("map");

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
    exportIncident,
    importIncident,
  } = useIncident();

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
    setIncidentLocation({ lat, lng });
  }, []);

  const handleClearLocation = useCallback(() => {
    setIncidentLocation(null);
  }, []);

  return (
    <div className="app">
      {/* ── Fixed sidebar ───────────────────────────────────── */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>AIMS CONSOLE</h1>
          <span className="sidebar-subtitle">All-Hazards Incident Management</span>
        </div>
        <div className="sidebar-content">
          <OverlayPanel
            layers={overlayLayers}
            onLayerLoad={handleOverlayLoad}
            onLayerToggle={handleOverlayToggle}
            onLayerClear={handleOverlayClear}
          />
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
        </div>
        <footer className="sidebar-footer">
          <button className="sidebar-footer-btn">⚙ Settings</button>
          <button className="sidebar-footer-btn">? Support</button>
        </footer>
      </aside>

      {/* ── Fixed top bar ───────────────────────────────────── */}
      <header className="top-bar">
        <div className="top-bar-left">
          <span className="top-bar-title">AIMS Console</span>
          <nav className="top-bar-nav">
            <button className={`nav-link${activeTab === "map" ? " active" : ""}`} onClick={() => setActiveTab("map")}>Map</button>
            <button className={`nav-link${activeTab === "eoc" ? " active" : ""}`} onClick={() => setActiveTab("eoc")}>EOC Console</button>
          </nav>
        </div>
        <div className="top-bar-right">
          <button className="btn-emergency">Emergency Alert</button>
        </div>
      </header>

      {/* ── EOC Console tab ─────── */}
      {activeTab === "eoc" && !incident && (
        <div className="eoc-tab-wrapper">
          <EocStartScreen onCreate={createIncident} />
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
          />
        </div>
      )}

      {/* ── Map area — always mounted so MapLibre doesn't reinitialize on tab switch ─── */}
      <main className="map-area" style={activeTab === "eoc" ? { display: "none" } : {}}>
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
        />
      </main>
    </div>
  );
}
