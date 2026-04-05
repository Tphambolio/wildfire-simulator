FROM python:3.12-slim

WORKDIR /app

# System libs needed by rasterio/GDAL and llvmlite (numba)
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

EXPOSE 8000

CMD ["uvicorn", "firesim_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
