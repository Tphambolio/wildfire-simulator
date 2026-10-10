# FBP System Reference

How FireSim implements the Canadian Forest Fire Behavior Prediction (FBP) System, and how that
implementation is checked. Code: `engine/src/firesim/fbp/` (`calculator.py`, `crown_fire.py`,
`constants.py`).

## Sources

- Forestry Canada Fire Danger Group (1992). *Development and Structure of the Canadian Forest Fire
  Behavior Prediction System.* Information Report ST-X-3. (Equation numbers below refer to it.)
- Wotton, B.M., Alexander, M.E., Taylor, S.W. (2009). *Updates and revisions to the 1992 Canadian
  Forest Fire Behavior Prediction System.* Information Report GLC-X-10.
- Van Wagner, C.E. (1977). Conditions for the start and spread of crown fire. *Can. J. For. Res.*
  7: 23-34.
- Reference implementation: `cffdrs` (CFS; R and Python ports). FireSim's FBP layer is checked
  against the Python port (see Verification).

## Fuel types

All 18 ST-X-3 fuel types (`constants.py`), with Table 6 spread parameters, Table 7 buildup
parameters and Table 8 default crown base height (CBH) and crown fuel load (CFL), plus the
GLC-X-10 revisions (M-4 `c` = 1.48). D-2 spreads at 0.2 x D-1 and not at all below BUI 80
(as in cffdrs, which cites Alexander 2010: "Surface fire spread potential in trembling aspen
during summer in the Boreal Forest Region of Canada", For. Chron. 86(2): 200-212,
doi:10.5558/tfc86200-2; reference as listed in de Groot et al. 2022 and CFS NOR-X-433, the
paper itself not read).

| Code | Name | Code | Name |
|------|------|------|------|
| C1 | Spruce-Lichen Woodland | M1 | Boreal Mixedwood, leafless (needs percent conifer) |
| C2 | Boreal Spruce | M2 | Boreal Mixedwood, green (needs percent conifer) |
| C3 | Mature Jack/Lodgepole Pine | M3 | Dead Balsam Fir Mixedwood, leafless (needs percent dead fir) |
| C4 | Immature Jack/Lodgepole Pine | M4 | Dead Balsam Fir Mixedwood, green (needs percent dead fir) |
| C5 | Red and White Pine | O1a | Matted Grass (needs degree of curing) |
| C6 | Conifer Plantation | O1b | Standing Grass (needs degree of curing) |
| C7 | Ponderosa Pine/Douglas-fir | S1 | Jack/Lodgepole Pine Slash |
| D1 | Leafless Aspen | S2 | White Spruce/Balsam Slash |
| D2 | Green Aspen | S3 | Coastal Cedar/Hemlock/Douglas-fir Slash |

## Calculation chain (`calculate_fbp`)

1. **ISI** from FFMC and wind (eqs 52-53). Above 40 km/h the wind function is
   `12 (1 - exp(-0.0818 (WS - 28)))` (eq 53a). FFMC moisture coefficient 147.2 (eq 46; cffdrs
   uses the exact 147.27723, which moves ISI by at most 0.1 % for a given FFMC; in the daily
   FWI chain the FFMC itself shifts too, by up to 0.12 FFMC / 0.67 ISI / 0.52 FWI, see
   `docs/PROJECT_RECORD.md` §4.1).
2. **BUI** from DMC and DC; **buildup effect** `BE = exp(50 ln(q) (1/BUI - 1/BUI0))` (eq 54) for
   every fuel with q < 1, including D-1 and the M types.
3. **Surface spread**: `RSI = a (1 - exp(-b ISI))^c` (eq 26) with the fuel-specific forms:
   M-1/M-2 percent-conifer blends of C-2 and D-1 (eqs 27-28), M-3/M-4 percent-dead-fir blends,
   grass x curing factor `CF` (GLC-X-10 eqs 35a/35b: `0.005 (exp(0.061 C) - 1)` below 58.8 %,
   `0.176 + 0.02 (C - 58.8)` above, so CF = 1 at full cure); `RSS = RSI x BE`.
4. **Slope** (eqs 39-50): the slope factor `SF = exp(3.533 (GS/100)^1.2)` (10 above 70 %) is
   converted to an equivalent wind speed, added vectorially to the wind, giving the net effective
   wind speed WSV and spread direction RAZ. ISI, LB and back rate all use WSV.
5. **Surface fuel consumption** SFC from BUI (and FFMC for C-1/C-7, percent conifer for M-1/M-2,
   grass fuel load for O-1), eqs 9-25 with the GLC-X-10 C-1 form. Not a constant.
6. **Crown fire**: `CSI = 0.001 CBH^1.5 (460 + 25.9 FMC)^1.5` (eq 56), `RSO = CSI / (300 SFC)`
   (eq 57), `CFB = 1 - exp(-0.23 (ROS - RSO))` (eq 58). Outside C-6 the final ROS equals RSS:
   crowning raises intensity through crown fuel consumption, not speed. C-6 uses its crown
   rate `RSC = 60 (1 - exp(-0.0497 ISI)) FME / 0.778` (eqs 64-66).
