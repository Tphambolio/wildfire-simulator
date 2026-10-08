# ICS Canada 209-WF situation report

The EOC console and the Situation panel's **ICS 209-WF** button open an Incident Status Summary
laid out as **ICS Canada Form 209-WF** (wildfire), pre-filled with the run's model output
(`frontend/src/utils/ics209.ts`). It replaced a NIMS ICS-209 (Rev. 2021-08) layout on
2026-10-08 (owner decision: ICS Canada, not NIMS).

## Source

- ICS Canada, *Incident Status Summary (ICS 209)*, ICS Form 209-WF, 4 pages, PDF created
  2021-05-15: <https://icscanada.ca/wp-content/uploads/2023/07/Form-209-wf.pdf> (listed at
  <https://icscanada.ca/resources/ics-forms/>). Block numbers and titles were checked against
  this PDF on 2026-10-08.
- ICS Canada, *ICS 209 Instructions* (PDF dated 2023-09-12):
  <https://icscanada.ca/wp-content/uploads/2023/11/209-Instructions.pdf>. Written for the
  all-hazards Form 209, whose block numbers are the same; 209-WF differs in block 9 (Status
  OC/BH/UC/O instead of incident definition), 28 (observed fire behaviour) and 29 (primary FBP
  fuel type). Block 27 guidance: say whether and how geospatial data is attached, its format
  (e.g. shp, kml, kmz, gml), content and projection, and keep files small.

Not verified: the expansions of the block 9 codes (the form prints only "OC BH UC O"; Out of
Control / Being Held / Under Control / Out is Canadian stage-of-control usage), and whether
Alberta agencies file the 209-WF in practice.

## Who fills what

Every block carries a tag: **MODEL OUTPUT** (FireSim, from the run), **SCENARIO INPUT** (what
the user entered to start the run) or **ENTER** (the incident fills it; editable in the page
before printing).

| Block | Content | Filled by |
|---|---|---|
| 6 | Incident start date/time: the run's start time | Scenario input |
| 7 | Size: modelled area at the end of the period (single run; ensemble P50 beside it) | Model output |
| 9 | Status OC / BH / UC / O | **User only, never the model** |
| 11 | For time period: run start to run end, clock time (America/Edmonton) | Scenario input |
| 22, 23, 26 | Ignition point lng/lat, WGS 84, UTM (Snyder 1987 formulas, checked against pyproj) | Scenario input |
| 27 | Geospatial data: FireSim's GeoJSON/KML exports, format, content time, run ID, "model output" label | Model output |
| 28 | Observed fire behaviour or significant events | **User only, never the model** |
| 29 | FBP fuel types in the modelled burned area (shares) | Model output |
| 30B | Structures threatened (72 h): building footprints within 100 m of a burned cell by 72 h or the end of the run, single run (exposure, not damage; `docs/building-exposure.md`). FireSim does not classify structures, so rows E-H and columns C/D are left to the user | Model output |
| 35 | Weather and FWI inputs of the run (not a forecast) | Scenario input |
| 36 | Area at 12/24/48/72 h in clock time: ensemble P50 and P10 (worst-credible) where available, and the single run; horizons beyond the run say "not projected"; runs shorter than a horizon also report the end of the modelled period | Model output |
| 38 | Threats at the same horizons: structures within 100 m, critical assets within 500 m and major roads reached (P10 · single run) | Model output |
| 42 | Area at the end of the modelled period (ensemble P50 and member range, single run), stated as *not* a final size | Model output |
| All others | 1-5, 8, 10, 12-21, 24, 25, 31-34, 37, 39-41, 43-53 | User |

Attachment A (after block 53) holds the modelled fire behaviour (peak ROS, HFI, fire type,
flame length, spotting), the Monte Carlo burn-probability areas and the head fire intensity
class interpretation, all labelled as model output. Evacuation status set by Planning is
listed under block 33 as entered.

Every page is stamped with the **run ID** (API simulation ID), the **model version** and git
SHA from `GET /api/v1/version` (or "unknown" if it cannot be reached within 3 s), the generation
time and whether an ensemble was used.

The removed NIMS layout also derived an "incident complexity" type from head fire intensity;
that mapping had no source and block 10 is now left to the user.

## Other ICS forms

`frontend/src/utils/icsForms.ts` generates 201, 202, 203, 204, 205, 206 and 214. All have
ICS Canada counterparts (Forms 201, 202, 203, 204-WF/204-AH, 205, 206, 214 at
icscanada.ca/resources/ics-forms/). Their layouts are FireSim adaptations with lettered
sections, not block-by-block reproductions of the ICS Canada forms; transcribe onto the
official forms for formal use.

## Tests

`frontend/src/utils/ics209.test.ts` (UTM against pyproj, horizons, P50/P10 areas, blocks 9 and
28 never filled, model blocks labelled, run stamp) and `frontend/tests/e2e/ics209.spec.ts`
(opens the report from the Situation panel after an ensemble run and checks the stamp,
projections and empty observed blocks).
