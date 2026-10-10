"""Configuration for the open-data FBP fuel-grid pipeline: paths, grids, sources and codes.

Everything downloaded or produced lives under ``FUELGRID_DATA`` (default
``~/dev/wildfire/fuelgrid-data``), outside the repository. The City of Edmonton LiDAR crown
tables (validation labels only) are read from ``FUELGRID_LIDAR_DIR`` and never copied into the
repository; only aggregate statistics derived from them are reported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PIPELINE_VERSION = "1.0.0"

REPO = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("FUELGRID_DATA", Path.home() / "dev/wildfire/fuelgrid-data"))
RAW = DATA / "raw"
REGION_DIR = DATA / "region"
LABEL_DIR = DATA / "labels"
OUT_DIR = DATA / "out"
VAL_DIR = DATA / "validation"

# City of Edmonton LiDAR crown tables (read-only; validation labels for Edmonton only)
LIDAR_DIR = Path(os.environ.get(
    "FUELGRID_LIDAR_DIR",
    Path.home() / "Documents/Work/City-of-Edmonton/Urban-Forestry/LiDAR/tree_results"))
# The City LiDAR fuel grid already in FireSim (reference for validation)
REFERENCE_GRID = REPO / "data" / "Edmonton_FBP_FuelLayer_20251105_10m.tif"

USER_AGENT = "FireSim-fuelgrid/1.0 (+https://github.com/Tphambolio/wildfire-simulator)"

# ── Grid ──────────────────────────────────────────────────────────────────────
CRS = "EPSG:3776"  # NAD83 / Alberta 3TM ref merid 114 W (same as the City LiDAR grid)
CELL = 20.0        # metres

# Edmonton reference grid (City LiDAR grid) extent, EPSG:3776
EDMONTON_BOUNDS = (18940.0, 5911640.0, 48160.0, 5953940.0)
# St. Albert: OSM boundary plus this buffer
ST_ALBERT_BUFFER_M = 3000.0


@dataclass(frozen=True)
class Aoi:
    key: str
    name: str
    osm_relation: int          # OSM administrative boundary relation (via Nominatim lookup)
    buffer_m: float            # buffer around the boundary included in the output
    fixed_bounds: tuple[float, float, float, float] | None = None  # snap output to these bounds


AOIS: dict[str, Aoi] = {
    "edmonton": Aoi("edmonton", "Edmonton", 2564500, 0.0, EDMONTON_BOUNDS),
    "st_albert": Aoi("st_albert", "St. Albert", 11890349, ST_ALBERT_BUFFER_M),
}

# Region grid covering every AOI (inputs are fetched once onto this grid). Rounded to 20 m;
# the Edmonton window coincides cell-for-cell with the City LiDAR grid.
REGION_BOUNDS = (14940.0, 5911640.0, 48160.0, 5953940.0)

# ── Sources (id → provenance). Every input is open; versions are pinned. ─────
SOURCES: dict[str, dict] = {
    "osm_boundary": {
        "title": "OpenStreetMap administrative boundaries (Edmonton R2564500, St. Albert R11890349) "
                 "via the Nominatim lookup API",
        "version": "retrieved at run time (date in provenance.json)",
        "licence": "ODbL 1.0 (© OpenStreetMap contributors)",
        "url": "https://nominatim.openstreetmap.org/lookup",
    },
    "osm_landuse": {
        "title": "OpenStreetMap Alberta extract (Geofabrik), multipolygons: landuse / natural / leisure",
        "version": "alberta-latest.osm.pbf (Last-Modified in provenance.json)",
        "licence": "ODbL 1.0 (© OpenStreetMap contributors)",
        "url": "https://download.geofabrik.de/north-america/canada/alberta-latest.osm.pbf",
    },
    "worldcover": {
        "title": "ESA WorldCover 10 m 2021 v200, tile N51W114",
        "version": "v200 (2021 imagery)",
        "licence": "CC BY 4.0",
        "url": "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
               "ESA_WorldCover_10m_2021_v200_N51W114_Map.tif",
        "citation": "Zanaga et al. 2022, ESA WorldCover 10 m 2021 v200, doi:10.5281/zenodo.7254221",
    },
    "meta_chm": {
        "title": "Meta / WRI High Resolution 1 m Global Canopy Height Map (alsgedi_global_v6_float), "
                 "tiles 021211312 and 021211310",
        "version": "v1 (2024 release; imagery mostly 2018-2020, per-tile dates in metadata/)",
        "licence": "CC BY 4.0",
        "url": "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/chm/",
        "citation": "Tolan et al. 2024, Remote Sensing of Environment 300:113888",
    },
    "scanfi_v2": {
        "title": "SCANFI v2 (CFS) 2025: total crown closure, height, species crown closure",
        "version": "v2_20260119, year 2025",
        "licence": "Open Government Licence – Canada",
        "url": "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/",
        "citation": "Guindon et al. 2026, doi:10.23687/07653869-f303-46c2-a04e-9ab479b73cbf",
    },
    "scanfi_v3": {
        "title": "SCANFI v3 (CFS) 2025 fractional land cover: treed coniferous / treed broadleaf",
        "version": "v3_20260528, year 2025",
        "licence": "Open Government Licence – Canada",
        "url": "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v3/",
    },
    "sentinel2": {
        "title": "Sentinel-2 L2A surface reflectance (Copernicus), COGs via Element84 Earth Search STAC",
        "version": "collection sentinel-2-l2a; scene list in provenance.json",
        "licence": "Copernicus Sentinel data terms (free, full and open)",
        "url": "https://earth-search.aws.element84.com/v1",
    },
    "aci": {
        "title": "AAFC Annual Crop Inventory 2025 (30 m) via the AAFC ImageServer exportImage",
        "version": "2025",
        "licence": "Open Government Licence – Canada",
        "url": "https://agriculture.canada.ca/imagery-images/rest/services/annual_crop_inventory/2025/ImageServer",
        "record": "https://open.canada.ca/data/en/dataset/89073037-d2fc-4a6d-9ce9-f0b1bcb48745",
    },
    "ms_buildings": {
        "title": "Microsoft Global ML Building Footprints, RegionName=Canada, quadkey 021211312",
        "version": "release 2026-02-03 (uploaded 2026-02-23)",
        "licence": "ODbL 1.0",
        "url": "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv",
    },
    "cfs_fbp_2026": {
        "title": "CFS Canadian National FBP Fuel Type Layer – Current Year, 2026, 30 m (comparison only)",
        "version": "FBP_FuelTypes_Canada_2026_30m.tif",
        "licence": "Open Government Licence – Canada",
        "url": "https://ca.nfis.org/fss/fss?command=retrieveByName&fileName=FBP_FuelTypes_Canada_2026_30m.tif"
               "&fileNamespace=fire_behaviour_prediction",
        "record": "https://open.canada.ca/data/en/dataset/851e6a27-a250-41e6-9cd0-d7ff96455dd6",
    },
}

# Sentinel-2 composite windows (inclusive dates). Leaf-off = snow-free, before aspen bud-flush
# (spring) or after leaf fall (autumn); leaf-on = midsummer.
S2_SEASONS: dict[str, list[tuple[str, str]]] = {
    "spring": [("2023-04-12", "2023-05-03"), ("2024-04-12", "2024-05-03"),
               ("2025-04-12", "2025-05-03"), ("2026-04-12", "2026-05-03")],
    "autumn": [("2023-10-18", "2023-11-08"), ("2024-10-18", "2024-11-08"),
               ("2025-10-18", "2025-11-08")],
    "summer": [("2024-07-01", "2024-08-31"), ("2025-07-01", "2025-08-31")],
    # Snow-on winter: snow under bare aspen is bright, snow-shedding spruce crowns stay dark
    "winter": [("2023-02-10", "2023-03-20"), ("2024-02-10", "2024-03-20"),
               ("2025-02-10", "2025-03-20"), ("2026-02-10", "2026-03-20")],
}
S2_MAX_CLOUD = 20.0
S2_BANDS = ["blue", "green", "red", "rededge1", "nir", "swir16", "swir22"]
S2_VALID_SCL = (4, 5, 6)  # vegetation, not vegetated, water
S2_VALID_SCL_WINTER = (4, 5, 6, 11)  # + snow (the evergreen signal in winter is canopy over snow)

# ── Internal fuel classes and output code schemes ────────────────────────────
NODATA, C2, D2, M2, D1, M1, O1A, O1B, NF, WATER = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
CLASS_NAMES = {NODATA: "nodata", C2: "C-2", D2: "D-2", M2: "M-2", D1: "D-1", M1: "M-1",
               O1A: "O-1a", O1B: "O-1b", NF: "NF", WATER: "Water"}

# FireSim `canopy_lidar` scheme (same codes as the City LiDAR grid; auto-detected by the loader).
# It has no leafless codes, so only the leaf-on grid is written in it.
CANOPY_LIDAR = {NODATA: 0, C2: 2, D2: 12, M2: 14, O1A: 31, O1B: 32, NF: 99, WATER: 98}
# FireSim `cfs_national` scheme (CIFFC codes; load with code_scheme="cfs_national").
# M-1 / M-2 carry percent conifer in the code: 4xx = M-1, 5xx = M-2 with xx % conifer.
CIFFC = {NODATA: 0, C2: 2, D1: 11, D2: 12, O1A: 31, O1B: 32, NF: 101, WATER: 102}
CIFFC_M1_BASE, CIFFC_M2_BASE = 400, 500

# Rule identifiers written to the provenance raster (one per decision-key branch)
RULES = {
    0: "outside AOI",
    1: "water (WorldCover water >= 50 % of cell)",
    2: "treed stand, typed by conifer fraction",
    3: "treed stand, open cell (canopy < 10 %) -> O-1b",
    4: "OSM natural open vegetation (scrub/heath/grassland/wetland/meadow) -> O-1b",
    5: "built-up or maintained urban context -> non-fuel",
    6: "AAFC crop or pasture/forage -> O-1a",
    7: "natural grass / shrub / wetland (AAFC or WorldCover) -> O-1b",
    8: "other (bare, unclassified) -> non-fuel",
}

# AAFC ACI classes (https://agriculture.canada.ca/atlas/data_donnees/annualCropInventory/...)
ACI_WATER = {20}
ACI_URBAN = {34, 35}
ACI_NATURAL_OPEN = {50, 80, 85, 110}          # shrubland, wetland, peatland, grassland
ACI_PASTURE = {122}                            # pasture / forages (managed hay)
ACI_FOREST = {200, 210, 220, 230}
# Annual and perennial crops (120 agriculture undifferentiated, 130-199 crops); 122 handled above
ACI_CROP = {120} | set(range(130, 200))

# WorldCover classes
WC_CLASSES = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare",
              70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}
WC_BANDS = [10, 20, 30, 40, 50, 60, 80, 90, 100]

# OSM categories (raster values; larger value = higher priority when polygons overlap)
OSM_NONE, OSM_FARMLAND, OSM_URBAN, OSM_MAINTAINED, OSM_NATURAL_OPEN, OSM_WOOD, OSM_WATER = range(7)
OSM_RULES = {
    OSM_FARMLAND: {"landuse": {"farmland", "orchard", "vineyard"}},
    OSM_URBAN: {"landuse": {"residential", "retail", "industrial", "commercial", "construction",
                            "religious", "brownfield", "railway", "education", "military", "garages",
                            "civic_admin", "civic", "landfill", "quarry", "farmyard", "depot",
                            "institutional", "greenhouse_horticulture", "port"},
                "amenity": {"parking", "school", "university", "college", "hospital"}},
    OSM_MAINTAINED: {"landuse": {"grass", "recreation_ground", "cemetery", "flowerbed",
                                 "village_green", "allotments", "plant_nursery", "greenfield"},
                     "leisure": {"park", "pitch", "playground", "garden", "golf_course",
                                 "sports_centre", "track", "dog_park", "stadium", "common",
                                 "ice_rink", "swimming_pool", "water_park", "horse_riding"}},
    OSM_NATURAL_OPEN: {"natural": {"scrub", "heath", "grassland", "wetland", "grass"},
                       "landuse": {"meadow"}},
    OSM_WOOD: {"natural": {"wood"}, "landuse": {"forest"}},
    OSM_WATER: {"natural": {"water"}, "landuse": {"reservoir", "basin"}},
}

# ── Decision-key parameters (fixed a priori, before the Edmonton comparison) ──
PARAMS = {
    "chm_tree_height_m": 5.0,    # canopy = CHM >= 5 m (tree definition; excludes 2-5 m shrubs)
    "stand_cover": 0.40,         # stand = 3x3 mean canopy >= 40 % (VRI-style closed/open cut)
    "stand_min_ha": 1.0,         # contiguous patch >= 1 ha
    "treed_cell_cover": 0.10,    # < 10 % canopy inside a stand = open (VRI "non-treed" < 10 %)
    "built_cell_frac": 0.02,     # cell with >= 2 % building footprint is built
    "built_3x3_frac": 0.10,      # or >= 10 % footprint in its 60 m neighbourhood
    "wc_built_frac": 0.50,       # or WorldCover built-up >= 50 %
    "water_frac": 0.50,          # WorldCover water >= 50 % -> water
    "conifer_c": 0.75,           # conifer fraction >= 0.75 -> C-2 (same as the LiDAR grid)
    "conifer_d": 0.25,           # conifer fraction <= 0.25 -> D-2
    "block_m": 2000.0,           # spatial hold-out block size for the conifer model
    "n_folds": 5,
    "seed": 20261010,
}

# Seasonal switch (see docs/fuel-grid-open-data.md): leaf-off before green-up and after leaf fall.
# Same day-of-year defaults as FireSim's validation harness (greenup_doy / leafoff_doy).
GREENUP_DOY = 150
LEAFOFF_DOY = 258


# ══ Version 2: full FBP decision key + disturbance (scripts/fuelgrid/key2.py) ══════════════
# Nothing above this line changes for v2, so the v1 grids (v20261010) rebuild bit for bit.
# v2 outputs carry the product name ``fbp_opendata2`` and go to OUT_DIR / "v2".
PIPELINE_VERSION_V2 = "2.0.0"
OUT_DIR_V2 = OUT_DIR / "v2"
VAL_DIR_V2 = VAL_DIR / "v2"

# New internal classes (v1 classes 0-9 keep their values)
C1, C3, C4, C7, S1, S2 = 10, 11, 12, 13, 14, 15
CLASS_NAMES_V2 = {**CLASS_NAMES, C1: "C-1", C3: "C-3", C4: "C-4", C7: "C-7", S1: "S-1", S2: "S-2"}
FOREST_V2 = (C1, C2, C3, C4, C7, D1, D2, M1, M2)
# CIFFC codes for v2 (Appendix A of NRCan 2026; same table as firesim CFS_NATIONAL_CODES)
CIFFC_V2 = {**CIFFC, C1: 1, C3: 3, C4: 4, C7: 7, S1: 21, S2: 22}

# Rule identifiers of the v2 key (provenance raster). 1 and 3-8 keep their v1 meaning; v1 rule 2
# (treed stand typed by conifer fraction) is split into 20-26.
RULES_V2 = {
    0: "outside AOI",
    1: "water (WorldCover water >= 50 % of cell)",
    3: "treed stand, open cell (canopy < 10 %) -> O-1b",
    4: "OSM natural open vegetation (scrub/heath/grassland/wetland/meadow) -> O-1b",
    5: "built-up or maintained urban context -> non-fuel",
    6: "AAFC crop or pasture/forage -> O-1a",
    7: "natural grass / shrub (AAFC or WorldCover), treed land outside stands -> O-1b",
    8: "other (bare, unclassified) -> non-fuel",
    20: "stand, conifer >= 75 %, spruce / other conifer -> C-2",
    21: "stand, conifer, black spruce >= 75 % + lichen >= 1 % + closure < 60 % -> C-1",
    22: "stand, conifer, pine >= 25 %, height < 12 m and cover >= 60 % -> C-4",
    23: "stand, conifer, pine >= 25 %, otherwise -> C-3",
    24: "stand, conifer, ponderosa pine / open Douglas-fir -> C-7",
    25: "stand, conifer <= 25 % -> D-2 (leaf-off D-1)",
    26: "stand, mixedwood -> M-2 (leaf-off M-1), percent conifer",
    60: "SCANFI-treed stand (test areas), spruce / other conifer -> C-2",
    61: "SCANFI-treed stand, black spruce + lichen + closure < 60 % -> C-1",
    62: "SCANFI-treed stand, pine, height < 12 m and closure >= 60 % -> C-4",
    63: "SCANFI-treed stand, pine, otherwise -> C-3",
    64: "SCANFI-treed stand, ponderosa pine / open Douglas-fir -> C-7",
    65: "SCANFI-treed stand, conifer <= 25 % -> D-2 (leaf-off D-1)",
    66: "SCANFI-treed stand, mixedwood -> M-2 (leaf-off M-1)",
    30: "open conifer outside stands (sparse / treed peatland), black spruce + lichen -> C-1",
    31: "open conifer outside stands, otherwise -> M-1/M-2 25 % conifer",
    32: "herbaceous wetland / peatland (WorldCover 90+100 >= 50 % or AAFC 80/85) -> O-1b",
    40: "burned 1-2 years before the map year -> non-fuel",
    41: "burned 3-5 years before -> D-2 (leaf-off D-1)",
    42: "burned 6-21 years before -> M-2 25 % conifer (leaf-off M-1)",
    50: "harvested 1-5 years before, pine-leading -> S-1",
    51: "harvested 1-5 years before, spruce / fir / mixed -> S-2",
    52: "harvested 1-5 years before, deciduous-leading -> D-2 (leaf-off D-1)",
    53: "harvested 6-24 years before -> O-1b",
}

# Decision-key parameters of v2 (all fixed before any comparison; sources in the docs, §4b)
PARAMS_V2 = {
    # Decision unit: forest is typed on 100 m blocks (5 x 5 cells) of its 20 m extent, the scale at
    # which the labels agree (split-half kappa 0.72 vs 0.33 at 20 m) and provinces map fuels
    # (conifer-mapping report 2026-10-10, §2.1, §4; SCANFI p.26)
    "unit_cells": 5,
    "unit_min_crowns": 50,        # units used to fit / score the unit-scale re-map (report §2.2)
    "conifer_c": 0.75,            # CFS 2018 p.1 / CFS 2026 §2.3: > 75 % needleleaf = coniferous
    "conifer_d": 0.25,            # <= 25 % conifer = D-2, kept (not low-PC M-2) [H], docs §4b
    "pine_share": 0.25,           # CFS 2026 rules 4.e / 4.f (> 25 % jack or lodgepole pine)
    "c4_height_m": 12.0,          # CFS 2026 rules 4.e / 4.f (height < 12 m = immature)
    "c4_cover": 0.60,             # [H] Perrakis et al. 2015 p.19 "dense (> 60 % crown closure)"
    "c1_black_spruce": 0.75,      # CFS 2026 rule 2.d (>= 75 % black spruce)
    "c1_lichen": 0.01,            # CFS 2026 rule 2.d (lichen >= 1 %)
    "c1_closure": 0.60,           # CFS 2026 rule 2.d (crown closure < 60 %)
    "c7_ponderosa": 0.30,         # CFS 2026 rule 4.h.i
    "c7_ponderosa_mixed": 0.20,   # CFS 2026 rule 4.h.ii (with Douglas-fir >= 25 %)
    "c7_douglas_fir": 0.25,
    "c7_df_open": 0.20,           # CFS 2026 rule 4.i / CFS 2018 rule 36: Douglas-fir, closure <= 40 %
    "c7_df_open_closure": 0.40,
    "open_conifer_cover2": 0.10,  # [H] >= 10 % cover of 2 m+ vegetation = treed (VRI "treed" >= 10 %)
    "open_conifer_treed": 0.25,   # [H] SCANFI v3 treed-coniferous fraction of the cell
    "open_conifer_ratio": 3.0,    # [H] coniferous >= 3 x broadleaf (>= 75 % conifer)
    "open_conifer_pc": 25,        # CFS 2026 rule 1.e: coniferous, crown closure < 30 % -> M1-25 %
    "wetland_frac": 0.50,         # WorldCover herbaceous wetland + moss/lichen >= 50 %
    "fire_nf_years": 2,           # CFS 2026 Table 3 (p.17): 1-2 years non-fuel
    "fire_d_years": 5,            # 3-5 years D-2
    "fire_m_years": 21,           # 6-21 years M-2 25 %; > 21 years: the vegetation inputs decide [H]
    "fire_m_pc": 25,
    "slash_years": 5,             # Perrakis et al. 2015 §5.4.3 p.24: slash for the first 5 years
    "harvest_open_years": 24,     # Perrakis et al. 2015 App. A4 p.55: 7-24 years -> O-1a/b (dry zones)
    "slash_pine_share": 0.50,     # [H] pine >= 50 % of the pre-harvest conifer -> S-1, else S-2
    "fire_harvest_window": 1,     # [H] forest loss within +-1 year of a burn, inside it = fire
    "harvest_pre_closure": 0.25,  # [H] a cut block must have been treed (SCANFI closure >= 25 %) before
}

# Northern Alberta test areas: CFSDS fires of the validation set (docs/validation.md). Bounds are
# the fire's CFSDS day-of-burning extent (lon/lat) plus TEST_BUFFER_M. Maps are built for the
# fire year with inputs dated before it (pre-fire), so a fire cannot leak into its own fuel map.
TEST_CRS = "EPSG:3400"  # NAD83 / Alberta 10-TM (Forest), the provincial standard
MAP_YEAR_CAPITAL = 2026  # Edmonton / St. Albert v2 maps: current (inputs dated before 2026)
TEST_BUFFER_M = 5000.0


@dataclass(frozen=True)
class TestArea:
    key: str
    fire_id: str
    year: int                                       # map year = fire year (inputs dated before it)
    fire_lonlat: tuple[float, float, float, float]  # CFSDS burned extent (lon0, lat0, lon1, lat1)
    region: str                                     # region grid the inputs are fetched onto


TEST_AREAS: dict[str, TestArea] = {
    "fire_2023_689": TestArea("fire_2023_689", "2023_689", 2023, (-114.838, 57.683, -114.530, 57.866),
                              "ab_n57w115"),
    "fire_2024_126": TestArea("fire_2024_126", "2024_126", 2024, (-114.977, 57.639, -114.663, 57.759),
                              "ab_n57w115"),
    "fire_2024_152": TestArea("fire_2024_152", "2024_152", 2024, (-110.313, 56.224, -110.065, 56.405),
                              "ab_n56w110"),
}
# SCANFI v2 is published every 5 years; v3 land cover / fractions every year.
SCANFI_V2_EPOCHS = (1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025)

SOURCES_V2: dict[str, dict] = {
    "scanfi_v3_annual": {
        "title": "SCANFI v3 (CFS) annual fractional cover (incl. nonTreed_lichen) and land cover",
        "version": "v3_20260528, year = map year - 1",
        "licence": "Open Government Licence – Canada",
        "url": "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v3/",
    },
    "scanfi_v2_epoch": {
        "title": "SCANFI v2 (CFS) crown closure, height, age and species crown closure, 5-year epochs",
        "version": "v2_20260119, latest epoch before the map year",
        "licence": "Open Government Licence – Canada",
        "url": "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/",
        "citation": "Guindon et al. 2026, doi:10.23687/07653869-f303-46c2-a04e-9ab479b73cbf",
    },
    "hansen_gfc": {
        "title": "Hansen et al. Global Forest Change 2000-2024 v1.12, lossyear, tile 60N_120W",
        "version": "GFC-2024-v1.12",
        "licence": "CC BY 4.0",
        "url": "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/"
               "Hansen_GFC-2024-v1.12_lossyear_60N_120W.tif",
        "citation": "Hansen et al. 2013, Science 342:850",
    },
    "ab_fire_perimeters": {
        "title": "Alberta Wildfire historical wildfire perimeters 1931-2025 (BURNCODE B*; PB* excluded)",
        "version": "fp-historical-wildfire-perimeter-data.zip (shapefile dated 2026-02-03)",
        "licence": "Open Government Licence – Alberta (open.alberta.ca)",
        "url": "https://geospatial.alberta.ca/titan/rest/services/wildfire/historical_wildfires_data/FeatureServer",
    },
    "avi_crown": {
        "title": "Alberta Vegetation Inventory (AVI) Crown: AVI_Crown + AVI_PostInventoryHarvest (comparison only)",
        "version": "AlbertaVegetationInventoryCrown.gdb (2022 release)",
        "licence": "Open Government Licence – Alberta",
        "url": "https://extranet.gov.ab.ca/srd/geodiscover/srd_pub/biota/AlbertaVegetationInventoryCrown.zip",
    },
    "cfs_fbp_2024": {
        "title": "CFS national FBP fuel type layer 2024, 100 m (comparison only; dated before the 2024 fires)",
        "version": "FBP_fueltypes_Canada_100m_EPSG3978_20240527.tif",
        "licence": "Open Government Licence – Canada",
        "url": "https://cwfis.cfs.nrcan.gc.ca/downloads/fuels/",
    },
}

# Inputs shared with other agents' downloads (read-only; never copied into the repo)
REFERENCE_LAYERS = Path(os.environ.get("FUELGRID_REFERENCE_LAYERS",
                                       Path.home() / "dev/wildfire/reference-layers"))
AB_PERIMETERS = Path(os.environ.get(
    "FUELGRID_AB_PERIMETERS", REFERENCE_LAYERS / "downloads/fp-historical-wildfire-perimeter-data.zip"))
AVI_GDB = REFERENCE_LAYERS / "AlbertaVegetationInventoryCrown/Data/10TM_Offset/AlbertaVegetationInventoryCrown.gdb"
CFS_FBP_2024 = (Path(os.environ.get("FIRESIM_VALIDATION_DATA", Path.home() / "dev/wildfire/validation-data"))
                / "fuels/FBP_fueltypes_Canada_100m_EPSG3978_20240527.tif")
