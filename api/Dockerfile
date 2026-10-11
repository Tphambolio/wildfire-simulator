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

# Install API (with boto3 for the run archive's S3/R2 backend; docs/run-archive.md)
COPY api/pyproject.toml api/pyproject.toml
COPY api/src api/src
RUN pip install --no-cache-dir "api/[archive-s3]"

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

# Litestream (Apache-2.0): backs up the run-archive index (SQLite) to R2 when FIRESIM_ARCHIVE_DB
# and the FIRESIM_ARCHIVE_S3_* secrets are set (api/deploy/entrypoint.sh). Checked against its
# SHA-256; if the download fails the image still builds and the index stays on local disk.
ARG LITESTREAM_URL=https://github.com/benbjohnson/litestream/releases/download/v0.5.17/litestream-0.5.17-linux-x86_64.tar.gz
ARG LITESTREAM_SHA256=cfb371176d164437ae869f8351cfde49bd1804ae71c61923f75c9cba9c9c006d
RUN python /tmp/fetch_ab_fuel_grid.py "$LITESTREAM_URL" "$LITESTREAM_SHA256" /tmp/litestream.tgz \
        "Litestream" "the run-archive index is not backed up" \
    && if [ -f /tmp/litestream.tgz ]; then \
        tar -xzf /tmp/litestream.tgz -C /usr/local/bin litestream && rm /tmp/litestream.tgz; fi \
    && rm /tmp/fetch_ab_fuel_grid.py
COPY api/deploy/litestream.yml /etc/litestream.yml
COPY api/deploy/entrypoint.sh /usr/local/bin/firesim-entrypoint
RUN chmod +x /usr/local/bin/firesim-entrypoint

# Commit of this build, reported by GET /api/v1/version
ARG GIT_SHA=unknown
ENV FIRESIM_GIT_SHA=$GIT_SHA

EXPOSE 8000

# Starts uvicorn (under Litestream when the run-archive index has R2 credentials)
CMD ["/usr/local/bin/firesim-entrypoint"]
