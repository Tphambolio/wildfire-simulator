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


settings = Settings()
