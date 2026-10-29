# M8 parity fixture (v8 decision fires; SEALED for the ML ember-cast project)

**Purpose.** Lets CI check that the engine port of the frozen v8 ember model M8
(`engine/src/firesim/spread/embers_m8.py`) reproduces the v8 reference code
(`scripts/validation/v8/intervals.py`, `run_fire` interval loop and `m8_score`; sha256 `4303e544…4dbe`,
PREREG_LR8 addendum A2.12) to float tolerance. Read by `engine/tests/spread/test_embers_m8_parity.py` only.

**Do not use for anything else.** These are intervals of two v8 *decision* fires. The ML ember-cast
programme (`research/ml-ember-cast`, `docs/ML_EMBER_RECORD.md`) treats the v8 decision fires as its sealed
confirmation set: no ML loader, feature builder, fit or exploratory script may read this folder, and its
contents are not to be passed to any other session. No outcome is stored here: no jump location, sealed
file, `final/` table, rank or score file was read to build it.

**What is in it.** One compressed `.npz` per interval (two intervals per fire) and `manifest.json`:

| File | Fire (open record) | Region | v8 epoch | Grid (125 m cells) | Active source cells | 375 m candidates | Bytes | sha256 |
|---|---|---|---|---|---|---|---|---|
| `fire1_interval1.npz` | NBAC:2018_780 | CAN | 2 | 333 × 375 | 47 | 5,056 | 129,750 | `97c5fe3b8f395754d20b2df89f546fae111e648c99bf2adf6ae6bf3986087c5b` |
| `fire1_interval2.npz` | NBAC:2018_780 | CAN | 4 | 333 × 375 | 50 | 5,480 | 134,387 | `ed5d4a12631a215abe4a837e2bfabf2ae628ece1fd6f4131bbe57493a50f7249` |
| `fire2_interval1.npz` | MTBS:AZ3368910927620210616 | US | 2 | 327 × 339 | 79 | 6,945 | 265,757 | `ea2b35404eb2fccb3863523fe229ed94d60335c452f5cd67b1e15120f929f2c7` |
| `fire2_interval2.npz` | MTBS:AZ3368910927620210616 | US | 4 | 327 × 339 | 165 | 7,165 | 267,692 | `e370fbdbe8de9ec13d2ddd7d502ae6969397c14615fff5e55c9b1ed6da41b16c` |

`manifest.json` sha256 `069972518e6b89203b1dc7f494b7d5dab172eef4ab9a77585e3359f02f340e87` (lists the same
hashes; the test checks each `.npz` against it).

Arrays per file (only what the test reads):

- `closed`, `act`, `burnable` (bit-packed booleans, `shape`): the start perimeter of the interval (VIIRS
  detections of earlier epochs dilated 375 m and closed 1 km, PREREG_LR8 §4), its active edge cells (a
  nominal/high detection within 1 km in the 12 h before the interval start, §4 / OD12), burnable cells.
- `ros` (m/min, per active cell in `np.nonzero(act)` order): distance to the previous start perimeter over the
  time between them, floor 0.5 (v8 intervals.py).
- `names` (FBP type index, `embers_m8.TYPES`), `pcs` (percent conifer / dead fir), `wcls` (WAF bed-wind
  class), `gcur` (grass curing on grass cells, else 1), `zone_grid`.
- `z_u10`, `z_wd_from`, `z_ffmc`, `z_bui` (zone weather over the interval, v8 `zone_weather`), `hours`,
  `fmc`, `cell`.
- `v8_*`: the interval file the v8 decision run wrote on 2026-10-25 (`intervals/<epoch>.npz`: candidate
  `bj`/`bi`, landing statistics `A`, `zone`, `u10`, `m_ff`).

**Provenance.** Built 2026-10-29 by `scripts/validation/v8_m8_parity_fixture.py` (run under `capjob`) from
`validation-data/v8-pool/fires/<fire>/` (outside the repo): `inputs.npz` (CFS FBP 2024 + SCANFI v2 back-cast
in Canada, LANDFIRE FBFM40 in the US; MODIS MOD13Q1 NDVI curing), `wx_<station>.csv` (IEM ASOS + ERA5
precipitation, A2.1), and the per-fire VIIRS 375 m detections (NASA FIRMS, S-NPP + NOAA-20), using the v8
code's own functions. The builder fed the re-derived inputs through the engine port and compared with the
stored v8 interval file: A identical (max relative difference 0) on all four intervals. Data are open:
NASA FIRMS (VIIRS), NBAC (OGL-Canada), MTBS (public domain), CFS/SCANFI (OGL-Canada), LANDFIRE (public domain),
MODIS (NASA open), IEM ASOS, ERA5 via Open-Meteo.

**References.** `docs/PREREG_LR8.md` (`6452c1d`, addenda A1 `e0df434`, A2 `9fe3a0f`, A3 `1c67168`, A4
`1b55caf`); [R70] *Long-range embers v8 result 2026-10-29*.
