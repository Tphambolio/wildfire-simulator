# AIMS Console

**All-Hazards Incident Management System** — a browser-based EOC command console built on NIMS/ICS standards.

**Live:** [aims-console.vercel.app](https://aims-console.vercel.app)

---

## What it does

AIMS Console supports full Incident Command System (ICS) workflow from initial briefing through IAP approval across multi-day operational periods:

- **All-hazards** — wildfire, flood, hazmat, earthquake, mass casualty, infrastructure failure, search & rescue, severe weather
- **Multi-day operational periods** — each period gets its own IAP approval slate; advance the clock and start fresh
- **IAP Forms Dashboard** — per-period form status tracking (empty → draft → complete → approved); IC signs off individual forms and the full IAP package
- **16 ICS forms** — ICS-201 through ICS-215A including ICS-205A (Comms List), ICS-209 (Status Summary), ICS-211 (Check-In List), ICS-213RR (Resource Request)
- **Resource requests** — create ICS-213RR requests per period; track through pending → ordered → filled
- **Section workspaces** — Command, Operations, Planning, Logistics, Finance; roster management per section
- **Common Operating Picture** — MapLibre GL map with ICS annotation symbols, hazard zone drawing, OSM facility fetch
- **Cloud sync** — share incident via a 6-character code; collaborators join via URL parameter
- **LocalStorage persistence** — incidents survive page reload; export/import as JSON

---

## ICS Forms

| Form | Title | Level |
|------|-------|-------|
| ICS-201 | Incident Briefing | Incident |
| ICS-202 | Incident Objectives | Per period |
| ICS-203 | Organization Assignment | Per period |
| ICS-204 | Assignment List | Per period |
| ICS-205 | Radio Comms Plan | Per period |
| ICS-205A | Communications List | Per period |
| ICS-206 | Medical Plan | Per period |
| ICS-207 | Org Chart | Per period |
| ICS-208 | Safety Message/Plan | Per period |
| ICS-209 | Incident Status Summary | Per period |
| ICS-211 | Check-In/Sign-In List | Per period |
| ICS-213 | General Message | Per period |
| ICS-213RR | Resource Request | Per request |
| ICS-214 | Activity Log | Per period |
| ICS-215 | Operational Planning Worksheet | Per period |
| ICS-215A | Safety Analysis | Per period |

Forms are fully editable HTML documents rendered in-browser. All edits are saved to the incident and survive page reload.

---

## IAP Approval Workflow

Each operational period tracks form status independently:

```
empty → draft → complete → approved
                         ↘ rejected → complete → approved
```

- Opening a form auto-transitions it from **empty → draft**
- Section chiefs mark forms **complete** when ready
- The IC **approves** individual forms with an inline signature confirm
- **Approve Full IAP** button is gated until all required forms are approved for the period
- Day 2+ shows a **↩ Carry from Period 1** link on empty forms that were approved the previous period

---

## Supported Hazard Types

`wildfire` · `flood` · `hazmat` · `earthquake` · `mass_casualty` · `infrastructure` · `search_rescue` · `severe_weather`

Each hazard type defines which ICS forms are **required** — the IAP dashboard highlights required forms and gates full-IAP approval accordingly.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + TypeScript 5.9 + Vite 7 |
| Map | MapLibre GL 5 |
| State | React hooks + localStorage (no external store) |
| Deploy | Vercel (frontend-only, no backend required) |

---

## Local development

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
npm run build     # production bundle (must be zero TS errors)
```

---

## Project structure

```
frontend/
  src/
    components/
      EOCConsole.tsx          Main tabbed console
      IAPDashboard.tsx        Per-period IAP forms dashboard
      SectionWorkspace.tsx    Per-section roster + forms
      MapView.tsx             MapLibre GL COP
      OperationalPeriodPanel  Multi-day period switcher
      InitBriefingPanel       ICS-201 initial briefing flow
      ...
    hooks/
      useIncident.ts          All incident state + cloud sync
    types/
      incident.ts             Core types — IncidentSession, OperationalPeriod,
                              ICSFormId, FormRecord, ResourceRequest, HAZARD_DEFS
    utils/
      icsForms.ts             All 16 ICS form HTML generators
    index.css                 All styles (dark EOC theme)
```

---

## Cloud sync

Incidents can be shared with a 6-character code generated on demand:

```
☁ Share Incident → copies a URL like https://aims-console.vercel.app?incident=ABC123
```

Anyone with the link joins the incident in read/edit mode. Changes sync automatically on a 2-second debounce via a lightweight cloud store.
