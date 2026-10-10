# Structure-to-structure spread: specification

Status: **specification, 2026-10-09**; built so far: units (§2), Hamada (§4) and, since
2026-10-10, ember ignition (§6.1). Nothing here is validated in Canada. Every output built
from it must be labelled **"illustrative — not validated in Canada"**.

This is the specification for adding structure-to-structure fire spread to FireSim. It fixes one
value for every equation and constant, with its source, and records each conflict between the
published sources and the choice made. Code must follow this file; when the code departs from
it, change this file in the same PR.

Owner decision (2026-10-09): rebuild the Berkeley/UMD WUI model (the ELMFIRE WUI extensions of
Purnomo, Qin, Trouvé, Gollner and co-workers) **from the published literature only**. ELMFIRE is
AGPL v3 + Commons Clause; its source must not be opened, read, quoted or translated for this work
(`docs/PROJECT_RECORD.md` §3). Every equation and constant below is cited to a paper, section,
equation and page. Where a value is not published, this file says so and states the FireSim
choice as a **heuristic**.

Background notes (owner's internal reports, not peer reviewed):
- *Structure ignition paper check* (2026-10-09), `~/dev/wildfire/reports/Structure ignition paper check.md`:
  component-by-component check, conflicting values, grid-resolution finding, build order.
- *Structure ignition in the WUI: a literature review for FireSim* (2026-10-06),
  `~/dev/wildfire/references/structure-ignition/structure_ignition_review.md`.

## 0. What this is and is not

- It is a **what-if layer** for preparedness and training: under the modelled wildland fire and
  wind, how fast could fire move through a neighbourhood if buildings burned and ignited each
  other the way an empirical urban-fire model says.
- It is **not** a prediction of which houses burn. It is not a loss probability, it does not
  replace building exposure (`docs/building-exposure.md`), and it says nothing about
  evacuation tiers. Exposure stays exposure. A building the model does not reach is **not safe**:
  ember ignition (most WUI losses), yard fuels, sheds, fences and vehicles are not in stage 3.
- The published validations are Californian, Rothermel/LANDFIRE-driven, and recall-heavy
  (§8). None transfers to Edmonton without a Canadian test.

## 1. Sources and labels

| Key | Source | Read as |
|---|---|---|
| **PROCI24** | Purnomo D.M.J., Qin Y., Theodori M., Zamanialaei M., Lautenberger C., Trouvé A., Gollner M.J. (2024). Reconstructing modes of destruction in wildland-urban interface fires using a semi-physical level-set model. *Proc. Combust. Inst.* 40: 105755. doi:10.1016/j.proci.2024.105755 | PDF (7 pp.) + supplementary material (eqs S1-S21) |
| **IJWF24** | Purnomo D.M.J., Qin Y., Theodori M., Zamanialaei M., Lautenberger C.W., Trouvé A., Gollner M.J. (2024). Integrating an urban fire model into an operational wildland fire model to simulate one dimensional wildland-urban interface fires: a parametric study. *Int. J. Wildland Fire* 33(10): WF24102. doi:10.1071/WF24102 | Publisher HTML (no page numbers; cited by section and equation). PDF and supplement not read |
| **FSJ104651** | Purnomo D.M.J., Zamanialaei M., Earle M., Theodori M., Qin Y., Lautenberger C., Trouvé A., Gollner M.J. (2026). Sensitivity of ELMFIRE to real-world input datasets for WUI fire modeling. *Fire Safety J.* 161: 104651. doi:10.1016/j.firesaf.2026.104651 | PDF + supplementary material ("Overview of HAMADA model", "Overview of ember model"; the supplement has no page or equation numbers) |
| **FSJ104686** | Qin Y., Purnomo D.M.J., Theodori M., Zamanialaei M., Lautenberger C., Gollner M., Trouvé A. (2026). Simulations of firebrand-driven fire spread in landscape-scale Wildland-Urban-Interface (WUI) and urban conflagration models. *Fire Safety J.* 162: 104686. doi:10.1016/j.firesaf.2026.104686 | PDF |
| **Qin25** | Qin Y. (2025). *A physics-based Eulerian framework for modeling firebrand showering in regional-scale wildland and WUI fire simulations.* PhD dissertation, University of Maryland (DRUM). | PDF, 305 pp.; printed page numbers |
| **HT08** | Himoto K., Tanaka T. (2008). Development and validation of a physics-based urban fire spread model. *Fire Safety J.* 43(7): 477-494. | Kyoto University repository author manuscript; pages are the manuscript's |
| **Cohen04** | Cohen J.D. (2004). Relating flame radiation to home ignition using modeling and experimental crown fires. *Can. J. For. Res.* 34: 1616-1626. | PDF |
| **NRC21** | National Research Council Canada (2021). *National Guide for Wildland-Urban Interface Fires.* | PDF |
| **Hamada51** | Hamada M. (1951). On the rate of fire spread. (Cited by HT08 [4,5], FSJ104651 [15], Qin25 [33].) | **Not read**; known only through the three papers above |
| **Scawthorn / Hazus** | Low-wind correction to Hamada, attributed to Scawthorn and the FEMA Hazus model by FSJ104651 SI and Qin25 p.39 | **Not read**; used as printed in FSJ104651 SI |
| **CAT17** | Caton S.E., Hakes R.S.P., Gorham D.J., Zhou A., Gollner M.J. (2017). Review of pathways for building fire spread in the wildland urban interface Part I: exposure conditions. *Fire Technol.* 53: 429-473. doi:10.1007/s10694-016-0589-z | PDF (read 2026-10-10). Background for the three pathways; no equations used |
| **LD10** | Lee S.W., Davidson R.A. (2010). Physics-based simulation model of post-earthquake fire spread. *J. Earthquake Eng.* 14(5): 670-687. doi:10.1080/13632460903336928 | PDF (read 2026-10-10). Model lineage and caveats; no equations used |
| **SBK14** | Syphard A.D., Brennan T.J., Keeley J.E. (2014). The role of defensible space for residential structure protection during wildfires. *Int. J. Wildland Fire* 23: 1165-1175. doi:10.1071/WF13158 | Letter-paged online-early PDF (pp. A-K) |

Labels used below: **[P]** primary source read; **[H]** FireSim heuristic or adaptation (not
published; stated reason); **[T]** tuned, not measured (calibrated to California fires or chosen
by the authors to fit an outcome); **[U]** unverified or not published.

PDFs and text extractions: `~/dev/wildfire/references/structure-ignition/` (`text/` holds the
extractions used for this file).

**Pathways and lineage (CAT17, LD10; read 2026-10-10).**
- CAT17 (p.436) groups WUI exposure into three pathways: radiant exposure, direct flame
  contact and firebrands. Here they map to stage 4 (WU-E radiation and flame contact, §5) and
  stages 6-7 (embers, §6); the Hamada stage lumps all three into one empirical rate (§4).
  CAT17 calls firebrands "one of the primary sources of ignition" with no consensus on their
  share (pp.440, 442), and records flame-contact fluxes of 20-40 kW/m² (turbulent) to 50-70 kW/m²
  (laminar) (p.438).
- LD10 (p.671) describes Hamada (1951) as assuming "equally spaced, equal-size square
  buildings" and an elliptical fire, with empirical upwind, downwind and crosswind speeds; it
  says later models to about 2000 (Scawthorn et al. 1981; FEMA 2006, i.e. Hazus) adapted
  Hamada's equations, and that Hamada gave "fair agreement" when hindcasting losses in five US
  earthquakes (Scawthorn 1987). This is a secondary confirmation of the Scawthorn / Hazus
  lineage above; the Hazus correction itself is still used as printed in FSJ104651 SI.
- LD10 is the physics-based alternative (room-by-room fires, window-flame impingement,
  configuration-factor radiation from window flames, room gas and roof flames, branding). Its
  ignition rule is a critical flux of 12.5 kW/m² with an ignition delay of 1 / 7 / 10 / 25 /
  30 min at 30 / 20 / 17.5 / 15 / 12.5 kW/m² (p.677, after Quintiere 2006). It needs room
  layouts, window areas and fire loads that FireSim does not have, so it is not a FireSim
  option.

## 2. Building representation

**Each building is one unit.** Units are built from the Microsoft Canadian Building Footprints
(`data/edmonton_buildings.geojson.gz`, 346,238 footprints, ODbL), clipped to the run area, each
with its own state. Buildings are **not** rasterised onto the FBP fuel grid.

Why: FSJ104686 §3.4, §4 and §6 (pp.7-9) [P] show that structure-to-structure spread on a grid
converges only when the cell size is at most half the structure size (SS) and half the
structure separation distance (SSD), and that each structure must be treated as a single unit
("single unit treatment", p.4). At 30 m cells the errors ranged from no spread to more than five
times too fast (p.9). Edmonton side yards are a few metres wide, so a converged grid would need
cells of about 1-3 m. The per-building (graph) treatment avoids this; it follows Qin25 §6.3.1
(eqs 6.9-6.10, pp.168-170) [P], which pools heat and embers per building and emits from the
centroid. It is a design departure from the published per-cell ELMFIRE coupling and must be
verified against the FSJ104686 one-dimensional benchmark (Figs 5-7) before the WU-E and ember
stages are used.

Per unit (`engine/src/firesim/structures/units.py`):

| Field | Definition | Note |
|---|---|---|
| `id` | index in the run's unit list | |
| centroid | footprint centroid (lat, lng and local x, y in metres) | |
| footprint | the footprint polygon, local metres | MultiPolygons kept whole |
| `area_m2` | footprint area | |
| `size_m` | `sqrt(area_m2)`: side of the equal-area square | Hamada assumes square plans (HT08 p.25) [P]; the square equivalent is an [H] |
| separation | edge-to-edge distance to each neighbour (shapely `distance`, metres); nearest-neighbour separation per unit | Hamada's d is the "average separation of buildings" (HT08 p.25) [P]; FireSim uses the pair's own separation [H] |
| neighbour graph | all pairs with separation ≤ `neighbour_cutoff_m` | cutoff: §4.4 |

Local metres: an equirectangular frame centred on the run area (the fuel grid box; `x = (lng −
lng0) · 111,320 · cos(lat0)`, `y = (lat − lat0) · 111,320`). Over a run area of a few tens of km
the distortion is well below the footprint accuracy. Footprints that are invalid are repaired
with `make_valid`; empty ones are dropped.

**Units are built only where the spread can reach** (2026-10-10; the API run that built all
334,213 Edmonton footprints in the grid box peaked at 1.76 GB and was OOM-killed on the 2 GB API
machine). After the grid run, units are built for the footprints whose bounds intersect a
*reachable box* R: the box of the burned cells grown by the front-contact reach (contact-offset
radius + 2 cells) and a spread margin (first 2 × cutoff + 250 m). The result is exact for the
whole run area when no involved unit lies within the cutoff (+ 1 m) of R's edge: a footprint not
built lies wholly outside R, so it is farther than the cutoff from every involved unit (not a
graph neighbour) and too far from the burned cells for front contact; it can never be involved
and cannot change any built unit's time. Otherwise the margin is doubled and the build repeated.
The frame is fixed at the grid box centre, so results do not depend on R. On the six 2026-10-09
sensitivity runs the counts are identical to the whole-area build, with 1,376-7,219 units built
instead of 334,192. A memory guard stops the build when R would hold more than 60,000 footprints
(frames then say `computed: false`, "not computed: too many buildings in run area").
`units_in_run` counts the footprints whose centroid is in the grid box (the building index's
ring-average centroid in the API: 334,213; before 2026-10-10 it counted valid units by shapely
centroid, 334,192).

The footprint attributes `type`, `height`, `material` and `roof_type` in the data file have no
documented source (every footprint is `wood_frame` / `asphalt_shingle`) and are **not used**.
None of the published models uses construction, roof or height either (PROCI24 p.3: "These
parameters are uniform for all structures"; Qin25 p.185).

## 3. Coupling to the FBP wildland front

Wildland intensity and flame length come from FireSim's FBP layer as the grid model already
computes them for building exposure (`exposure.py`, `cellular.py: flame_emitters`): head fire
intensity per burned cell; flame length Byram (1959) `0.0775 I^0.46` for surface fire and
Thomas (1963) `0.0266 I^(2/3)` when CFB ≥ 0.1, as suggested by Rothermel (1991) for crown fires
(via Alexander & Cruz 2012, p.99; approximate for crown fires). FireSim
does not use Rothermel.

- **Time a unit is first reached by the front** `t_front` **[H]** (rule changed 2026-10-09,
  `building_cell_contact_times` in `structures/spread.py`). A unit's *building cells* are the
  grid cells its footprint touches: the cells the all-touched building mask makes non-fuel
  (`data/environment.py`), found from the footprint geometry so the rule is the same inside and
  outside the masked neighbourhoods. The unit is reached at the earliest arrival of a burned cell
  whose square lies within `wildland_contact_m` of the square of one of its building cells
  (edge-to-edge distance between cells). Cells on a regular grid are 0 m apart (shared edge or
  corner) or at least one cell apart, so for any contact distance below the cell size, including
  the default 10 m on the 50 m engine grid, the rule is: **the front has reached one of the 8
  neighbours of a building cell, or a building cell itself where it is not masked**; `t_front` is
  that cell's arrival. A contact distance of a whole cell or more adds rings of cells.
  Default `wildland_contact_m = 10 m`, the flame-contact band of `docs/building-exposure.md`
  (Cohen 2000: walls ignited only on flame contact at 10 m in ICFME). The published coupling has
  no such distance: in FSJ104651 (p.3) a structure cell next to a burning cell simply starts to
  burn at the Hamada rate, and in PROCI24 (eq 8, p.3) the wildland cell's `HRR = I_f Δx` drives
  the WU-E heat balance.
  - *Why from the building cells* (owner decision under delegation, 2026-10-09, [R6] addendum):
    the first rule measured 10 m from the footprint to the nearest burned cell edge. With 50 m
    cells and the all-touched mask the nearest burnable cell lies 0-50 m from a footprint
    depending only on where it sits inside its masked cell, so the 10 m test was decided by grid
    geometry (contact 5 / 20 m changed involved buildings by −71 % to +557 %, [R7]). The mask
    removes a building's cells because the building is there, and a 50 m grid cannot say how much
    yard fuel lies between the wall and the cell edge; fire reaching the cells around the building
    is the closest the grid can resolve to reaching the building. The outcome now depends only on
    *which* cells a footprint touches, never on where it sits inside them. The formulation as a
    cell-to-cell distance (rather than "8-adjacent" directly) keeps `wildland_contact_m`
    meaningful on finer grids.
  - *No jumps across non-fuel*: the burned cell must neighbour a cell the footprint touches, so a
    road or river at least one cell wide that the footprint does not touch stops contact. The
    rule bridges non-fuel only inside the building's own cells (at most one cell, ≤ 71 m on the
    diagonal on the 50 m grid).
  - *Consequences* ([R8]): `wildland_contact_m` has no effect below the cell size (contact 5 / 10
    / 20 m give identical results); front contact is now made at cell resolution (in the R8 runs
    the footprint was a median 22-42 m and at most ~70 m from the nearest burned cell edge), and
    involved buildings at 6 h rose by +38 % to +751 % against the first rule. The first rule is
    kept as `footprint_contact_times` for diagnostics only.
  - *20 m cells near buildings* (2026-10-10, M5, [R19] in the record): when the run uses the 20 m
    WUI window (`spread/wui_window.py`; frame `grid.cell_m` = 20) the same rule is applied on the
    20 m crop: building cells are the 20 m cells a footprint touches, and contact is a burned 20 m
    cell next to one. On the six sensitivity runs the footprint-to-burned-cell gap fell from a
    median 22-42 m (max 64-78 m) to **9-15 m (max 24-30 m)**, close to the 10 m band. Contact
    distances below 20 m still give one outcome (5 = 10 m); 20 m now adds a ring (+9 to +68 %
    involved). Units are built over the whole run grid (`area_bbox`), so building-to-building
    spread continues beyond the crop; the crop holds every burned cell more than 6 cells from its
    edge, so no unit outside it has front contact.
- **Wind**: the run's 10 m open wind (FBP input) for the weather period in force, converted to
  m/s. Hamada51's wind height is not stated in any source read [U]; FSJ104651 drove its runs with
  RTMA 10-minute mean wind (gust for Thomas; Table 2, p.5). FSJ104686 (p.3) flies embers at the
  6.1 m (20 ft) wind. FireSim uses the 10 m wind for Hamada [H]; the ember stage needs an
  explicit 10 m → 6.1 m → 2 m reduction (§6).
- **Burning period**: the wildland burning period (`diurnal.py`) does not apply to building
  spread [H]: buildings burn regardless of fine-fuel moisture, and Hamada has no diurnal term.
- **Wildland HRR into the WU-E stage** (later): `HRR = HFI · Δx` (PROCI24 eq 8, p.3; Qin25 eq
  2.82, p.42) [P], with HFI from FBP. Published runs hold the vegetation HRR constant once lit
  (Qin25 p.42); FireSim uses the flame residence already in `exposure.py` (cell size / normal
  spread rate, 60 s where the front stops). This is a stated departure.
- **Building → wildland** (later): PROCI24 (p.3) has interface wildland cells ignite by the same
  heat-accumulation rule "with distinct parameter values", which are **not published** [U]. Not
  specified here; a later stage must choose and label a rule.
- The vegetation 250 kW/m² fix next to urban cells (Qin25 §8.4, p.227: "a crude but practical
  fix") is **[T]** and is **not** adopted. FBP crown-fire HFI is large enough to test without it.

## 4. Hamada option (stage 3)

Empirical urban fire spread (Hamada51 via HT08, FSJ104651 SI and Qin25 §2.1.2). Japanese
wooden-city calibration (HT08 p.25: β "deduced from the record of the past urban fires"). It
lumps all spread modes (flame contact, radiation, and implicitly embers) into one rate (Qin25
p.177, p.159).

### 4.1 Equations

For direction i ∈ {d (downwind), s (sidewind/crosswind), u (upwind)}, building size a₀ (m),
separation d (m), combustible fraction f_b, wind V (m/s):

```
T_i  = (1 − f_b) [3 + 0.375 a₀ + 8 d / (c4_i + c5_i V)]
       + f_b / C_i(V) · [5 + 0.625 a₀ + 16 d / (c4_i + c5_i V)]          (FSJ104651 SI; Qin25 eq 2.69, p.39)
C_i(V) = c1_i (1 + c2_i V + c3_i V²)                                     (FSJ104651 SI; Qin25 eq 2.70, p.39 — typo, see C6)
U_i  = (a₀ + d) / T_i                                                    (FSJ104651 eq 2, p.3; Qin25 eqs 2.77-2.79, p.40)
```

| i | c1 | c2 | c3 | c4 | c5 |
|---|---|---|---|---|---|
| d | 1.6 | 0.1 | 0.007 | 25.0 | 2.5 |
| s | 1.0 | 0.0 | 0.005 | 5.0 | 0.25 |
| u | 1.0 | 0.0 | 0.002 | 5.0 | 0.2 |

(FSJ104651 SI table; Qin25 Table 2.2, p.39 — identical.) [P]

**Units.** T_i in **minutes**, U_i in m/min. Neither FSJ104651 nor Qin25 states the unit of T;
HT08 eqs 42-43 (p.25) give Hamada's rates explicitly in m/min with the same 3 + 3a/8 + 8d/(…)
structure [P]. FireSim takes minutes from HT08.

**Low-wind (Hazus) correction**, applied when V < 10 m/s (FSJ104651 SI; Qin25 eqs 2.71-2.73,
p.39) [P]:

```
K_d' = K_d V/10 + (1 − V/10) √( ((K_d + K_u)/2) K_s )
K_s' = K_s V/10 + (1 − V/10) √( ((K_d + K_u)/2) K_s )
K_u' = K_u V/10 + (1 − V/10) √( ((K_d + K_u)/2) K_s )      (SI typo corrected, C7)
```

where K_i are the ellipse dimensions (Qin25 eqs 2.66-2.68, pp.38-39). Their time derivatives
(FSJ104651 SI; Qin25 eqs 2.74-2.76, p.40) depend on t because K_s and K_u carry offsets. A
building-to-building step is a steady crossing, so FireSim uses the **long-time limit** (K_i ≈ U_i t):

```
V_i = (V/10) U_i + (1 − V/10) √( ((U_d + U_u)/2) U_s )      for V < 10 m/s
V_i = U_i                                                    for V ≥ 10 m/s
```

This limit is FireSim's derivation from the published eqs 2.74-2.79 **[H]**. At V = 0 all three
rates equal the geometric term, so spread is isotropic, which is the stated purpose of the
correction (FSJ104651 SI: the original "predicts an elliptical fire perimeter with an extended
reach in the downwind direction even in the absence of wind").

### 4.2 Direction

The Hamada perimeter is an ellipse with downwind reach K_d, upwind reach K_u and crosswind
extent K_s from the ignition point (Qin25 pp.38-39). FireSim builds the rate toward any bearing
from that ellipse directly: semi-major (V_d + V_u)/2 along the downwind direction, semi-minor V_s,
centre (V_d − V_u)/2 downwind of the burning building, and the rate toward angle θ from downwind
is the distance from the burning building to the ellipse along θ — the same construction as the
FBP ellipse (`spread/ellipse.py: calculate_ros_at_theta`, ST-X-3 eqs 82-89) with head = V_d,
back = V_u, flank = V_s **[H]**. It returns V_d downwind and V_u upwind exactly. Departure from
the level-set projection in Qin25 (eq 2.80 `LB = (V_u + V_d)/V_s`, then eqs 2.54-2.57 and 2.61-2.64)
explained in C8.

### 4.3 Building-to-building time

For a burning unit j and a neighbour k at edge-to-edge separation d_jk and bearing θ_jk from
the downwind direction:

- a₀ = (size_j + size_k)/2 (the pair's mean square-equivalent side) [H]; d = d_jk [H].
- Rates V_d, V_s, V_u from §4.1 with that a₀ and d and the wind in force; rate toward k
  `R_jk = r(θ_jk)` from §4.2.
- Crossing time `τ_jk = (a₀ + d_jk) / R_jk` (minutes). With the wind constant and k straight
  downwind (V ≥ 10 m/s), τ_jk = T_d: Hamada's "time required for fire to reach adjacent
  buildings" (FSJ104651 p.3; Qin25 p.39).
- Wind changes during the crossing: progress accumulates at `R_jk(t) / (a₀ + d_jk)` per minute
  through the piecewise-constant weather periods; k ignites when progress reaches 1 [H]. This is
  the graph analogue of advancing the level set at a time-varying U.
- First ignition times over the graph: Dijkstra from all units ignited by the wildland front
  (`t_front`). The crossing times are non-negative and a later start never arrives earlier, so
  the label-setting search is exact.
- **No burnout** in Hamada (FSJ104651 pp.3, 17) [P]: a burning unit stays a source. FireSim
  reports first ignition times only, so burnout would matter only if a source burned out before
  a crossing finished; not modelled, stated as a limit.
- **Ignition from the front**: a unit is involved at `t_front` (§3) [H].

### 4.4 Defaults

| Parameter | Default | Source / reason |
|---|---|---|
| f_b (combustible fraction) | 1.0 | Qin25 p.224: f_b = 1 for the Thomas Fire Hamada run [P]. Not measured for Edmonton. f_b < 1 lowers the wind term (see C9) |
| `neighbour_cutoff_m` (largest separation that can carry spread) | **30 m** | **[H]** Hamada alone never stops spreading: τ is finite at any d (at V = 17.8 m/s, a₀ = 10 m, d = 100 m gives τ_d ≈ 4.3 min). The published level-set coupling limits a burning cell's influence to its 8 neighbours (FSJ104651 p.17, at 30 m cells). 30 m: IJWF24 Fig. 7a (no spread beyond 30 m separation for 10 m buildings without embers; model output) and NRC21 p.27 ("a 30 m distance is often used as the limit for significant radiative heating"). **Confirmed 2026-10-09** under the owner's delegation (decision D2, `docs/PROJECT_RECORD.md` §3 [R6]). Most Edmonton front-to-front distances across a local street are about 30-40 m, so the cutoff decides whether a street stops spread. Sensitivity on Edmonton footprints [R7]: involved buildings at 6 h −48 % to 0 % at 20 m, +5 % to +220 % at 45 m; with the building-cell front contact [R8]: −58 % to −27 % at 20 m, +4 % to +170 % at 45 m |
| `wildland_contact_m` | 10 m | §3 [H]. **Confirmed 2026-10-09** (D2). Measured from the building's own grid cells since 2026-10-09 (§3): below one cell (50 m engine grid) it reduces to the 8 neighbours of the building cells and has no further effect. Sensitivity with the first rule (footprint to nearest burned cell) [R7]: −71 % to 0 % at 5 m, +1 % to +557 % at 20 m, a grid artefact; with the building-cell rule [R8]: 0 % at 5 and 20 m |
| footprints | all (garages and sheds included) | **Decision D4** (2026-10-09). Dropping footprints < 40 m² [H] changes involved buildings by −8 % to 0 % [R7]; −12 % to 0 % with the building-cell front contact [R8] |
| wind | 10 m open wind of the period in force, km/h ÷ 3.6 | §3 [H] |

### 4.5 Check values (from the equations above; used as unit tests)

a₀ = d = 10 m, f_b = 1:

| V (m/s) | T_d | T_s | T_u (min) | U_d (m/min) | U_d (m/s) |
|---|---|---|---|---|---|
| 0 | 11.031 | 43.25 | 43.25 | 1.813 | 0.030 |
| 10 | 3.345 | 21.72 | 28.42 | 5.979 | 0.100 |
| 17.8 | 1.695 | 10.91 | 18.33 | 11.80 | 0.197 |

At V = 0 after the Hazus correction all rates are √(((1.813 + 0.462)/2) · 0.462) = 0.725 m/min.

**Published worked value not reproduced.** Qin25 reports a Hamada rate of 0.34 m/s for a = d =
10 m, fully combustible, 17.8 m/s wind (pp.159, 161, 167; baseline in Table 6.1, p.153). The
published equations give 0.197 m/s. Qin25 also reports 0.2-0.6 m/s over its separation range
(p.177) where the equations give about 0.14-0.29 m/s for d = 2.5-30 m. The ratio is about 1.7 in
both. No published equation, unit or wind height read here explains it (T in seconds, wind in
mph, or applying the Hazus blend above 10 m/s do not give 0.34). FireSim follows the equations
and records the gap as a strict expected failure in the tests and as an open item.

Published properties the code must show: rate increases with separation (Qin25 p.177: "the
Hamada model also predicts an increasing ROSsurface with increasing separation distance, which
appears counterintuitive"), although the **time** to cross a larger gap still increases; rate
increases with wind (Qin25 p.178); isotropic at zero wind (FSJ104651 SI).

## 5. WU-E option (stage 4, not built)

Semi-physical: direct flame contact, point-source radiation, flux-time ignition (PROCI24 eqs 1-8,
pp.2-3; IJWF24 eqs 2-15; Qin25 eqs 2.81-2.95, pp.41-46) [P]. Specified here so later stages
start from fixed values.

| Item | Equation | Chosen value | Source |
|---|---|---|---|
| Radiation | `q_r'' = χ HRR / (4π R²)`, zero beyond R = 100 m | **χ = 0.3** (C2) | PROCI24 eq 3, p.3; Qin25 eq 2.81, p.42 [P] |
| Flame contact | `q_c = HRR · A_t / Δx²` | — | PROCI24 eq 2, p.2; IJWF24 eq 2; Qin25 eq 2.83, p.42 [P] |
| Contact disables radiation | a target in flame contact receives no radiation term | — | PROCI24 p.2 [P] |
| Flame reach ellipse | a (downwind), b (side), c (upwind) linear in v for v < 10 or > 17.3 m/s, quadratic for 10-17.3 m/s; coefficients linear in house size d and separation s | PROCI24 SI eqs S1-S21 (C3) | PROCI24 SI; = Qin25 Table 2.3 [P] |
| Heat received | `q_t = α_c q_c + α_r q_r'' Δx²`; `α_c = (S_b + S_v)/S_t`; `α_r = absorptivity · α_c` | **α_c = 0.95, absorptivity 0.89** (IJWF24 baseline), α_c 0.5 as a scenario (Qin25 p.227) | PROCI24 eqs 4-5, p.3 (no values printed); IJWF24 eqs 11-12 and scenarios [P] |
| Ignition | `Σ q_t Δt / A ≥ FTP` (per unit: A = footprint area) | **FTP = 10,500 kJ/m²** [T] | IJWF24 eq 13 and baseline; Qin25 p.227. PROCI24 (p.3) says "a uniform threshold is used" without the value [P] |
| Design fire (per unit) | linear growth → plateau → linear decay; `HRR = HRRPUA · area` | **150 kW/m², 5 min growth, 1 min full, 60 min decay** (C1) | PROCI24 p.3, after Maranghides & Johnsson (NIST TN 1600) [P] |
| Wildland source | `HRR = HFI · Δx`; flame reach from a wildland cell `3((3/5)v + 3) + d/2` | — | PROCI24 eq 8; IJWF24 eq 15 (after Jiang et al. 2021) [P] |
| Point source position | unit centroid; distance R centroid to target footprint edge | — | [H], after Qin25 §6.3.1 single-unit treatment |

Caveat on the radiation row: LD10 (p.672) notes that treating emitted radiation as a point
source "performs poorly when ignition of combustibles is of concern" (citing Beyler 2002) and
uses configuration factors instead. The WU-E point source is kept because it is the published
coupled model, but its near-field fluxes (separations of a few metres, typical in Edmonton)
must be checked against a view-factor calculation (e.g. `exposure.py`'s panel model) before
stage 4 outputs are shown.

The flux-time product here is the **heat dose** form of PROCI24/IJWF24 (kJ/m², no critical flux),
not Cohen04's `∫(q − 13.1)^1.828 dt ≥ 11,501` used in `exposure.py`. The two must not be mixed in
one output; the WU-E output must say which it uses.

Verification gates before use: IJWF24 Fig. 3a (within 20 % of Hamada without embers), IJWF24
Fig. 7a (no spread beyond 30 m for 10 m buildings without embers), PROCI24 pp.4-5 heat-flux
ranges (Tubbs: flame contact 30-50, radiation 5-25 kW/m²; Thomas: 80-130 and 10-40).

## 6. Embers (stages 6-7, built 2026-10-10: opt-in, illustrative)

| Item | Equation / value | Source |
|---|---|---|
| Generation | `GR = GR' · HRR`; GR' = 33.3 pcs/(MW·s) vegetation, **10 pcs/(MW·s) structures [T]** (raised from 5.68; 5.68 kept as a scenario) | Qin25 eqs 3.1-3.2, p.61, p.147, Table 6.1 (p.153); FSJ104686 p.4 [P] |
| Ember mass | 0.2 g | Qin25 eq 3.7; FSJ104686 p.3 [P] |
| Transport, vegetation | Sardoy lognormal: μ = 1.47 I_f^0.54 v^−0.55 + 1.14 (Fr ≤ 1), 1.32 I_f^0.26 v^0.11 − 0.02 (Fr > 1); σ = 0.86 I_f^−0.21 v^0.44 + 0.19 (Fr ≤ 1), 4.95 I_f^−0.01 v^−0.02 − 3.48 (Fr > 1); Fr = v/√(g L_c); L_c = (1000 I_B / (ρ c_p T √g))^(2/3), I_B in MW/m, ρ = 1.1 kg/m³, c_p = 1.0 kJ/(kg K), T = 300 K | FSJ104651 SI; IJWF24 eqs 6-10; Qin25 eq 4.2, p.72 [P]. (IJWF24 HTML renders L_c without the exponent; Qin25 eq 4.2 is used) |
| Transport, structures | Himoto lognormal, truncated at the 99th percentile; X_max = 65 / 84 / 109 m for 10 / 40 / 160 MW in a 17.9 m/s wind | Qin25 eqs 6.3-6.4; FSJ104686 p.3, Fig. 2 [P] |
| Flight wind | 6.1 m (20 ft) wind | FSJ104686 p.3 [P]; FireSim must reduce its 10 m wind explicitly (method to be specified and labelled [H]) |
| Pooling | per unit (footprint), emission from the centroid | Qin25 §6.3.1, eqs 6.9-6.10, pp.168-170; FSJ104686 p.4 [P] |
| Ignition criterion | ψ ≥ C / ((v_air − v_min)(v_max − v_air)), **v_min = −0.073 m/s, v_max = 4.111 m/s, C = 0.211 g/cm²** (PTW, 2.0 % moisture) | FSJ104686 eq 1, p.3; Qin25 eq 5.12, p.120 [P]. One material, one moisture; "PTW (not zone-0 fuel)" (FSJ104686 p.4) |
| Ignition delay | t_ign,small with P = 0.9 and **τ = 42 s** (C4), then t_ign,large = 300 s (t² growth) | FSJ104686 p.4; Qin25 pp.106, 121-123 [P] |
| Check value | at 17.9 m/s wind, v_air ≈ 1.1 m/s at 2 m gives ψ* ≈ 0.059 g/cm² ≈ 2.95 × 10⁵ embers on a 10 × 10 m target | FSJ104686 p.5 [P] |
| Simplified version (not adopted) | fixed 10 embers per time step per burning structure, P_ign = 1 **[T]** ("selected based on an analysis of different ember generation rates", FSJ104651 p.13) | FSJ104651 pp.7, 13 [P] |

The ember models produce short-range (≲ 100 m) structure-to-structure transport (FSJ104686 p.3:
"focus… is on short-distance firebrands"). They do not reproduce the 1-2 km spotting seen at
Jasper and Fort McMurray; FireSim's Albini spotting (`spread/albini.py`) stays the long-range
model.

### 6.1 As built (2026-10-10, `engine/src/firesim/structures/embers.py`)

Opt-in: API `structure_embers` (default **false**; needs `structure_spread`) and
`structure_design_fire_kw_m2` (150 default, 400). Ember ignition is added **to** Hamada and front
contact: a unit is involved at the earliest of front contact, a Hamada crossing and an ember
ignition, and the mechanism is recorded (`ember`). Every equation was checked against the paper
page before coding; the page and equation are in the code docstrings.

| Step | As built | Source / label |
|---|---|---|
| Burning unit | From its involvement time (any mechanism) a unit burns the design fire, HRR = HRRPUA(t) × footprint area | Qin25 eqs 6.2, 6.10, pp.146, 169 [P]; design fire from involvement for front and Hamada units too [H] |
| Design fires | 150 kW/m², 300 / 60 / 3600 s (default, D1); 400 kW/m², 300 / 3600 / 300 s (scenario) | PROCI24 p.3; FSJ104686 p.2 [P] |
| Generation | GR = GR' × HRR; GR' 10 pcs/(MW·s) structures [T] (5.68 scenario), 33.3 vegetation; counts are the exact integral of HRR over each step | Qin25 eqs 3.8, 3.17, Table 6.1 [P] |
| Structure transport | Himoto lognormal: mean / std of the flight distance 0.47 B*^(2/3) D and 0.88 B*^(1/3) D converted to log form; D = √(footprint area); ρ_p 100 kg/m³, d_p 5 mm, ρ∞ 1.1, c_p 1.0, T∞ 300 K; truncated at the 99th percentile and renormalised; B* evaluated at the **10 m** wind (C17) | HT08 eqs 38-40, pp.22-24; Qin25 eqs 6.3-6.4, p.149; FSJ104686 p.3 [P] |
| Crosswind | Normal, σ_Y = 0.92 D, truncated at the two-sided 99 % and renormalised | HT08 eq 39, p.23 [P]; truncation by analogy with Qin25 p.75 [H] |
| Emission point | Centroid; the flight distance is measured from the source's downwind edge (its extent along the wind), so nothing lands on the emitting footprint | Qin25 §6.3.1 item 3, p.170 [P]; the edge offset is the reading that reproduces FSJ104686 p.5's 33 % (C16) [H] |
| Vegetation transport | Burning grid cells emit at HRR = HFI × cell size (Qin25 eq 6.1) for their flaming time (cell size / normal speed, + 60 s where the front stops, as `exposure.py`), Sardoy lognormal (Qin25 eqs 4.2-4.6, I in MW/m) with the **6.1 m** wind (C18), measured from the cell's downwind edge, σ_Y = 0.92 × cell size | Qin25 pp.42, 72-73, 124 [P]; σ_Y for a cell [H] |
| Pooling | Expected number landing on each footprint: the target's box in the source's wind frame (exact for a wind-aligned rectangle) × footprint area / box area | Qin25 eqs 4.17, 6.5, 6.9 [P]; box × fill [H] |
| Ignition | ψ = N × 0.2 g / A ≥ ψ*(v_air), v_air = 0.064 × 6.1 m wind; the crossing time is interpolated within the 30 s accumulation step; ignition at crossing + flight time (distance / 6.1 m wind) + 42 s + 300 s; the unit then starts its design fire | FSJ104686 eq 1, pp.3-5; Qin25 eq 5.12, pp.120-123, 157 [P] |
| Determinism | Expected-value pooling, no random draws: the stochastic t_ign,small algorithm (Qin25 eqs 5.2-5.4, p.106) is replaced by the fixed 42 s of the FSJ104686 p.5 worked example | [H]; runs are repeatable without a seed |
| Wind | 10 m open wind of the period in force; 6.1 m = 10 m ÷ 1.15 | Andrews (2009) RMRS-GTR-213 p.58 [P] for the ratio; its use in town [H] |
| No ember burnout, no cooling | Embers accumulate without loss | FSJ104686 p.8 limit (9) [P] |

**Check values reproduced (unit tests, `engine/tests/structures/test_embers.py`):** X_max 65 / 84 /
109 m for 10 / 40 / 160 MW (FSJ104686 p.3); v_air 1.1 m/s, ψ* 0.059 g/cm², 2.95 × 10⁵ embers on
10 × 10 m (p.5; Qin25 p.157: 2,928.9 pcs/m²); 33 % of a 40 MW structure's embers on the next
structure (p.5); the 1-D benchmark (SS = SSD = 10 m, 400 kW/m², GR' 10, 17.9 m/s) ignites the second
structure at 2,703 s against the paper's 2,705 s (p.5) and spreads at 0.009 m/s against "≈ 0.01 m/s"
(p.6); fire load 1.56 GJ/m² and 1.56 × 10⁶ embers per 100 m² structure (pp.2, 5); Sardoy μ 2.18,
σ 1.23, X_kmax 150 m (Qin25 p.124). The accumulation step is converged (30 s vs 6 s: < 3 s
difference).

**Reachable box (memory, §2).** The first margin also covers the wildland embers: not Sardoy's X_max
(kilometres at crown-fire intensity), but the distance beyond which even every burning cell's whole
emission at its peak density could not reach the smallest ψ* of the run, plus 100 m for footprint
extents [H] (`WildlandSources.safe_reach_m`). The exactness test grows each involved unit by the
larger of the cutoff and its own ember reach (Himoto X_max at its design-fire peak in the strongest
wind, plus crosswind). Tests check that the reachable build equals the whole-area build with
structure and wildland embers. Live-request reproduction ([R11] request, embers on): 3,937 units
built, peak RSS 1,088 MB (1,086 MB with embers off), structure step 9 s.

**What the equations imply (FireSim arithmetic, before any validation).** A building's whole design
fire emits GR' × fire load × area embers: 0.30 GJ/m² × 10 = 3,015 embers/m² of footprint at
150 kW/m², 15,600 at 400 kW/m². The threshold is ψ*/0.2 g = 2,950 embers/m² at 17.9 m/s and
8,900 /m² at 15 km/h (v_air 0.23 m/s). With about a third of the embers landing on the next building
in the best case (and less with the crosswind spread over real layouts), **at the 150 kW/m² default
a building can ignite a neighbour by embers only if several burning buildings feed it; in practice
the ember stage is inert at 150 kW/m²** (Jasper: largest pool 0.05-0.4 of the threshold on any
building not otherwise involved, at 15-80 km/h). GR' = 10 [T] was set by its authors with the
400 kW/m² curve (Qin25 p.147, Table 6.1); combining it with PROCI24's 150 kW/m² curve is FireSim's
choice under D1, recorded as open item (owner decision needed).

## 7. Conflicting published values and the choices made

| # | Conflict | Values and sources | FireSim choice | Reason |
|---|---|---|---|---|
| C1 | Building design-fire peak HRR per unit area | **150 kW/m²**, 5 / 1 / 60 min (PROCI24 p.3); **400 kW/m²**, 300 / 3600 / 300 s (FSJ104686 p.2; Qin25 pp.146-147); **~500 kW/m²** with a plateau of about 10,000 s (Qin25 Fig. 8.6, p.228, read off the plot; Qin says it follows "the default setup in" PROCI24, which it does not match) | **150 kW/m² default; 400 kW/m² as a named scenario. Confirmed 2026-10-09 under the owner's delegation (decision D1).** | 150 is the value printed in the peer-reviewed paper whose WU-E runs were compared with structure losses; PROCI24 p.6 found 700 kW/m² over-predicts (Fig. S5c) and lower peaks "preferable" because heat is lost before reaching targets; plateau length has little effect (PROCI24 p.6; IJWF24 Fig. 6b). The ~500 value is read from a plot only. The evidence does not settle 150 vs 400, so both are run and labelled |
| C2 | Radiant fraction χ | 0.3 (PROCI24 eq 3, p.3; Qin25 eq 2.81, p.42); 0.35 (IJWF24 eq 5) | **0.3** | Two sources incl. the peer-reviewed 2-D paper; IJWF24 is the earlier 1-D study |
| C3 | Flame-reach formulas | Linear Jiang-style (IJWF24 eqs 3-4: a = (3/5)v + 3 + d/2, b = −(1/15)v + 3 + d/2); three-regime regression (PROCI24 SI eqs S1-S21 = Qin25 Table 2.3) | **PROCI24 SI**, behind a verification gate | Later, peer-reviewed, used for the 2-D runs compared with losses. But its intercepts are large (e.g. α₂ = 78.63 + 1.54d − 0.57s gives a downwind reach of about 120 m at v = 10 m/s, d = s = 10 m, versus 14 m from IJWF24 eq 3) and the two regimes are discontinuous at 10 m/s. Before use, stage 4 must compare the reach with IJWF24 and Hamada; if the PROCI24 reach is implausible, fall back to IJWF24 and record it |
| C3a | PROCI24 SI duplicate intercepts | At v < 10 m/s the downwind (S5, α₂) and upwind (S9, γ₂) intercepts are identical (78.63 + 1.54d − 0.57s); Qin25 Table 2.3 repeats it | Use as printed; flag | Cannot be resolved from the published text; may be a transcription error carried into both. With α₁ > 0 > γ₁ the downwind reach still exceeds the upwind reach for v > 0 |
| C3b | PROCI24 SI equation labels | Two equations labelled "S14" (α₂ and α₃); no S15 | Treat the second S14 as S15 (α₃ = 615.19 + 0.31d + 19.34s) | Labelling only; the sequence S13-S21 is otherwise complete |
| C4 | Ember ignition delay τ (t_ign,small) | 42 s (FSJ104686 p.4, citing its ref [24]); 47 s (Qin25 p.121) | **42 s** | The peer-reviewed value; the dissertation precedes it |
| C5 | Upwind/downwind label swap | IJWF24: the text calls a "downwind" and b "upwind"; Table 1 labels a "Upwind flame reach" and b "Downwind flame reach" | **a downwind, b upwind** (the text) | a = (3/5)v + 3 + d/2 grows with wind, b shrinks with wind: only the text's reading is physical |
| C6 | Qin25 eq 2.70 typo | Qin25 eq 2.70 prints `C(V) = c1i(1 + c2i V + c4i V²)` | **c3 on V²** | FSJ104651 SI prints c3; Table 2.2 has a c3 column that eq 2.70 would otherwise never use |
| C7 | FSJ104651 SI Hazus K_u' typo | SI prints `K_u' = K_u (K_u V/10) + …` (extra K_u factor) | `K_u' = K_u V/10 + (1 − V/10)√(((K_d + K_u)/2) K_s)` | Follows the K_d' and K_s' pattern and Qin25 eq 2.73 |
| C8 | Hamada ellipse LB | Qin25 eq 2.80 and FSJ104651 SI: `LB = (V_d + V_u)/V_s`, then the rear-focus ellipse of eqs 2.54-2.57 | **Direct Hamada ellipse** (§4.2) | With V_s the crosswind reach from the ignition point (a half-width), eq 2.80 gives LB = 2 at zero wind after the Hazus correction, i.e. the downwind-elongated fire the correction exists to remove; and the rear-focus projection returns V_d/HB, not V_u, upwind. The direct ellipse returns V_d, V_s and V_u exactly and is a circle at zero wind |
| C9 | f_b parameterisation | FSJ104651 SI / Qin25 eq 2.69 mix two brackets by f_b, with wind only in the f_b term; Hamada's original (HT08 eqs 42-43) instead weights building types (bare wood a′, mortar b′, fire-resistant c′) and has wind in both directions' numerators | **SI form**, f_b = 1 | It is the form the coupled model used; HT08 is cited only for units and as the independent original. With f_b < 1 the (1 − f_b) bracket has no wind term, so spread slows as f_b falls, as intended |
| C10 | Hamada T units | Not stated in FSJ104651 SI or Qin25 | **minutes** | HT08 eqs 42-43 (p.25) give Hamada's rates in m/min |
| C11 | Qin25 0.34 m/s Hamada rate | Qin25 pp.159, 161, 167 vs 0.197 m/s from the published equations | Follow the equations; strict xfail test; open item | §4.5 |
| C12 | Wind height | 6.1 m / 20 ft (FSJ104686 p.3; Qin25 p.78), 10 m (RTMA, FSJ104651), unstated for Hamada | 10 m open wind for Hamada; explicit reduction for embers | §3 |
| C13 | WU-E coefficients PROCI24 does not print | α_c, absorptivity and FTP are not in PROCI24; Qin25 (p.227) attributes defaults to it | IJWF24 baseline (α_c 0.95, absorptivity 0.89, FTP 10,500); α_c 0.5 scenario | Values printed in a peer-reviewed source |
| C14 | Structure firebrand generation | 5.68 → 10 pcs/(MW·s) (Qin25 p.147; FSJ104686 p.4) | **10 [T]**, 5.68 scenario | The tuned value is the one the authors carry forward; both are labelled |
| C15 | Himoto crosswind ember spread | HT08 eq 39: σ_Y/D = 0.92 (author manuscript); LD10 p.682 reproduces the same model with p_Y ~ N(0, 0.092 R_f) | **HT08 (0.92)** | Primary source; LD10's 0.092 is a factor of 10 smaller and is taken as a transcription error. Matters only if the structure-ember stage (§6) uses the crosswind spread |
| C16 | Where the flight distance starts | Qin25 §6.3.1 (p.170): emission from the centre, nothing lands on the emitting footprint; FSJ104686 p.5: 33 % of the first structure's embers land on the second (SS = SSD = 10 m) | **From the source's downwind edge** | Measured from the centroid the 1-D fraction is 20 %; from the downwind edge it is 33.3 % and the published ignition time (2,705 s) is reproduced (2,703 s). Qin25's own Test 6-1 (2,496.9 s to threshold, p.157) implies 31 % |
| C17 | Wind in the Himoto PDF | FSJ104686 p.3 quotes X_max 65 / 84 / 109 m "in a 40-mph wind, u_wind = 17.9 m/s" (the 6.1 m wind of p.3) | **10 m wind** (= 1.15 × 6.1 m) in B* | With 17.9 m/s in B* the published equations give 62 / 80 / 103 m; with 1.15 × 17.9 = 20.6 m/s they give 65.2 / 84.4 / 108.8 m. Qin25 p.72 says the PDFs use the 10 m wind. Inferred, not stated |
| C18 | Wind in the Sardoy PDF | Qin25 p.72: "ambient wind velocity measured at 10-meter elevation"; Qin25 p.124 worked case: μ = 2.18, σ = 1.23 at 6.71 m/s "measured at 6.1 m" | **6.1 m wind** | Only the 6.1 m wind reproduces the worked values (2.215 with the 10 m wind) |
| C19 | Sardoy fireline-intensity unit | FSJ104651 SI: I_f in kW/m; Qin25 eq 4.2: I_B in MW/m | **MW/m** | With kW/m the p.124 case gives μ ≈ 13 (flight distances of e^13 m); MW/m reproduces μ = 2.18 |
| C20 | Small-flame delay | Qin25 eqs 5.2-5.4: random draw each step with P = 0.9 by τ; FSJ104686 p.5 worked example: + 42 s | **+ 42 s, deterministic** | Repeatable runs; 42 s is the time by which P = 0.9 (C4) |

California-tuned or outcome-tuned values, all marked **[T]** wherever they appear: FTP 10,500
kJ/m² (IJWF24, "qualitative" combustibility scale); structure GR' 10 pcs/(MW·s); FSJ104651's 10
embers per step with P = 1 (chosen partly so the ember share came out near 30 %, p.13); the
250 kW/m² vegetation fix; the road-firebreak choice (calibrated with the ember rate, FSJ104651
p.13); the design-fire peak (PROCI24 p.6 sensitivity). The Hamada coefficients are empirical
(Japanese post-earthquake urban fires), not tuned to California, but also not measured for
Canadian construction.

## 8. Validation plan (Jasper 2024)

Published evidence is weak for this purpose:
- PROCI24's "about 70 %" is **recall** only: the share of CAL FIRE DINS damaged/destroyed
  structures inside the simulated burned area (Tubbs 4011/6022 = 67 %, Thomas 774/1009 = 77 %;
  PROCI24 p.4). False positives are not reported.
- FSJ104651 Table 3 (p.12) gives full counts for the Hamada runs: recall 92 / 78 / 97 %,
  **precision 59 / 9 / 77 %** (Tubbs / Thomas / Camp; FireSim's arithmetic from the table).
- The "ember-ignited" share is not validated (FSJ104651 p.7: "this data are currently
  unavailable").

FireSim's plan:
1. **Data** (public-data search 2026-10-09, [R9] in `docs/PROJECT_RECORD.md`):
   - Observed losses: the Municipality of Jasper "Damage Assessment Structures" layer (ArcGIS
     Online, owner MOJ_GIS). It holds one footprint polygon per structure with a rapid-assessment
     `Status`. The townsite subset is 1,119 polygons: 370 Destroyed, 16 Visible Damage and
     733 No Visible Damage. The official figure is 358 destroyed of 1,113 structures (municipal
     after-action review); the difference is not reconciled. **No licence is stated**, so the
     layer is used locally only and never committed.
   - Timing and weather: CFS NOR-X-433 (OGL-Canada 2.0), the municipal after-action review and
     FPInnovations WF TR 2025 n.04.
   - The earlier statement here, "about 1,153 homes" in a structure-level survey, was **not
     confirmed** and is withdrawn. FPInnovations assessed paired and neighbourhood-block samples
     and publishes only figures and summaries. Its per-structure table (roof, spacing, exposure
     mode) would have to be requested.
   - Fort McMurray 2016: the Westhaver 2017 sample only; RMWB would hold building-level data.
2. **Unit of scoring**: each footprint in the run area is a unit; observed = destroyed (and,
   separately, damaged); modelled = involved by the end of the scored period.
3. **Report all of**: confusion counts (TP, FP, FN, TN), **precision**, **recall**, F1 and
   **Cohen's κ** at the structure level; perimeter κ for the wildland part. Never report recall
   alone, and report false positives prominently.
4. **Baselines**: the same counts for (a) building exposure bands alone (e.g. "within 30 m of
   the front") and (b) no structure spread. Structure spread must beat both on κ to be worth
   showing.
5. **No tuning on the test fire**: any parameter changed (cutoff, f_b, design fire) is chosen on
   a different fire or stated in advance; report the sensitivity runs (cutoff 20 / 30 / 45 m;
   contact 5 / 10 / 20 m; 150 / 400 kW/m²). The cutoff, contact and footprint-size runs were
   made on Edmonton footprints on 2026-10-09 without observed data (`scripts/structure_sensitivity.py`,
   reports [R7] and, after the front-contact change, [R8] in `docs/PROJECT_RECORD.md`). The cutoff
   runs were repeated on Jasper (item 6).
6. **First result, Jasper 2024 (2026-10-10, [R10]; `scripts/jasper_structure_validation.py`)**.
   - **Test type:** end-state, structure-only. There is no Jasper fuel grid, so no wildland run
     and no front contact.
   - **Set-up, pre-registered in the script before any status was read:**
     - 15 ember-entry seeds: the 5 units nearest each of the three first-impact areas the
       municipal after-action review names for 18:00 on 24 July 2024;
     - engine defaults: 30 m cutoff, f_b = 1;
     - wind 15 km/h from the SW (NOR-X-433 pp.33-36);
     - window 18:00-24:00;
     - 1,119 townsite footprints as units; Destroyed = positive.
   - **Result:** 396 involved against 370 destroyed. Precision 63 %, recall 68 %, F1 65 %,
     **κ 0.47**.
   - **Baselines:**
     - The count-matched distance band (baseline a: the same number of buildings nearest the
       seeds) scores **κ 0.52**. FireSim − (a) = −0.04, with a 95 % block-bootstrap interval of
       −0.16 to +0.05.
     - No spread (seeds only, baseline b): κ 0.01.
     - The FPInnovations "< 5 m" separation rule, as a percolation from the seeds: κ 0.10.
     - Random at the observed rate: κ ≈ 0.
   - **Item 4's rule is not met.** Given where the embers started fires, Hamada's pattern does
     no better than distance from those points.
   - **Pre-specified sensitivity:**
     - cutoff 20 / 45 m gives 124 / 593 involved buildings (κ 0.19 / 0.51);
     - wind ±25 %, wind from SSW, a 4 h or 12 h window and four alternative seed sets give
       κ 0.34-0.50;
     - FireSim − (a) runs from −0.04 to +0.07, and every interval includes zero.
   - **Not modelled:** ember ignitions after 18:00 and suppression. The latter includes the
     unrecorded demolitions in the business district at 21:30 (a likely source of false
     positives).

7. **Ember ignition at Jasper (2026-10-10, [R16]; `--embers`, `PREREG_EMBERS`, committed
   `ccce4d6` before scoring).** Same units, seeds, wind, window and baselines as item 6; ember
   values from §6.1; no wildland source (structure-only run).
   - **No building was ignited by embers in any of the 17 pre-registered runs** (150 or
     400 kW/m², GR' 5.68 or 10, cutoff 20 / 30 / 45 m, wind × 0.75 / × 1.25, the documented 27 km/h
     gust, and embers without Hamada). Every Hamada + embers run equals its Hamada-only reference
     exactly: κ 0.472 (primary), distance band (a) 0.516, FireSim − (a) −0.044 (−0.157 to +0.045).
   - **The decision rule is not met; embers do not help on this test.** They do not reach the
     destroyed groups 250-500 m from the seeds (100 FN there).
   - Why (model-only diagnostic): at 15 km/h v_air = 0.23 m/s and ψ* = 0.178 g/cm² (~1.4 million
     embers on a median 154 m² footprint) while one such building emits 0.46 million (150 kW/m²) or
     2.4 million (400) in its whole fire; Himoto X_max is 46-57 m. The largest pool on any building
     not otherwise involved was 4.5 % (150) to 14 % (400) of its threshold with Hamada, 9-53 % with
     embers alone. Embers alone ignite a few buildings only at 400 kW/m² and ≥ 40 km/h.
   - Edmonton (`scripts/structure_sensitivity.py --embers`, the [R7]/[R8] sites and days): no
     ember ignition in any of 42 runs (seven ember variants incl. 400 kW/m² and wildland embers
     at 78-82 MW/m peak HFI); counts identical to Hamada alone.
   - The published model is short-range by design (FSJ104686 p.3). The 250-500 m losses need the
     wildland-front ember source of a coupled run (§6.1), longer-range spotting, or are outside this
     model.

## 9. Limits

- Not validated in Canada. The only validations are on three Californian fires with Rothermel,
  LANDFIRE and RTMA inputs, and they are recall-heavy (§8).
- The first Canadian check (Jasper 2024, structure-only, ember entry imposed) did not beat a
  distance-from-ignition baseline. Building-level precision was 55-84 % across the
  pre-specified runs (§8 item 6, [R10]).
- Hamada is a homogeneous-community model applied here pair by pair (§4.3 [H]); a₀ and d are
  per pair, not area averages as Hamada intended (HT08 p.25).
- Hamada has no construction, no topography (FSJ104651 p.17), no suppression, no burnout.
- The neighbour cutoff (30 m) and the front-contact distance (10 m) are FireSim choices, not
  published (confirmed by the owner's delegation 2026-10-09, D2); results depend on them. On
  three Edmonton sites (2026-10-09) involved buildings at 6 h changed by −58 % to +170 % over
  cutoffs 20-45 m [R8] (−48 % to +220 % with the first front-contact rule [R7]).
- Front contact is measured from the building's grid cells (§3 [H]), so it is made at grid
  resolution: on the 50 m grid a building is reached when the front reaches a cell next to one
  its footprint touches, typically 20-40 m and up to ~70 m from the footprint [R8]; with the 20 m
  WUI window (2026-10-10, used when the fire is small enough) typically 9-15 m and up to ~30 m.
  Fires over the window's guards (≈ 2,400 ha of 20 m burned cells) stay at 50 m.
  Counts depend on the resolution: on the live reference request the 20 m run involved 29 units
  (4 front) against 289 (27 front) at 50 m for the same burned area. It no longer
  depends on where a footprint sits inside its cell, but it still depends on which cells a
  footprint touches and on the grid's cell size and origin; `wildland_contact_m` has no effect
  below one cell. The fuel-grid building mask covers only the 4 neighbourhoods nearest the
  ignition; outside them a building's own cells can burn and the same rule applies.
- Wind is the run's 10 m open wind, uniform over the run area; no street canyon or sheltering.
- Ember ignition (opt-in, §6.1) is the published short-range model (≲ 100 m from buildings) with
  one target material (pressure-treated wood, 2 % moisture), no ember cooling, no wind modification
  by buildings, and California-tuned generation (GR' 10 [T]). At the 150 kW/m² default it almost
  never ignites a building (§6.1); at Jasper it ignited none in 17 runs (§8 item 7). With Hamada on
  it double counts embers, which Hamada's empirical rate already includes (Qin25 p.177). The wind
  heights in the transport PDFs are inferred from the published check values (C17, C18), and the
  wildland coupling (Sardoy from 50 m FBP cells at crown-fire intensities) is outside the range the
  correlation was shown for (Qin25 Fig. 6.2: I ≤ 0.5 MW/m).
- Without the ember option, ember ignition, the main WUI loss mechanism, is not in the Hamada stage except implicitly
  through Hamada's empirical rate; yard fuels, sheds, fences and vehicles are not modelled.
- Microsoft footprints: detached garages and sheds may be separate footprints or missing;
  attached buildings may merge into one footprint. Accuracy of the footprints in Edmonton has
  not been assessed. All footprints are kept (D4); dropping those under 40 m² changed involved
  buildings by −8 % to 0 % and left the city-wide median nearest separation at 2.6 m [R7].
- Outputs are counts and times of **modelled involvement**. They are not predictions of which
  buildings burn and must not be used for evacuation tiers or per-building loss.

### Outputs and display (2026-10-10)

- **Counts per frame** (`structure_spread`): units involved by the frame's time, by mechanism
  (front contact, building to building and, with `structure_embers`, `units_ember` /
  `units_ember_from_wildland`), with `label` "illustrative — not validated in Canada".
- **Involved units on the final frame** (`structure_spread_detail`, owner decision 2026-10-10,
  D3 reversed): for each involved unit only its footprint (simplified 0.5 m), involvement time
  (h), mechanism, and the unit that passed the fire on (building to building). No address,
  owner or parcel data. 20-160 kB on the 2026-10-09 sensitivity runs (83-653 units).
- **App** (map display only): Setup → Fuel & landscape "House-to-house spread" (off by default;
  needs the Edmonton grid with buildings). Situation card: counts at the selected time and a
  stacked chart of involved buildings over the run (front contact / building to building) with
  the timeline cursor. Map: involved footprints coloured by mechanism (magenta front contact,
  aqua building to building; outside the fire, fuel, evac and isochrone colour families),
  appearing as the timeline reaches their involvement time; hover or click shows the time,
  mechanism and the label. Legend and card carry an "Illustrative" badge; the full caveat
  (not validated in Canada; modelled involvement, not a prediction of which buildings will
  burn; 30 m neighbour cutoff; front contact on the ~50 m grid) is in an info tooltip on hover
  or keyboard focus. Per-building results are not put in any export, ICS 209 or report.
- **Ember ignition in the app (2026-10-10):** an "Ember ignition" checkbox under House-to-house
  spread (off by default, enabled only with it; sends `structure_embers`, default design fire
  150 kW/m²). When the run had embers the card adds an "Ember ignition" row, the chart a third
  (chartreuse, `--struct-ember`) segment, the map and legend a third mechanism colour, and the
  caveat names the ember model and design fire.
- D3's concern (building-level precision 9-77 % in FSJ104651, §8; a per-house map can read as
  a loss forecast) is handled by labelling, not by hiding: the owner's reasons are that
  aggregated blocks can look more catastrophic than the modelled result and that firefighters
  work a fire house by house, then block by block.

## 10. Build order

1. This specification (docs only).
2. Building-unit layer: `engine/src/firesim/structures/units.py` (§2).
3. Hamada option (§4): `engine/src/firesim/structures/hamada.py`, `spread.py`; API flag
   `structure_spread` (default false), labelled "illustrative — not validated in Canada".
4. WU-E option (§5) with verification gates.
5. FBP coupling refinements: residence, building → wildland, road firebreaks.
6. Firebrand generation and transport (§6). **Built 2026-10-10** (§6.1).
7. Ember ignition (§6). **Built 2026-10-10** (§6.1); first check at Jasper §8 item 7.
8. Verification suite: Qin25 1-D tests, FSJ104686 benchmark (SSD × wind × GR maps, Fig. 6).
9. Canadian validation (§8).
