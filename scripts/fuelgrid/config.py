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
