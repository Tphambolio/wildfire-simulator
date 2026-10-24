/**
 * "About & sources" tab: what FireSim is for, its limits and validation, methods and references,
 * data sources and licences, responsible use, the ICS Canada forms, help, and the version.
 * The explanations and literature that used to sit in the panels live here (owner direction
 * 2026-10-10); each section ends with the repository documents it summarises.
 */
import { useEffect, useRef, useState } from "react";
import { getVersion } from "../services/api";
import { BADGES, TIPS, doc, REPO_URL } from "../content/explanations";
import { HFI_CLASS_CAVEAT, HFI_CLASS_SOURCE } from "../utils/fireClasses";
import { RPAS_NOTE } from "../utils/suppressionAdvisory";

export const ABOUT_SECTIONS = [
  ["about-use", "What FireSim is for"],
  ["about-limits", "Limits and validation"],
  ["about-methods", "Methods and references"],
  ["about-data", "Data sources and licences"],
  ["about-responsible", "Responsible use"],
  ["about-forms", "ICS Canada forms"],
  ["about-help", "Using the app"],
  ["about-version", "Version"],
] as const;

export type AboutSectionId = (typeof ABOUT_SECTIONS)[number][0];

function Docs({ links }: { links: Array<[string, string]> }) {
  return (
    <p className="about-docs">
      Read more:{" "}
      {links.map(([label, href], i) => (
        <span key={href}>
          {i > 0 && " · "}
          <a href={href} target="_blank" rel="noopener noreferrer">{label}</a>
        </span>
      ))}
    </p>
  );
}

function Section({ id, title, children }: { id: AboutSectionId; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="about-section" aria-labelledby={`${id}-h`} tabIndex={-1}>
      <h3 id={`${id}-h`}>{title}</h3>
      {children}
    </section>
  );
}

const REFERENCES: Array<[string, string]> = [
  ["Fire Behavior Prediction System", "Forestry Canada Fire Danger Group (1992), Information Report ST-X-3; updates: Wotton, Alexander & Taylor (2009), GLC-X-10 (incl. grass curing)."],
  ["Fire Weather Index System", "Van Wagner & Pickett (1985), Forestry Technical Report 33; Van Wagner (1987), FTR 35."],
  ["Crown fire", "Van Wagner (1977), Canadian Journal of Forest Research 7: 23-34."],
  ["FBP layer verification", "Matches the cffdrs reference implementation (Wang et al. 2017) to floating-point precision."],
  ["Grid spread model", "Level-set front with the FBP ellipse (Richards 1990; Lautenberger 2013); Huygens wavelets on uniform fuel (Tymstra et al. 2010, Prometheus)."],
  ["Ember spotting (illustrative)", "Maximum distance: Albini (1979, 1981, 1983), Chase (1981, 1984). Emission, probability and landing are heuristic."],
  ["D-2 aspen", "No spread below BUI 80: Alexander (2010), as cited by cffdrs (full citation unverified)."],
  ["Hourly FFMC and spin-up", "Van Wagner (1977) PS-X-69; diurnal FFMC, Lawson, Armitage & Hoskins (1996), FRDA Report 245."],
  ["Head fire intensity classes", `${HFI_CLASS_SOURCE}; equipment after Alexander (2001).`],
  ["FWI classes", "CWFIS national FWI map intervals. An FWI map class, not an official fire danger rating."],
  ["Building exposure", "Cohen (2004) radiant flux from a worst-case flame; exposure, not ignition probability."],
  ["House-to-house spread (illustrative)", "Hamada building-to-building rates (via Himoto & Tanaka 2008; Purnomo et al. 2024, 2026; Qin et al. 2026). Not validated in Canada."],
  ["Validation protocol", "Bennett, Jain, Moore & Boisvert (2026), International Journal of Wildland Fire; Canadian Fire Spread Dataset (Barber et al. 2024)."],
];

