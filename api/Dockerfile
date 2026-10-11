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
        /app/data/ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif \
    && rm /tmp/fetch_ab_fuel_grid.py
ENV FIRESIM_AB_FUEL_GRID_PATH=/app/data/ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif

# Commit of this build, reported by GET /api/v1/version
ARG GIT_SHA=unknown
ENV FIRESIM_GIT_SHA=$GIT_SHA

EXPOSE 8000

CMD ["uvicorn", "firesim_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
