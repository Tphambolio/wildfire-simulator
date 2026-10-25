# SEALED_2026: a sealed 2026-season held-out fire set (backlog C4)

- **Registered:** 2026-10-24, branch `feat/c4-sealed-2026` (from `master` `6dcc41d`). The rules in §1-§5
  are committed and pushed **before** the draw script is run and before any 2026 fire record is opened.
  The draw result (§6) and the use ledger (§8) are appended below them; the text above §6 is not edited
  after registration (check with `git diff <rules commit> HEAD -- docs/SEALED_2026.md`, which may only add
  lines).
- **Owner approval:** Travis Kennedy, 2026-10-24 (backlog C4, pool choice): seal a **US 2026 block now**
  from NIFC WFIGS open interagency perimeters; add a **Canadian block from NBAC 2026 later**, when it
  publishes, sealed the same way.
- **Why:** Camp, Eaton, Palisades, Tubbs, Woolsey, Dixie and Caldor are spent for deciding; the Alberta
  harness held-out fire-days have been scored repeatedly ([R62], [R63]). FireSim needs fires no model,
  no agent and no setting choice has touched.
- **First uses (planned, each needs its own pre-registration):** confirming the 1-burn-day active-edge
  rule from PR #101 [R63] on fresh fire-days; re-scoring FireSim at each release (C3).
- **Nothing here runs a model.** Sealing uses perimeter attributes and geometry only. No FireSim run, no
  comparator run and no look at VIIRS progression, weather or growth of a sealed fire is part of this work.

## 1. Source (US block)

| Item | Value |
|---|---|
| Service | NIFC WFIGS **2026 Interagency Fire Perimeters to Date** (ArcGIS item `7c81ab78d8464e5c9771e49b64e834e9`) |
| Layer | `https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_YearToDate/FeatureServer/0` |
| Scope (from the item description) | IRWIN records categorised Wildfire (WF) or Prescribed Fire (RX), fire discovery date in 2026, valid and not quarantined, source polygon Public / Approved / Visible; no fall-off rules; refreshed every 5 min |
| Query (one request, paged) | `where=attr_IncidentTypeCategory='WF' AND poly_GISAcres>=12355.3`, `outFields=*`, `outSR=4326`, `f=geojson`, `resultOffset` paging |
| Licence / terms | No explicit licence; NIFC disclaimer (no warranty, "data are dynamic and may change over time"). US federal interagency open data, treated as public domain as in PREREG_LR8 §2. Access information: DOI Office of Wildland Fire (IRWIN), USDA Forest Service, NPS, FWS, BIA, BLM, NASF, USFA, NWCG. |
| Download time | recorded by the draw script in `_about.json` and §6 (UTC) |
| Revision caveat | **Perimeters may still be revised** after the download (the service is live; 2026 perimeters are not certified, and MTBS 2026 has not published). The seal fixes the **list of fires** (keys and IRWIN IDs) and the downloaded snapshot (sha256). A later pre-registration states whether it scores against the sealed snapshot or a re-download, and reports any changed perimeter. |

The all-years layer `WFIGS_Interagency_Perimeters` holds the same 2026 records; the year-to-date layer is
used because its scope is defined by the 2026 discovery year. MTBS 2026 and CAL FIRE FRAP 2026 are not
published at registration (they normally follow 1-2 years later); they are not used to draw.

## 2. Eligibility (US block, applied in this order; every rule uses attributes or geometry only)

1. **Wildfire:** `attr_IncidentTypeCategory = 'WF'` (prescribed fires excluded).
2. **Region:** `attr_POOState` in the 11 western states of PREREG_LR8 (AZ, CA, CO, ID, MT, NV, NM, OR, UT,
   WA, WY). Alaska and Hawaii are excluded (LANDFIRE CONUS fuels; same pool as v8).
3. **Size:** final perimeter `poly_GISAcres` ≥ 12,355.3 acres (5,000 ha), the PREREG_LR8 pool threshold.
4. **Discovered in 2026:** `attr_FireDiscoveryDateTime` non-null and in 2026 (UTC).
5. **Complete:** a containment, control or out time is recorded (`attr_ContainmentDateTime`,
   `attr_ControlDateTime` or `attr_FireOutDateTime` non-null) or `attr_PercentContained` ≥ 100; and the
   earliest recorded of containment / control / out is **≤ download time − 7 days** (so ERA5 hourly
   weather, about 5 days behind real time, covers the whole record window). Fires still active at
   download are not in the block and are **not added later**: the US block is closed at sealing.