export default function AboutPanel({ section = null }: { section?: AboutSectionId | null }) {
  const [version, setVersion] = useState<string>("loading…");
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    getVersion()
      .then((v) => alive && setVersion(`${v.version} · ${v.git_sha}`))
      .catch(() => alive && setVersion("not available (the API did not answer)"));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!section) return;
    const el = rootRef.current?.querySelector<HTMLElement>(`#${section}`);
    el?.scrollIntoView({ block: "start" });
    el?.focus({ preventScroll: true });
  }, [section]);

  return (
    <div className="about-panel" ref={rootRef} data-testid="about-panel">
      <div className="about-inner">
        <header className="about-header">
          <h2>About &amp; sources</h2>
          <nav aria-label="About sections" className="about-toc">
            <ol>
              {ABOUT_SECTIONS.map(([id, title]) => (
                <li key={id}><a href={`#${id}`} onClick={(e) => {
                  e.preventDefault();
                  const el = rootRef.current?.querySelector<HTMLElement>(`#${id}`);
                  el?.scrollIntoView({ block: "start", behavior: "smooth" });
                  el?.focus({ preventScroll: true });
                }}>{title}</a></li>
              ))}
            </ol>
          </nav>
        </header>

        <Section id="about-use" title="1. What FireSim is for">
          <p>
            FireSim models wildfire growth and fire behaviour with the Canadian Forest Fire Behavior
            Prediction (FBP) System for the EOC Planning section: preparedness, training, tabletop
            exercises and what-if planning. It is <strong>not an operational forecast</strong> and does
            not replace the lead agency's prediction.
          </p>
          <ul>
            <li>Evacuation status is set by Planning. FireSim never recommends Order, Alert or Watch; it shows modelled fire arrival and the statuses Planning has set.</li>
            <li>Asset, road and neighbourhood arrival times are model output, not instructions.</li>
            <li>A single run is not the expected fire; with the range of outcomes on, the P10 line is a planning margin, not a worst case.</li>
          </ul>
          <Docs links={[["Model card: intended use", doc("model-card.md", "intended-use")], ["Out-of-scope uses", doc("model-card.md", "out-of-scope-uses")], ["Project record §1", doc("PROJECT_RECORD.md", "1-purpose-and-audience")]]} />
        </Section>

        <Section id="about-limits" title="2. Limits and validation">
          <p>
            <span className="ui-badge ui-badge-warn">{BADGES.lowSkill}</span>{" "}
            The FBP equations match the cffdrs reference implementation and spread was compared with
            WISE. A first validation on 143 Alberta fire-days showed low one-day skill (F1 about
            0.15–0.24) and over-predicted growth on most days, similar to WISE with default settings.
          </p>
          <ul>
            <li><strong>Burning period and spin-up.</strong> {TIPS.burningPeriod} {TIPS.spinUp}</li>
            <li><strong>RPAS active edges.</strong> {TIPS.activeEdges}</li>
            <li><strong>Range of outcomes (ensemble).</strong> {TIPS.rangeTooNarrow}</li>
            <li><strong>P10, P50, P90.</strong> {TIPS.p10} {TIPS.p50} {TIPS.p90}</li>
            <li><strong>Single run.</strong> {TIPS.singleRun}</li>
          </ul>
          <Docs links={[["Validation (docs/validation.md)", doc("validation.md")], ["Ensemble calibration", doc("validation.md", "ensemble-calibration-third-round-2026-10-08")], ["What this means for EOC use", doc("validation.md", "what-this-means-for-eoc-use")], ["Model card: evidence and limits", doc("model-card.md", "known-limitations-and-biases")]]} />
        </Section>

        <Section id="about-methods" title="3. Methods and references">
          <dl className="about-refs">
            {REFERENCES.map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
          <p>
            The full numbered reference list, with what was verified against the original and what is
            cited second-hand, is in the project record. The reference PDFs are kept in the owner's
            Zotero collection (not in the repository).
          </p>
          <Docs links={[["Project record §4.1 method → source", doc("PROJECT_RECORD.md", "41-method--source")], ["§4.2 references", doc("PROJECT_RECORD.md", "42-references")], ["FBP reference", doc("fbp-reference.md")], ["Verification", doc("verification.md")], ["Building exposure", doc("building-exposure.md")], ["Structure spread spec", doc("structure-spread-spec.md")]]} />
        </Section>

        <Section id="about-data" title="4. Data sources and licences">
          <ul>
            <li><strong>Edmonton fuel grid.</strong> {TIPS.edmontonGrid("C-2 (or the fuel type chosen in Setup)")} Built from the City of Edmonton's 2025 LiDAR acquisition (flown 29-30 June 2025; published on the City's open data portal as surface and bare-earth rasters; the build used a vegetation-classified point cloud of the same flight supplied by the City), the Urban Primary Land and Vegetation Inventory 2025, Naturalized Areas, and a conifer/deciduous crown classifier trained on the City's Trees inventory. Contains information licensed under the Open Government Licence – Edmonton.</li>
            <li><strong>Alberta Wildfire FBP fuel types 2025, 100 m.</strong> {TIPS.albertaGrid} Data use is governed by the Open Government Licence – Alberta; the image service carries the Government of Alberta disclaimer (provided "as is", no warranty).</li>
            <li><strong>Terrain.</strong> Edmonton: 30 m DEM, UTM 12N (file dated 2011; recorded as Open Government Canada, likely NRCan CDEM, unverified). Elsewhere in Alberta: NRCan Medium Resolution DEM (MRDEM-30) averaged to about 100 m; contains information licensed under the Open Government Licence – Canada.</li>
            <li><strong>Extra water mask.</strong> {TIPS.waterMask} © OpenStreetMap contributors (ODbL).</li>
            <li><strong>Buildings.</strong> {TIPS.buildings} Municipal footprints via Statistics Canada's Open Database of Buildings v3 (2025): contains information licensed under the Open Government Licence – City of Calgary; the City of Lethbridge – Open Data License; the Open Data License – City of Grande Prairie; the Open Data Licence – City of Airdrie; the Open Government Licence – Town of Okotoks; the Open Data Licence – Town of Cochrane; the Town of Canmore Open Data Licence; the Open Government Licence – Banff; and the Open Government License – Parkland County.</li>
            <li><strong>Neighbourhoods.</strong> City of Edmonton Open Data, Neighbourhoods (Open Government Licence – City of Edmonton), 407 polygons. Elsewhere in Alberta: Statistics Canada 2021 census subdivisions (Statistics Canada Open Licence), tagged "CSD", and the hamlets and localities inside them, named like "Calahoo (hamlet, Sturgeon County)": Government of Alberta hamlet boundaries and urban service areas (Open Government Licence – Alberta), Statistics Canada 2021 designated places, and Canadian Geographical Names Database localities (Open Government Licence – Canada; a point counts as a 250 m disc). A county is named only where the fire crosses into it away from any of its hamlets or localities.</li>
            <li><strong>WUI zone modifiers</strong> <span className="ui-badge ui-badge-warn">{BADGES.unsourced}</span>: {TIPS.wui}</li>
            <li><strong>Critical assets and major roads.</strong> City of Edmonton Open Data (Open Government Licence – City of Edmonton) · Government of Alberta continuing care list (Open Government Licence – Alberta) · Statistics Canada ODHF (Open Government Licence – Canada) · © OpenStreetMap contributors (ODbL). Care facilities found only in OpenStreetMap are marked "verify": they are in no current official list. Outside Edmonton: Statistics Canada ODHF (2020) and OpenStreetMap only, served for the area around the ignition; coverage depends on OpenStreetMap mapping.</li>
            <li><strong>Weather.</strong> Current FWI codes from the CWFIS station network (Natural Resources Canada); hourly forecast from Open-Meteo.</li>
            <li><strong>Base maps.</strong> © OpenStreetMap contributors; OpenTopoMap. The map attribution names the sources of the layers shown.</li>
          </ul>
          <Docs links={[["Data sources (docs/data-sources.md)", doc("data-sources.md")], ["Project record §4.3", doc("PROJECT_RECORD.md", "43-data-sources")]]} />
        </Section>

        <Section id="about-responsible" title="5. Responsible use">
          <ul>
            <li><strong>RPAS.</strong> {RPAS_NOTE} FireSim gives no stand-off distances.</li>
            <li><strong>Head fire intensity classes</strong> <span className="ui-badge ui-badge-info">{BADGES.c2Generalisation}</span>: {HFI_CLASS_CAVEAT}</li>
            <li><strong>Building exposure</strong> <span className="ui-badge ui-badge-info">{BADGES.exposureNotIgnition}</span>: {TIPS.exposure}</li>
            <li><strong>House-to-house spread</strong> <span className="ui-badge ui-badge-warn">{BADGES.illustrative}</span>: modelled involvement, not a prediction of which buildings will burn; not validated in Canada. Map display only, never exported.</li>
            <li>Outputs shared beyond Planning should state who ran them, when, the model version (below) and the inputs.</li>
          </ul>
          <Docs links={[["Model card: responsible use", doc("model-card.md", "responsible-use")]]} />
        </Section>

        <Section id="about-forms" title="6. ICS Canada forms">
          <p>
            The EOC Console pre-fills the ICS Canada Form 209-WF (Incident Status Summary, May 2021) and
            an IAP package (201–206, 214) from the current run. Blocks the model fills are tagged
            MODEL OUTPUT, scenario inputs SCENARIO INPUT; the tags stay on printed forms. Observed
            status and observed fire behaviour are left for the user to enter. Projections at
            12/24/48/72 h use the ensemble (P50, P10) when it ran, in clock time, with the run ID and
            model version.
          </p>
          <Docs links={[["ICS Canada 209-WF (docs/ics-canada-209.md)", doc("ics-canada-209.md")]]} />
        </Section>

        <Section id="about-help" title="7. Using the app">
          <ul>
            <li><strong>Ignition.</strong> {TIPS.ignitionHowTo}</li>
            <li><strong>Coordinates.</strong> {TIPS.coords}</li>
            <li><strong>Start time.</strong> {TIPS.startNow} {TIPS.hindcast}</li>
            <li><strong>Explanations.</strong> Hover, focus or tap a badge or an "i" button to read it; Esc closes it.</li>
            <li><strong>Your own asset layer.</strong> {TIPS.ownLayer}</li>
            <li><strong>Multi-day runs.</strong> {TIPS.multiDay}</li>
            <li><strong>Setup after a reload.</strong> The ignition, start time, weather (and where it came from), grass curing and run options are kept in this browser for the open incident (or without one) and come back after a reload. When FireSim fills the weather from a station for a new ignition, it says what it replaced; Undo puts the earlier values back. The run on screen comes back too, in the same browser tab: a run still computing is followed to the end, and a run the server no longer has is said so.</li>
            <li><strong>ICS forms.</strong> Objectives, command emphasis, strategy and resources are left for Planning, the IC and Operations to enter. FireSim adds model context only (modelled area, head fire intensity class), labelled as model output.</li>
            <li><strong>RPAS perimeter restart.</strong> After a run, open Setup → Observed perimeter (RPAS): load an observed perimeter (or use the modelled one), mark the active edges on the map or by side, and restart at the timeline's selected time. The restart uses Setup's current weather, FWI codes and weather changes, runs the full Predict time and keeps earlier arrival times. {TIPS.bufferBlank}</li>
            <li><strong>Weather changes during a run.</strong> Setup → Weather & FWI → Add a weather change: step changes of wind, temperature, RH and rain at set times (e.g. 47 km/h, then 20 km/h from 20:00). FFMC and ISI are recomputed hourly from the starting codes.</li>
            <li><strong>Fuel-grid year.</strong> The fuel grids represent 2025. A scenario dated earlier shows a warning: areas burned since may be mapped as grass or non-fuel, so historical replays overstate spread in old burn scars.</li>
            <li><strong>Saved runs.</strong> Finished runs are kept on the server for a year. Setup → Saved runs lists them behind a shared passphrase (asked once per browser tab, not stored after the tab closes): time, label, ignition point, kind, area, duration and fuel source; filter by the map view or dates; click a run to reload it with its timeline, perimeter, exposure and range of outcomes. Name a run before it starts with "Run label", or rename it later. Deleting a run removes it for everyone.</li>
          </ul>
          <Docs links={[["Frontend README", `${REPO_URL}/blob/master/frontend/README.md`]]} />
        </Section>

        <Section id="about-version" title="8. Version">
          <p data-testid="about-version">Model version: {version}</p>
          <Docs links={[["Decisions log", doc("PROJECT_RECORD.md", "3-decisions-log")], ["Work log", doc("PROJECT_RECORD.md", "7-work-log")], ["Repository", REPO_URL]]} />
        </Section>
      </div>
    </div>
  );
}