7. **Fuel consumption and intensity**: `CFC = CFL x CFB` (x PC/100 or PDF/100 for M types),
   `TFC = SFC + CFC`, `HFI = 300 x TFC x ROS` (eq 69).
8. **Fire ellipse**: `LB = 1 + 8.729 (1 - exp(-0.030 WSV))^2.155` (eq 79); for grass
   `LB = 1.1 WSV^0.464` (GLC-X-10 eq 80). Back rate from the back ISI with wind function
   `exp(-0.05039 WSV)` (eqs 75-76); flank rate `FROS = (ROS + BROS) / (2 LB)` (eq 89).
9. **Foliar moisture** FMC: an explicit value, or eqs 1-8 from latitude, longitude, elevation
   and day of year (minimum 85 % at the seasonal dip, 120 % away from it). The API computes it
   from `fuel_modifiers.day_of_year`; without a date it is 100 %.
10. **Acceleration** from a point ignition (`calculate_acceleration`, `..._at_time`):
    `alpha = 0.115` for C-1, O-1, S and D fuels, else `0.115 - 18.8 CFB^2.5 exp(-8 CFB)`
    (eqs 70-71); `ROS(t) = ROS_eq (1 - exp(-alpha t))` (eq 72); distance eq 73;
    `LB(t) = (LB - 1)(1 - exp(-alpha t)) + 1` (eq 81).

Flame length is not an FBP output. FireSim uses Byram (1959), `L = 0.0775 I^0.46`, for surface
fires and Thomas (1963), `L = 0.0266 I^(2/3)`, when CFB >= 0.1. Using Thomas' relation for crown
fires was suggested by Rothermel (1991), as reported by Alexander & Cruz (2012, IJWF 21: 95-113,
p.99), who also found that none of the methods "seem to work consistently well" against
experimental crown fires, so crown-fire flame lengths are approximate. Both forms are taken from
that review (Byram: their Table 1, p.98).

Fire type: surface (CFB = 0), surface with torching (0 < CFB < 0.1), intermittent / passive
crown (0.1 to 0.9), continuous / active crown (>= 0.9). ST-X-3's classes are surface (< 0.1),
intermittent and crown; the torching label only splits ST-X-3's surface class.

## Inputs a caller can set

`calculate_fbp(fuel, wind_speed, ffmc, dmc, dc, slope, pc, grass_cure, fmc, *, wind_direction,
slope_aspect, pdf, gfl, cbh, cfl)`. Per-cell CBH and CFL (e.g. from drone LiDAR) override the
Table 8 defaults; a CFL <= 0 means "use the default" (cffdrs convention). Through the API these
come from `fuel_modifiers` (curing, grass fuel load, percent conifer, percent dead fir, FMC,
day of year, elevation) and from per-cell canopy layers on the fuel grid.

## Verification

| Check | Where | Result |
|---|---|---|
| All primary and secondary outputs vs cffdrs (Python), 18 fuels x 6 weather/slope cases incl. wind > 40 km/h and slope > 70 % | `engine/tests/fbp/test_cffdrs_reference.py` (fixture regenerated by `engine/tests/fbp/data/generate_cffdrs_reference.py`) | ROS, BROS, FROS, LB, WSV, RAZ, ISI, SFC, CFB, CFC, TFC, HFI equal to float precision |
| Same comparison over 12,960 cases (development check, 2026-10-05) | — | max relative difference 1.5e-15 (ROS), 3e-10 (HFI) |
| Foliar moisture, acceleration, distance and LB at time t vs cffdrs | development check | exact |
| One regression test per defect fixed in 2026-10 (curing, D-1 buildup, SFC, eq 58, C-6, ISI cap, grass LB, back/flank, C-4 q, FMC) | `engine/tests/fbp/test_calculator.py` | pass |

Earlier versions of this file described tests "validated against ST-X-3 tables"; those tests
re-implemented the same formulas as the code and could not detect errors in them. They were
replaced by the cffdrs comparison.

## Known gaps

- Point-ignition acceleration is applied; line ignitions and fires already burning at
  equilibrium (multi-day continuation, RPAS perimeter correction) are treated as established.
- Buildup effect and SFC use the daily BUI. FFMC is hourly only when an hourly weather stream
  is given (hourly FFMC: Van Wagner 1977, Information Report PS-X-69, as in cffdrs `hffmc`);
  otherwise the daily value applies all day.
- Grass fuel load defaults to 0.35 kg/m2 unless set. Grass curing (2026-10-10, decision M1):
  the API and UI default to 95 % only in the pre-green-up window (day of year 60-149, about
  1 March to 29 May; `firesim/fbp/curing.py`) and require a value outside it when O-1 grass can
  burn. The engine's low-level functions and `SimulationConfig` keep a 60 % keyword default
  for direct library use and the `cffdrs` comparison tests; the API never relies on it when
  grass can burn.