6. **Known start:** `attr_InitialLatitude` and `attr_InitialLongitude` non-null, and the initial point lies
   within 2 km of the final perimeter (otherwise the start is unknown or miscoded).
7. **Single incident:** one fire per IRWIN ID (`attr_IrwinID`, else `poly_IRWINID`, normalised to lower case
   without braces; the largest polygon kept). Records whose `attr_IncidentName` contains `COMPLEX` are
   excluded (several ignitions, no single start). If two remaining perimeters overlap by more than 50 % of
   the smaller one's area (EPSG:5070), only the larger is kept (duplicate shapes of one fire).
8. **Never used in the record:** a fire is excluded if its IRWIN ID or `attr_UniqueFireIdentifier` appears,
   case-insensitive, in any text file (`.md .txt .csv .json .geojson .py .tsv .yaml .yml`, ≤ 50 MB) under
   `~/dev/wildfire/reports/`, `~/dev/wildfire/validation-data/` (except `sealed2026/`) or the repository's
   `docs/`; or if the phrase "`<IncidentName> Fire`" (case-insensitive, word-bounded) appears in any such
   file. The name rule is deliberately conservative: a 2026 fire that shares a name with any fire already
   discussed is dropped, so no record entry can be confused with a sealed fire. The spent fires named
   above (Camp, Eaton, Palisades, Tubbs, Woolsey, Dixie, Caldor) and every v8 pool fire are 2018-2025 and
   cannot enter a 2026 block.

**What is not checked at sealing, and why.** FireSim's harness needs a start (rule 6), daily progression
and hourly weather on a pre-fire fuel grid. Pre-fire fuels (LANDFIRE CONUS, latest vintage before 2026) and
ERA5 hourly weather exist for every fire passing rules 2 and 5. Daily progression for 2026 US fires will
come from VIIRS 375 m (FIRMS yearly 2026 archives, published after the year ends; or NRT), because no
CFSDS-type product covers 2026. Looking at a fire's hotspots, growth or weather would be looking at its
outcome, so **fire-day eligibility (VIIRS growth on the day, minimum growth, station coverage, run-time
caps) is set by each future pre-registration and applied blind to model output**. A fire with no usable
fire-day under that pre-registration contributes nothing and is listed.

## 3. Draw (US block)

- **Seed:** `S_SEAL_US = 2026102401`.
- **Order:** eligible fire keys (`WFIGS:<attr_UniqueFireIdentifier>`) sorted ascending, then permuted with
  `numpy.random.default_rng(S_SEAL_US).permutation`; `order` = position in the permutation (1-based).
- **Every eligible fire is sealed** (no sub-sampling; the 2026 western-US pool is small).
- **Two subsets, fixed at sealing:**
  - **`confirm`** = orders 1 … ceil(n/2). For one-shot **deciding** tests only (first: the 1-burn-day
    active-edge rule, PR #101 [R63]). A deciding pre-registration names the subset (or a contiguous prefix
    of it in `order`), is committed before any run, and the fires it uses are then spent for deciding.
  - **`scorecard`** = the remaining orders. For the **per-release re-score** (C3): descriptive, re-run at
    each tagged release, **never decides** a method choice (it is seen every release).
- **Script:** `scripts/validation/sealed2026/draw.py` (this branch; committed before it is run). It prints
  counts only, never fire names.

## 4. Where the list lives (sealed)

- `~/dev/wildfire/validation-data/sealed2026/` (outside the repository), with a README saying **do not
  open before a pre-registration names the subset**:
  - `us/sealed_us_2026.csv`: the list (order, subset, fire key, IRWIN ID, state, discovery date, acres);
    its sha256 is the seal.
  - `us/sealed_us_2026_perimeters.geojson`: the perimeters of the sealed fires as downloaded (sha256).
  - `raw/wfigs_ytd_2026_ge5000ha.geojson`: the full query result (every state, before rules 2-8; sha256).
  - `us/eligibility_log.csv`: every pool record with the rule that removed it (pool members removed by a
    rule are not sealed and may be read).
  - `_about.json`, `README.md`.
- `docs/PROJECT_RECORD.md` holds only the sha256, count, seed and a pointer to these rules.
- "Opening" a sealed fire means running any model on it, or viewing its VIIRS progression, weather or
  growth. Reading the CSV row (name, date, acres) to set up an approved pre-registration is not opening.

## 5. Canadian block (added later, sealed the same way; rules fixed now)

