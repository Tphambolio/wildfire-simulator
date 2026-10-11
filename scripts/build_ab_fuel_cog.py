#!/usr/bin/env python3
"""Build the cloud-optimised GeoTIFF of the Alberta Wildfire FBP fuel grid that the API ships.

Input: the province-wide download of "Alberta Wildfire - Fire Behavior Prediction (FBP) Fuel
Type - Raster" (WildfireRasters.GDB.WF_FBP_FUEL_TYPE; 100 m, EPSG:3400, int16, 7,856 x 15,002
cells; dataset date 2025-07-16), e.g.
~/dev/wildfire/reference-layers/ab_wildfire_fbp_fueltype/AB_Wildfire_FBP_FuelType_100m_3400.tif.

Output: the same cells and codes as a COG (512 px tiles, DEFLATE with horizontal predictor,
mode overviews), with 0 (outside the service extent) declared as no data, provenance and licence
in the GeoTIFF tags, and a JSON sidecar with size and SHA-256 for the Dockerfile. Nothing is
reclassified: the code table lives in ``firesim.data.fuel_loader.AB_WILDFIRE_CODES``.

    python scripts/build_ab_fuel_cog.py SRC.tif OUT_DIR

Then attach OUT_DIR/ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif to the GitHub release
``data-ab-fbp-2025`` and set AB_FUEL_SHA256 in api/Dockerfile (docs/fuel-grid-alberta.md §6).
Uses the gdal_translate command (GDAL >= 3.1, COG driver); never loads the raster in Python.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

NAME = "ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif"
TAGS = {
    "product": "Alberta Wildfire - Fire Behavior Prediction (FBP) Fuel Type - Raster",
    "source_dataset": "WildfireRasters.GDB.WF_FBP_FUEL_TYPE (dataset date 2025-07-16)",
    "source_service": "https://geospatial.alberta.ca/umbriel/rest/services/wildfire/"
                      "alberta_wildfire_fbp_fuel_type_raster/ImageServer",
    "copyright": "Copyright Government of Alberta",
    "licence": "Open Government Licence - Alberta (https://open.alberta.ca/licence), as stated "
               "on the FBP Fuel Type map-service item; the image-service item carries the "
               "Government of Alberta disclaimer (information provided as is, no warranty)",
    "attribution": "Contains information licensed under the Open Government Licence - Alberta.",
    "codes": "1-7 C-1..C-7; 13 D-1/D-2; 21-23 S-1..S-3; 31 O-1; 101 non-fuel; 102 water; "
             "104 unclassified; 105 vegetated non-fuel; 610-690 M-1/M-2 with 10-90 percent "
             "conifer; 0 outside extent (nodata); 32767 no data",
    "converted_by": "FireSim scripts/build_ab_fuel_cog.py (cells and codes unchanged)",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(src: str, out_dir: str) -> None:
    out = Path(out_dir) / NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    tags = dict(TAGS, converted=date.today().isoformat())
    cmd = ["gdal_translate", "-q", "-of", "COG", "-a_nodata", "0",
           "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=2", "-co", "LEVEL=9",
           "-co", "BLOCKSIZE=512", "-co", "OVERVIEW_RESAMPLING=MODE"]
    for k, v in tags.items():
        cmd += ["-mo", f"{k}={v}"]
    subprocess.run(cmd + [src, str(out)], check=True)
    meta = {"file": NAME, "bytes": out.stat().st_size, "sha256": sha256(out),
            "source_file": Path(src).name, "source_sha256": sha256(Path(src)), **tags}
    (out.parent / (NAME + ".json")).write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps({k: meta[k] for k in ("file", "bytes", "sha256")}))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
