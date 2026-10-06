# Building exposure

What FireSim reports about buildings near a modelled fire, how it is computed, and what it does
not mean. Code: `engine/src/firesim/exposure.py` (model), `spread/cellular.py: flame_emitters`
(flame panels from the grid run), `spread/simulator.py` (per-frame counts). Tests:
`engine/tests/test_exposure.py`.

**These are exposure measures, not ignition predictions.** Most homes lost in Canadian WUI
fires (Fort McMurray 2016, Jasper 2024) were ignited by embers and by burning neighbours, not by
the wildland flame front. Neither is modelled here, so a building with low exposure is not safe.

## When it runs

With the grid (level-set) model and building footprints, i.e. a spatial fuel grid with the
building mask applied (the API does this when `FIRESIM_BUILDINGS_PATH` and the neighbourhood
index are set). With centroids only, distances and flux use the centroid as the target.
The Huygens model (no fuel grid) has no buildings and reports no exposure.

## Outputs

Per frame (`building_exposure`, counts reached by that time):

| Field | Meaning |
|---|---|
| `inside_perimeter` | building centroids inside the outline of the largest burned area (formerly "buildings at risk") |
| `within_10m` ... `within_500m` | buildings whose footprint has come within 10 / 30 / 100 / 500 m of a burned cell edge |
| `flux_over_12_5`, `flux_over_25` | peak radiant flux at the wall >= 12.5 / 25 kW/m2 (NRC construction-class bands) |
| `ftp_reached` | Cohen's flux-time criterion for piloted ignition of wood reached |

Final frame (`building_exposure_detail`, buildings within 500 m): minimum distance, band, time
the fire first came within 30 m and 100 m, peak flux, minutes over 12.5 kW/m2, and the
flux-time index `FTP / 11,501` for both emissive-power scenarios.

Distance bands: <= 10 m flame contact possible (Cohen 2000, ICFME walls ignited only on flame
contact at 10 m); 10-30 m radiant zone (NRC 2021 p.27: "a 30 m distance is often used as the
limit for significant radiative heating"); 30-100 m short-range ember zone; 100-500 m long-range
ember zone.

## Radiant model

Cohen's Structure Ignition Assessment Model (SIAM; Cohen 2004, CJFR 34: 1616-1626):

- Flux at a wall element: `q = F E`, with `F` the view factor from the element to the flame and
  `E` the flame emissive power. `E = sigma T^4` with T = 1200 K and emissivity 1 gives
  117.6 kW/m2 (Cohen's scenario); a second scenario uses 200 kW/m2, the top of the 150-200 kW/m2
  measured for thick crown-fire flames (NRC 2021 p.27). Flux is reported with 117.6; the
  flux-time index is reported for both.
- Flux-time criterion (Cohen 2004 eqs 2-4, after Tran et al. 1992):
  `FTP = integral of (q - 13.1)^1.828 dt`, ignition of wood when FTP >= 11,501 (kW/m2)^1.828 s.
  At a constant 31 kW/m2 that takes 59 s (Cohen: about 60 s).
- Check: a 50 m x 20 m flame at 1200 K, target facing its centre, gives 80.6 / 45.9 / 27.8 kW/m2
  at 10 / 20 / 30 m; Cohen reports 79 / 45 / 27 (tested).

**Flames from the grid run.** Each burned cell is a vertical flame panel, one cell wide, facing
the local spread direction (the arrival-time gradient). Flame height is the flame length from
the cell's fireline intensity: Byram (1959) `0.0775 I^0.46` for surface fire, Thomas (1963)
`0.0266 I^(2/3)` when CFB >= 0.1 (Alexander & Cruz 2012). A panel is present while the front
crosses its cell (cell size / normal spread rate), so one panel carries the moving flame face;
where the front stops (fuel edge, barrier, end of run) it keeps flaming for a residence time of
60 s (NRC p.27: ~30 s; Cohen 2000: 50-70 s; Westhaver 2017: 60-90 s).

**Target.** Points every ~3 m around the footprint (at most 16). Each point faces each panel at
the flame's mid-height (SIAM's centred geometry); the building takes the worst point for each
metric. The summed view factor is capped at 1, so flux never exceeds the emissive power.
Emitters beyond 150 m are ignored.

## Assumptions (state them with any result)

Conservative (raise the numbers):
- blackbody flame at 1200 K, emissivity 1 even for thin surface-fire flames (Cohen's
  assumption holds for flames thicker than ~3 m; surface-fire values are upper bounds);
- no canopy attenuation, no shielding by fences, terrain or other buildings, transmissivity 1;
- each panel seen face-on at its mid-height. SIAM overestimates measured flux (Cohen 2000:
  70 vs 46 kW/m2 at 10 m).

Not conservative (lower the numbers):
- vertical flames (no wind tilt toward downwind buildings);
- no convective heating or direct flame contact beyond the distance band;
- only the modelled wildland front radiates; burning buildings, sheds, fences, vehicles and
  yard fuels, usually the main sources in a WUI fire, are not modelled;
- no embers: the distance bands mark the ember zones but no ember exposure is computed.

Other: cell intensity uses the fire's final weather period (as in the burned-cell display);
distances are from cell edges on the fuel grid, so they are resolved to about half a cell.

## Not included yet

- Ember exposure (a geometric index of crown fire upwind within 100 / 500 m is the next step);
- structure-to-structure separation classes and clusters (needs all footprints, not only those
  near the fire);
- per-building map layer and export.

## Sources

- Cohen, J.D. (2000). Preventing disaster: home ignitability in the wildland-urban interface.
  *J. For.* 98(3): 15-21.
- Cohen, J.D. (2004). Relating flame radiation to home ignition using modeling and experimental
  crown fires. *Can. J. For. Res.* 34: 1616-1626.
- National Research Council Canada (2021). *National Guide for Wildland-Urban Interface Fires.*
- Alexander, M.E., Cruz, M.G. (2012). Interdependencies between flame length and fireline
  intensity in predicting crown fire initiation and crown scorch height. *IJWF* 21: 95-113.
- Tran, H.C. et al. (1992). Wood ignition with radiant heat. In *Fire and Flammability of
  Furnishings and Contents of Buildings*, ASTM STP 1233. (Via Cohen 2004; not checked here.)