- **Status at registration (checked 2026-10-24):** **NBAC 2026 does not exist yet.** The latest NBAC
  release is `20260513` (files `NBAC_<year>_20260513.zip` for 1972-2025, posted 2026-05-22 at
  `https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/`), so NBAC 2026 is expected about May 2027. NFDB polygons
  (`nfdb/fire_poly/current_version/NFDB_poly.zip`, 2026-01-28) do not hold 2026. CWFIS does publish
  **provisional 2026 FireM3 perimeters** (`downloads/hotspots/perimeters.shp`, modified 2026-10-24, and
  `progression.shp`): these are buffered-hotspot estimates refreshed daily, not mapped burned area; they are
  **not used to draw** (they would also show each fire's progression, i.e. its outcome). The 2025 archive
  (`hotspots/archive/2025_perimeters.zip`) appeared on 2026-04-01, so a 2026 archive is expected in spring 2027.
  Licence for all CWFIS/NBAC data: Open Government Licence - Canada.
- **Trigger:** the first NBAC release that contains a 2026 file. The agent who finds it appends a §6b draw
  here; no rule below changes.
- **Rules:** NBAC 2026 rows grouped by `NFIREID`/GID (rows of one GID summed, as PREREG_LR8 §3); not
  prescribed burns; the largest row's agency in AB, BC, SK, MB, NT or YT (Parks Canada and DND rows
  excluded); summed area ≥ 5,000 ha; an NBAC start date (earliest HS/AG start) recorded; rule 8 above
  (IDs, and "`<name> Fire`" or the NBAC fire ID in the record); fire-day eligibility left to each
  pre-registration, as for the US block. Alberta fires also used by any Alberta harness built after this
  date are excluded by rule 8 automatically.
- **Seed:** `S_SEAL_CA = 2026102402`, same order and the same confirm/scorecard split (ceil(n/2)).
- **Output:** `validation-data/sealed2026/ca/sealed_ca_2026.csv` and perimeters, sha256 appended to §6 and
  to PROJECT_RECORD; committed and pushed before any model is run.

---

## 6. Draw result (appended after the run; nothing above was edited)

- **Rules commit:** `5b43907a5c1a7d5c67a2a13b585cb318cfb61647` (this file, §1-§5; sha256 at that commit
  `34dcce1399709537fb7dad728f00f7c26f813de8a47faf062411b6a1935b3c4d`), pushed before the script existed.
- **Script commit:** `0c31f17cad28ebc827ac4d5d3177ab27a039d9e7`, `draw.py` sha256
  `dab74ee6143a162037518b638e36bc30ddb7327a7a21e214c509bb2d71bc630d`, pushed before it was run; run once,
  under `capjob`, with no error.
- **Downloaded:** 2026-10-24T19:04:51Z (layer last edited 2026-10-24 18:52 UTC). 110 records matched the query.
- **Removed:** outside the 11 states 25; no containment/control/out time 24; not complete 17; initial point
  more than 2 km from the final perimeter 12 (2.3-59 km; miscoded or approximate starts); prescribed, under
  5,000 ha, not 2026, duplicate IRWIN, complex, overlapping duplicate and **used in the record: 0**
  (5,379 text files scanned under `reports/`, `validation-data/` and `docs/`).
- **Sealed: 32 fires** (CA 2, CO 2, ID 2, NM 1, NV 8, OR 6, UT 4, WA 6, WY 1); **confirm 16** (orders 1-16),
  **scorecard 16** (orders 17-32). Seed `S_SEAL_US = 2026102401`.

| Sealed file (`~/dev/wildfire/validation-data/sealed2026/`) | sha256 |
|---|---|
| **`us/sealed_us_2026.csv` (the seal)** | **`b9c7a303c71e63ebc63e9fdf25cda18e8ebcaf61a5af36a3ac69cdd297748e80`** |
| `us/sealed_us_2026_perimeters.geojson` | `7f0c62a2c482a116f130fb30b3104ad455e41f736e30d736c5fd24c300f7ab74` |
| `raw/wfigs_ytd_2026_ge5000ha.geojson` | `9d04ba84696b5af48efac2794c8eb1f741f22e603b9e780a89962b1149140c19` |
| `us/eligibility_log.csv` | `4ae2a663808b599e918f9fffcdfae3f489f12b995ee07f2480b542064229c22b` |

**Disclosed reading of rule 5.** Rule 5 accepts "complete" by a recorded time *or* `PercentContained` ≥ 100,
but its 7-day clause needs a recorded time. The script (committed before the run) read a record with
100 % containment and no containment/control/out time as failing the 7-day clause (end time unknown), so
it removed it. **24 records were removed this way, all at 100 % contained.** This is the conservative
reading; the other reading would have admitted them. They were not checked against rules 6-8 and are
**left unopened**: the owner may choose to seal them as a supplementary US block (same rules 6-8, seed
`2026102403`, appended here as §6a before any use). Until then the sealed set is the 32 above.

