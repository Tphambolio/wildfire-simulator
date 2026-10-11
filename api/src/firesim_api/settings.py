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
    def pyra_source(self) -> bool:
        """Use Pyra's chain for the starting FWI codes (``/api/v1/weather/current`` "pyra" tier).

        On unless ``FIRESIM_PYRA_SOURCE`` is ``0``, ``false``, ``no`` or ``off``. When off, or when
        Pyra's chain has no value for the point, the CWFIS → archive → GEM chain is used.
        """
        return os.environ.get("FIRESIM_PYRA_SOURCE", "1").strip().lower() not in {"0", "false", "no", "off"}


settings = Settings()
