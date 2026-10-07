# Model card: FireSim

A short, citable statement of what FireSim is for, what evidence supports it, and where it must
not be relied on. Keep it current with every change to the engine or its evidence.

## Summary

FireSim simulates wildfire growth and fire behaviour with the Canadian Forest Fire Behavior
Prediction (FBP) System and reports building exposure, for municipal EOC planning,
preparedness and training. Version: `GET /api/v1/version` returns the deployed git commit,
which identifies the exact model on any output.

## Intended use

- **Now:** preparedness, training, tabletop exercises and what-if planning by EOC Planning
  sections and fire officers: how a fire could grow under given weather, where intensity
  allows which suppression resources, which neighbourhoods and roads could be reached and
  roughly when, and how sensitive this is to the inputs.
- **Later (not yet supported by evidence):** operational fire-growth prediction during
  incidents, once validation against observed fires (below) shows skill comparable to the
  operational Canadian models (Prometheus / WISE).

## Users

EOC Planning section staff (Situation Unit, Plans Chief), emergency management and fire
officers trained in the FBP System. Outputs assume the reader knows what FBP fuel types,
head fire intensity and FWI codes mean.

## Out-of-scope uses

- Deciding evacuation tiers or timing. FireSim reports fire arrival and exposure; the
  decision belongs to the Director of Emergency Management with the Incident Commander.
  FireSim does not suggest tiers.
- Predicting which buildings will ignite or be lost. Exposure is not ignition probability.
- Firefighter or aircraft safety distances. FireSim prints none (earlier unsourced RPAS and
  personnel stand-off rules were removed).
- Replacing the lead agency's prediction (e.g. Alberta Wildfire, Parks Canada) where that
  agency has jurisdiction; compare with it, label which is authoritative.
- Fires dominated by pyroconvection or plume dynamics; FBP spread does not represent them.

## Model

| Part | Method | Source |
|---|---|---|
| Fire behaviour | FBP System, 18 fuel types | ST-X-3 (1992), GLC-X-10 (2009); `cffdrs` |
| Weather / moisture | FWI System; hourly FFMC | Van Wagner & Pickett (1985); Van Wagner (1977) PS-X-69; `cffdrs` |
| Growth (spatial fuel) | Level set advected with the Huygens velocity of each cell's FBP ellipse | Richards (1990); Lautenberger (2013) |
| Growth (uniform fuel) | Huygens wavelets (convex front) | Richards (1990) |
| Crown fire | Van Wagner (1977) initiation; CFB eq 58; C-6 crown rate | ST-X-3 |
| Flame length | Byram (surface), Thomas (CFB >= 0.1) | Alexander & Cruz (2012) |
| Spotting (opt-in) | Albini/Chase/Morris maximum distance; heuristic emission and landing | USDA FS INT reports 1979-1987 |
| Building exposure | Distance bands; Cohen solid-flame radiant flux; flux-time index | Cohen (2004); NRC (2021) |
| Burn probability | Monte Carlo over ignition point, wind speed and RH | (method, not a validated product) |
| Classes | HFI classes 1-6; FWI classes | Cole & Alexander (1995) and CWFIS HFI map; CWFIS FWI map |

Inputs: ignition point or observed perimeter, weather (constant or hourly), FWI codes, FBP
fuel grid (Edmonton: City canopy-LiDAR product), optional DEM, water and building masks,
grass curing, percent conifer / dead fir, foliar moisture or date. Outputs: perimeters,
burned cells with arrival time, speed and head/flank/back, head summary, arrival-time grid,
intensity and fire type, spot fires, building exposure, burn probability.

## Evidence

| Evidence | Result | Reference |
|---|---|---|
| Verification of FBP and FWI equations | Matches CFS `cffdrs` to floating-point precision (12,960 FBP cases; hourly FFMC 5,760 cases) | docs/verification.md §1 |
| Spread models vs the FBP ellipse | Burned area 0.90-1.07 x the FBP ellipse on uniform fuel | docs/verification.md §2 |
| Comparison with WISE (independent Canadian model) | Uniform fuel, flat and sloped: area ratio 0.955-1.09, head distance ratio 0.98-1.05 | docs/verification.md §3 |
| Head / flank / back | Fastest head cell = FBP ROS; rearmost back cell = BROS; head direction within 2° of RAZ incl. slope | engine/tests/spread/test_deployment_data.py |
| Spotting maximum distance | Reproduces published worked examples | engine/tests/spread/test_albini.py |
| Radiant exposure | Reproduces Cohen (2004) worked values within 3.5 % | engine/tests/test_exposure.py |
| **Observed fires** | **None yet.** A validation harness on the Canadian Fire Spread Dataset (Alberta fires) using the Bennett et al. (2026) protocol is in progress; results will be reported here as F1, IoU, Hausdorff distance and area/ROS error, not as a single "accuracy" figure. | (pending) |

For context, published single-day skill of operational FBP growth models on observed fires
is modest (WISE: F1 about 0.26 with default settings, about 0.54 tuned; Bennett et al. 2026),
and rate-of-spread models commonly err by 35-75 % (Cruz & Alexander 2013).

## Known limitations and biases

- Not validated on observed fires (above).
- No suppression is modelled.
- Weather: constant or hourly; DMC and DC fixed within a run; forecast quality is
  Open-Meteo's. Wind direction is the dominant source of error in fire growth models.
- Spotting is illustrative; active crown fire spotting is underestimated.
- Burn probability varies only ignition, wind speed and RH (not a Burn-P3-style analysis).
- Huygens perimeters are convex; use the grid model on heterogeneous fuel.
- Edmonton fuel grid: urban trees are non-fuel; structure-to-structure spread is not modelled.
- Building exposure uses worst-case radiant assumptions and ignores embers, burning buildings
  and yard fuels (usually the main causes of loss).
- WUI zone modifiers in the repository have no source and are off by default.
- Time: America/Edmonton; Alberta moved to permanent UTC-6 on 2026-06-18 (IANA tzdata 2026c).
  Systems with older time zone data show MST after 2026-11-01.

## Responsible use

- State the issuer, run time, model version and inputs with any output shared beyond the
  Planning section; outputs carry the "not validated against observed fires" label.
- Do not present a single run as the expected fire; show its sensitivity to inputs.
- Re-check this card when the engine changes; keep `docs/verification.md` as the record.