Nothing was run on any of these fires. No hotspot, weather or growth record of a sealed fire was opened.
The 12 rule-6 removals were checked after the draw (initial-point distances, removed records only) to rule
out a coding error.

### 6a. Supplementary US block: the 24 records removed under the conservative reading of rule 5 (rules fixed 2026-10-25; result appended below after the run)

**Owner decision** (Travis Kennedy, 2026-10-25, decision D2 of the ML ember-cast review): seal the 24 records
of §6 that were removed as `rule5_no_end_time` (100 % contained, no containment/control/out time) as a
**scale-only block**: they add fires to any 2026 test that names them, and **never decide anything**, like the
`scorecard` subset. Nothing in §1-§6 changes.

- **Pool:** the §6 raw snapshot `raw/wfigs_ytd_2026_ge5000ha.geojson` (sha256 `9d04ba84…40c19`, downloaded
  2026-10-24T19:04:51Z). **No re-download**: the block is defined on the same pool as the seal, so the two
  blocks cannot overlap or disagree about a record's attributes.
- **Candidates:** exactly the records that pass rules 1-4 and were removed only by the rule-5 clause "no end
  time" (100 % contained, `end` missing). The script recomputes this from the snapshot; it does not read the
  sealed list or the eligibility log.
- **Rule 5 reading (disclosed):** 100 % containment recorded by the snapshot date is taken as "complete";
  the 7-day lag cannot be shown and is **not** required for a scale-only block. Each pre-registration that
  uses the block states the end of its fire-day window from its own progression source (VIIRS), never from
  a WFIGS time, and reports results on this block **separately** from the §6 `confirm` subset.
- **Rules 6-8** applied as written in §2 (initial point within 2 km of the final perimeter; one record per
  IRWIN ID, no complexes, no overlapping duplicates, checked against the 32 sealed fires as well as within
  the block; never used in the record, same text scan).
- **Order:** sorted fire keys permuted with `numpy.random.default_rng(S_SEAL_US_SUPP)`,
  **`S_SEAL_US_SUPP = 2026102403`**. Subset label `scale` for every record (no `confirm` orders).
- **Output:** `us/sealed_us_2026_supp.csv` (the seal), `us/sealed_us_2026_supp_perimeters.geojson`,
  `us/supp_eligibility_log.csv`, `us/supp_draw_summary.json` (counts and hashes only). The script prints
  counts and hashes, never fire names, and refuses to overwrite an existing file.
- **Script:** `scripts/validation/sealed2026/draw_supplement.py`, committed and pushed after this text and
  before it is run; its sha256 and the result go below.

---

### 6b. Canadian block draw result

(appended by the agent who finds the first NBAC release holding 2026; see §5)

## 7. How to use the set

1. A pre-registration (committed and pushed before any run) names the subset (`confirm`, or a contiguous
   prefix of it in `order`; or `scorecard` for a release re-score), the fire-day rules (VIIRS progression
   source, minimum growth, weather source, run-time caps) and whether perimeters come from the sealed
   snapshot or a re-download. It checks the list's sha256 against §6 before reading it.
2. It appends one row to the ledger in §8 when it opens the subset.
3. `confirm` fires used by a deciding test are spent for deciding afterwards; `scorecard` fires never decide.

## 8. Use ledger (append only)

| Date | Pre-registration | Subset / orders | Purpose |
|---|---|---|---|
| — | — | — | (none yet; sealed 2026-10-24) |

## 9. Claims on subsets (append only; a claim is not an opening)

A pre-registration may lay a claim on a subset before its data exist, so that no other test spends it. The
claim names the subset, the target and the pre-registration that will use it. Opening still follows §7 and
is logged in §8.

| Date | Claimed by | Subset | Target | Owner decision |
|---|---|---|---|---|
| 2026-10-25 | ML ember-cast programme (`docs/PREREG_ML_EMBER_DEV.md` §11, stage 2 `docs/PREREG_ML_EMBER.md` to follow) | Canadian 2026 **`confirm`** subset (§5, NBAC 2026, seed `S_SEAL_CA = 2026102402`) | ember target L (where new VIIRS spot fires start 1-15 km ahead), PREREG_LR8 §4/§6 definitions | D2, Travis Kennedy, 2026-10-25 |
| 2026-10-25 | ML ember-cast programme (same) | Supplementary US 2026 `scale` block (§6a) | scale only for the ember target L; never decides | D2, Travis Kennedy, 2026-10-25 |
