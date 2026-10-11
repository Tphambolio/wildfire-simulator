FROM python:3.12-slim

WORKDIR /app

# System libs needed by rasterio/GDAL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Install engine
COPY engine/pyproject.toml engine/pyproject.toml
COPY engine/src engine/src
RUN pip install --no-cache-dir engine/

# Install API
COPY api/pyproject.toml api/pyproject.toml
COPY api/src api/src
RUN pip install --no-cache-dir api/

# Bundle Edmonton spatial data so the server-side paths /app/data/* work out of the box
COPY data/ /app/data/

# Alberta Wildfire FBP fuel grid 2025 (100 m, COG, 31.8 MB; Open Government Licence - Alberta):
# the default fuel outside the City grid, read by windowed reads only. Not in git: fetched from
# the repository's data release and checked against its SHA-256. If the download fails the
# build continues and the API uses the City grid only (docs/fuel-grid-alberta.md section 6).
ARG AB_FUEL_URL=https://github.com/Tphambolio/wildfire-simulator/releases/download/data-ab-fbp-2025/ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif
ARG AB_FUEL_SHA256=e9555f401c4932f348db09a54231bb8123b3598794f15e9346c002fc934a991f
COPY scripts/fetch_ab_fuel_grid.py /tmp/fetch_ab_fuel_grid.py
RUN python /tmp/fetch_ab_fuel_grid.py "$AB_FUEL_URL" "$AB_FUEL_SHA256" \
        /app/data/ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif
ENV FIRESIM_AB_FUEL_GRID_PATH=/app/data/ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif

# Alberta building footprints (Microsoft Canadian Building Footprints 2019, ODbL; FlatGeobuf
# with a packed R-tree, 372 MB, 1,777,439 footprints): read by window only, never loaded whole.
# Not in git: fetched from the repository's data release and checked against its SHA-256. If
# the download fails the build continues and the API uses the Edmonton buildings file only
# (docs/buildings-alberta.md).
ARG AB_BUILDINGS_URL=https://github.com/Tphambolio/wildfire-simulator/releases/download/data-ab-buildings-ms2019/ab_buildings_ms2019.fgb
ARG AB_BUILDINGS_SHA256=14f0d6760665d332d74917798071bef893dd372b2b6b9f217a82c907da4405f8
RUN python /tmp/fetch_ab_fuel_grid.py "$AB_BUILDINGS_URL" "$AB_BUILDINGS_SHA256" \
        /app/data/ab_buildings_ms2019.fgb "Alberta buildings"
ENV FIRESIM_AB_BUILDINGS_PATH=/app/data/ab_buildings_ms2019.fgb

# Alberta terrain (NRCan MRDEM-30 elevation averaged to ~100 m, uint16 decimetres, COG, 91.7 MB;
# Open Government Licence - Canada): slope and aspect for runs outside Edmonton, read by window
# only. Critical assets (Statistics Canada ODHF, OGL - Canada; OpenStreetMap, ODbL; 1.5 MB) and
# major roads (OpenStreetMap, ODbL; 4.1 MB) as FlatGeobuf with packed R-trees, served by area
# (GET /api/v1/layers/*). Fetched from the repository's data release and checked against their
# SHA-256; if a download fails the build continues and the API behaves as before for that
# layer (docs/terrain-alberta.md, docs/data-sources.md).
ARG AB_DATA_RELEASE=https://github.com/Tphambolio/wildfire-simulator/releases/download/data-ab-infra-2026-10
ARG AB_TERRAIN_SHA256=90ba464a8e73d410ee8b0103208ad13624c1c8bc5ae5b150c0309e937ac9aeb9
ARG AB_ASSETS_SHA256=b61daf342bbf371010d786bbd99bd3119d932e8d4f70f175fa47cf5c7140028e
ARG AB_ROADS_SHA256=40ee4de4af7c7180e649074f20a66c0da6362edd836e151cd570e4595142283b
RUN python /tmp/fetch_ab_fuel_grid.py "$AB_DATA_RELEASE/ab_terrain_mrdem30_100m.tif" \
        "$AB_TERRAIN_SHA256" /app/data/ab_terrain_mrdem30_100m.tif "Alberta terrain" \
    && python /tmp/fetch_ab_fuel_grid.py "$AB_DATA_RELEASE/ab_assets_2026.fgb" \
        "$AB_ASSETS_SHA256" /app/data/ab_assets_2026.fgb "Alberta critical assets" \
    && python /tmp/fetch_ab_fuel_grid.py "$AB_DATA_RELEASE/ab_major_roads_osm.fgb" \
        "$AB_ROADS_SHA256" /app/data/ab_major_roads_osm.fgb "Alberta major roads" \
    && rm /tmp/fetch_ab_fuel_grid.py
ENV FIRESIM_AB_TERRAIN_PATH=/app/data/ab_terrain_mrdem30_100m.tif \
    FIRESIM_AB_ASSETS_PATH=/app/data/ab_assets_2026.fgb \
    FIRESIM_AB_ROADS_PATH=/app/data/ab_major_roads_osm.fgb

# Commit of this build, reported by GET /api/v1/version
ARG GIT_SHA=unknown
ENV FIRESIM_GIT_SHA=$GIT_SHA

EXPOSE 8000

CMD ["uvicorn", "firesim_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
