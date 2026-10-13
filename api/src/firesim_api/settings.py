"""Runtime settings loaded from environment variables.

All paths are optional — the API falls back gracefully when not set.
Set these in the deployment environment or a local .env file.

Example .env:
    FIRESIM_FUEL_GRID_PATH=/data/fuel_maps/Edmonton_FBP_FuelLayer_20251105_10m.tif
    FIRESIM_WATER_PATH=/data/edmonton_water_bodies.geojson
    FIRESIM_BUILDINGS_PATH=/data/edmonton_buildings.geojson
"""

from __future__ import annotations

import os


class Settings:
    """Environment-backed settings for the FireSim API."""

    @property
    def fuel_grid_path(self) -> str | None:
        """Default GeoTIFF fuel raster to use when use_ca_mode=True.

        When set and the file exists, the real spatial grid is loaded instead
        of generating a synthetic landscape around the ignition point.
        """
        return os.environ.get("FIRESIM_FUEL_GRID_PATH")

    @property
    def water_path(self) -> str | None:
        """Default water bodies GeoJSON for non-fuel masking."""
        return os.environ.get("FIRESIM_WATER_PATH")

    @property
    def buildings_path(self) -> str | None:
        """Default building footprints GeoJSON for non-fuel masking."""
        return os.environ.get("FIRESIM_BUILDINGS_PATH")

    @property
    def ab_buildings_path(self) -> str | None:
        """Alberta building footprints (FlatGeobuf with a spatial index; Microsoft Canadian
        Building Footprints, ODbL, with municipal footprints from the ODB where licensed in
        the 2026-10 file), read by window only. When set and the file exists, a
        request naming the Edmonton buildings file reads this file instead: Edmonton runs get the same buildings as before and
        runs elsewhere in Alberta get buildings too (docs/buildings-alberta.md). Unset or
        missing: the Edmonton file only."""
        return os.environ.get("FIRESIM_AB_BUILDINGS_PATH") or None

    @property
    def city_buildings_path(self) -> str:
        """The bundled Edmonton buildings file that ``ab_buildings_path`` stands in for."""
        return os.environ.get("FIRESIM_CITY_BUILDINGS_PATH",
                              "/app/data/edmonton_buildings.geojson.gz")

    @property
    def edmonton_box_legacy(self) -> bool:
        """``FIRESIM_EDMONTON_BOX_LEGACY=1`` restores the behaviour before 2026-10-12 for
        Edmonton runs (structure spread limited to buildings in the former extract's box;
        neighbourhoods get no buildings south of 53.39 N). Default off: the box is dropped
        for structure spread and the City's own neighbourhoods keep all their buildings
        (docs/buildings-alberta.md §6). Kept for equivalence checks."""
        return os.environ.get("FIRESIM_EDMONTON_BOX_LEGACY", "").lower() in ("1", "true", "yes")

    @property
    def buildings_max_near(self) -> int:
        """Most buildings selected around an ignition outside Edmonton (building mask,
        exposure and inside-perimeter counts; default 30,000)."""
        return int(os.environ.get("FIRESIM_BUILDINGS_MAX_NEAR", "30000"))

    def _sibling_of_buildings(self, env: str, name: str) -> str | None:
        v = os.environ.get(env)
        if v:
            return v
        b = self.buildings_path
        if b:
            p = os.path.join(os.path.dirname(b), name)
            if os.path.exists(p):
                return p
        return None

    @property
    def building_vegetation_path(self) -> str | None:
        """Per-building vegetation attributes (structure step 3, open data; keyed by the
        footprint ``id``). ``FIRESIM_BUILDING_VEGETATION_PATH``, else
        ``edmonton_building_vegetation.csv.gz`` next to the buildings file when present."""
        return self._sibling_of_buildings("FIRESIM_BUILDING_VEGETATION_PATH",
                                          "edmonton_building_vegetation.csv.gz")

    @property
    def canopy_path(self) -> str | None:
        """Gap-cover canopy raster (Meta 1 m CHM, open-ground canopy share on ~5 m cells,
        EPSG:3857) for the vegetation-bridged cutoff. ``FIRESIM_CANOPY_PATH``, else
        ``edmonton_canopy_5m.tif`` next to the buildings file when present."""
        return self._sibling_of_buildings("FIRESIM_CANOPY_PATH", "edmonton_canopy_5m.tif")

    @property
    def neighbourhoods_path(self) -> str | None:
        """Path to neighbourhood polygons GeoJSON for BuildingIndex partitioning.

        When set alongside buildings_path, enables neighbourhood-filtered building
        masks — only the 3-4 nearest neighbourhoods to the ignition point are loaded.
        Defaults to the bundled Edmonton neighbourhoods file.
        """
        return os.environ.get("FIRESIM_NEIGHBOURHOODS_PATH")

    @property
    def dem_path(self) -> str | None:
        """Default DEM GeoTIFF for slope-adjusted fire spread.

        When set, slope (%) and aspect (°) are derived per cell from this DEM
        and applied via the CFFDRS ISF → RSF slope correction (ST-X-3 §3.3).
        Per-request dem_path in SimulationCreate takes precedence over this.
        """
        return os.environ.get("FIRESIM_DEM_PATH")

    @property
    def wui_fine_grid(self) -> bool:
        """Run near buildings at the fuel raster's native cell size (20 m for Edmonton).

        Mechanics decision M5: a grid run whose window holds buildings is repeated at the
        native cell size on a crop around the 50 m fire, within memory guards
        (``firesim.spread.wui_window``). ``FIRESIM_WUI_FINE_GRID=0`` keeps every run at 50 m.
        """
        return os.environ.get("FIRESIM_WUI_FINE_GRID", "1").strip().lower() not in ("0", "false", "no", "off")

    @property
    def ab_fuel_grid_path(self) -> str | None:
        """Alberta Wildfire FBP fuel-type grid (2025, 100 m; a cloud-optimised GeoTIFF).

        When set and the file exists, runs on the City grid fill its no-data cells from this
        grid and runs outside the City grid use a window of it (``firesim.data.provincial``;
        docs/fuel-grid-alberta.md). Read by windowed reads only. Unset: City grid only.
        """
        return os.environ.get("FIRESIM_AB_FUEL_GRID_PATH") or None

    @property
    def ab_backcast_dir(self) -> str | None:
        """Directory of the pre-fire (back-cast) fuel data: ``backcast.json``, the NBAC burn key
        and the SCANFI epoch rasters on the Alberta grid's lattice
        (``scripts/fuelgrid/backcast.py``). When set and complete, a run dated before the end
        of the Alberta grid's vintage year (2025) gets its Alberta cells burned since that date
        restored to pre-fire fuel (``firesim.data.backcast``; docs/fuel-grid-backcast.md).
        Unset: no back-cast (the run reports why in ``fuel_source.backcast``)."""
        return os.environ.get("FIRESIM_AB_BACKCAST_DIR") or None

    @property
    def city_fuel_grid_path(self) -> str:
        """The City of Edmonton grid: a request naming this file gets the provincial default
        (City grid where mapped, Alberta grid elsewhere). Any other ``fuel_grid_path`` is used
        as given."""
        return os.environ.get(
            "FIRESIM_CITY_FUEL_GRID_PATH", "/app/data/Edmonton_FBP_FuelLayer_20251105_10m.tif")

    @property
    def national_fuel_grid_path(self) -> str | None:
        """Optional national FBP grid used outside Alberta (none is bundled)."""
        return os.environ.get("FIRESIM_NATIONAL_FUEL_GRID_PATH") or None

    @property
    def national_fuel_scheme(self) -> str:
        """Code scheme of ``national_fuel_grid_path`` ("cfs_national" or "cfs_national_2014")."""
        return os.environ.get("FIRESIM_NATIONAL_FUEL_SCHEME", "cfs_national")

    @property
    def provincial_window_km(self) -> float:
        """Side of the square fuel window for runs outside the City grid (km, default 40)."""
        return float(os.environ.get("FIRESIM_PROVINCIAL_WINDOW_KM", "40"))

    @property
    def city_margin_km(self) -> float:
        """Ignitions at least this far inside the City grid's box run on the City lattice
        (km, default 5); others get a window centred on the ignition."""
        return float(os.environ.get("FIRESIM_CITY_MARGIN_KM", "5"))

    @property
    def ab_terrain_path(self) -> str | None:
        """Alberta elevation file (NRCan MRDEM-30 averaged to ~100 m, uint16 decimetres, a
        cloud-optimised GeoTIFF; ``scripts/build_ab_terrain.py``). When set and the file
        exists, provincial / national fuel windows get slope and aspect from a window of it
        instead of the Edmonton DEM (flat outside Edmonton), if the request names the bundled
        DEM (docs/terrain-alberta.md). Unset or missing: the Edmonton DEM only."""
        return os.environ.get("FIRESIM_AB_TERRAIN_PATH") or None

    @property
    def city_dem_path(self) -> str:
        """The bundled Edmonton DEM that ``ab_terrain_path`` stands in for outside Edmonton."""
        return os.environ.get("FIRESIM_CITY_DEM_PATH", "/app/data/edmonton_dem.tif")

    @property
    def ab_assets_path(self) -> str | None:
        """Alberta critical assets (FlatGeobuf; ``scripts/build_ab_assets.py``), served by
        area by ``GET /api/v1/layers/assets``. Unset or missing: the endpoint reports the layer
        unavailable and the app keeps the bundled Edmonton layer only."""
        return os.environ.get("FIRESIM_AB_ASSETS_PATH") or None

    @property
    def ab_roads_path(self) -> str | None:
        """Alberta major roads (FlatGeobuf; ``scripts/build_ab_roads.py``), served by area by
        ``GET /api/v1/layers/roads``."""
        return os.environ.get("FIRESIM_AB_ROADS_PATH") or None

    @property
    def layers_max_box_deg(self) -> float:
        """Largest box side (degrees of latitude; longitude allowed twice this) the layers
        endpoints serve (default 1.0, about 110 km)."""
        return float(os.environ.get("FIRESIM_LAYERS_MAX_BOX_DEG", "1.0"))

    @property
    def pyra_source(self) -> bool:
        """Use Pyra's chain for the starting FWI codes (``/api/v1/weather/current`` "pyra" tier).

        On unless ``FIRESIM_PYRA_SOURCE`` is ``0``, ``false``, ``no`` or ``off``. When off, or when
        Pyra's chain has no value for the point, the CWFIS → archive → GEM chain is used.
        """
        return os.environ.get("FIRESIM_PYRA_SOURCE", "1").strip().lower() not in {"0", "false", "no", "off"}

    @property
    def small_footprint_m2(self) -> float | None:
        """Size rule for headline building counts (decision 2026-10-12, [R40];
        docs/structure-spread-spec.md §2.2): footprints under ``FIRESIM_SMALL_FOOTPRINT_M2``
        m² (default 20) are outbuildings, counted separately from buildings at risk, the
        exposure bands and structure-spread involvement; they still carry fire. ``0`` counts
        every footprint (the behaviour before 2026-10-12, kept for equivalence checks).
        ``None`` when unset (the engine default, 20 m²)."""
        v = os.environ.get("FIRESIM_SMALL_FOOTPRINT_M2", "").strip()
        return float(v) if v else None

    @property
    def small_footprints_mode(self) -> str | None:
        """``FIRESIM_SMALL_FOOTPRINTS``: ``count_separately`` (default; outbuildings carry
        fire, counted separately) or ``exclude`` (dropped from the structure-spread graph;
        a sensitivity setting, not for the live API)."""
        return os.environ.get("FIRESIM_SMALL_FOOTPRINTS", "").strip() or None


settings = Settings()

# The engine reads the size rule's default at call time through this provider.
from firesim.structures.units import set_small_footprint_default  # noqa: E402

set_small_footprint_default(lambda: settings.small_footprint_m2,
                            lambda: settings.small_footprints_mode)
