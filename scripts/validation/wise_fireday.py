#!/usr/bin/env python3
"""Run W.I.S.E. on the same validation fire-days as FireSim and score both identically.

Local, optional, slow. Needs the headless WISE Docker image ``wise-local:1.0.6b6`` and its demo
job template (see /home/rpas/dev/wildfire/wise-reference/README.md), geopandas and pyproj.

For each fire-day the WISE job gets exactly FireSim's inputs: the same fuel types per cell
(after the seasonal D-1/D-2, M-1/M-2 choice, M-1/M-2 at 50 % conifer), the same DEM, the same
starting burned area (perimeter mode, as POLYGON_OUT ignitions), the same hourly ERA5 weather
and previous-day FWI codes, 06:00-23:00 local, spotting off. WISE perimeters are rasterised onto
the validation grid and scored with firesim.validation.metrics, as are FireSim's.

    PYTHONPATH=engine/src python3 scripts/validation/wise_fireday.py 2016_255:126 2019_177:168
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

ROOT = Path(os.environ.get("FIRESIM_VALIDATION_DATA", "/home/rpas/dev/wildfire/validation-data"))
WISE_DIR = Path("/home/rpas/dev/wildfire/wise-reference")
DEMO = Path.home() / "dev/wildfire/vendor/wise_demo/demo_data/testjob/job.fgmj"
DEMO_LUT = Path.home() / "dev/wildfire/vendor/wise_demo/demo_data/testjob/Inputs/dataset.lut"
WISE_CRS = 3400  # NAD83 / Alberta 10-TM (Forest)
TZ = {"timezone": "MDT", "timezoneId": 131084}

# FireSim fuel type -> code in the WISE demo LUT
WISE_CODES = {"C1": 101, "C2": 102, "C3": 103, "C4": 104, "C5": 5, "C6": 6, "C7": 7,
              "D1": 11, "D2": 12, "M1": 450, "M2": 550, "M3": 70, "M4": 80,
              "O1a": 31, "O1b": 32, "S1": 21, "S2": 22, "S3": 23}
NON_FUEL = 119


def t(s: str) -> dict:
    return dict(time=s, **TZ)


def build_job(case, opts, name: str) -> tuple[Path, object, object]:
    import rasterio
    import shapely
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    from firesim.validation.harness import fuel_grid_for

    dom = case.domain
    d = WISE_DIR / "jobs" / f"val_{name}"
    shutil.rmtree(d, ignore_errors=True)
    (d / "Inputs").mkdir(parents=True)
    (d / "Outputs").mkdir()
    src_t = from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat)

    fg = fuel_grid_for(dom, case.day, opts)
    codes = np.array([[WISE_CODES[f.value] if f is not None else NON_FUEL for f in row]
                      for row in fg.fuel_types], dtype=np.int16)
    res = min(dom.dx, dom.dy)
    from rasterio.warp import calculate_default_transform
    dst_t, w, h = calculate_default_transform("EPSG:4326", f"EPSG:{WISE_CRS}", dom.cols, dom.rows,
                                              dom.lng_min, dom.lat_min, dom.lng_max, dom.lat_max,
                                              resolution=res)
    prof = dict(driver="GTiff", width=w, height=h, count=1, crs=f"EPSG:{WISE_CRS}", transform=dst_t)
    fuel_p = np.full((h, w), NON_FUEL, dtype=np.int16)
    reproject(codes, fuel_p, src_transform=src_t, src_crs="EPSG:4326", dst_transform=dst_t,
              dst_crs=f"EPSG:{WISE_CRS}", resampling=Resampling.nearest)
    fuel_p[fuel_p == 0] = NON_FUEL  # cells outside the source grid; WISE rejects unknown codes
    with rasterio.open(d / "Inputs/fuels.tif", "w", dtype="int16", nodata=-9999, **prof) as f:
        f.write(fuel_p, 1)
    elev = dom.elevation if dom.elevation is not None else np.full((dom.rows, dom.cols), 500.0)
    elev_p = np.zeros((h, w), dtype=np.float32)
    reproject(elev.astype(np.float32), elev_p, src_transform=src_t, src_crs="EPSG:4326",
              dst_transform=dst_t, dst_crs=f"EPSG:{WISE_CRS}", resampling=Resampling.bilinear)
    with rasterio.open(d / "Inputs/elevation.tif", "w", dtype="float32", nodata=-9999, **prof) as f:
        f.write(elev_p, 1)
    from pyproj import CRS
    (d / "Inputs/dataset.prj").write_text(CRS.from_epsg(WISE_CRS).to_wkt("WKT1_ESRI"))
    shutil.copy(DEMO_LUT, d / "Inputs/dataset.lut")

    # Weather: the burn day's ERA5 hours (and the evening before), previous-day codes
    lines = ["HOURLY,HOUR,TEMP,RH,WD,WS,PRECIP,FFMC,DMC,DC"]
    # The stream starts the day before (WISE needs a previous day to initialise daily FWI);
    # hours before the burn day's 06:00 repeat the 06:00 record (codes are given explicitly).
    day0 = case.start.replace(hour=0) - timedelta(days=1)
    hrs = {case.start + timedelta(hours=k): h for k, h in enumerate(case.hourly)}
    first = min(hrs)
    for k in range(72):
        ts = day0 + timedelta(hours=k)
        hw = hrs.get(ts) or hrs[first]
        lines.append(f"{ts:%Y-%m-%d},{ts:%H},{hw.temperature:.1f},{hw.relative_humidity:.0f},"
                     f"{hw.wind_direction:.0f},{hw.wind_speed:.1f},{hw.precipitation:.2f},"
                     f"{case.ffmc:.1f},{case.dmc:.1f},{case.dc:.1f}")
    (d / "Inputs/weather.txt").write_text("\n".join(lines) + "\n")

    # Ignition: the starting burned area (perimeter mode), one POLYGON_OUT per patch
    prior = (dom.dob > 0) & (dom.dob < case.day)
    polys = [shapely.geometry.shape(g).buffer(0) for g, v in
             shapes(prior.astype(np.uint8), mask=prior, transform=src_t)]
    ign_shapes = []
    for p in polys:
        p = shapely.Polygon(p.exterior).simplify(0.25 * dom.cell_lng)
        if p.area <= 0:
            continue
        pts = [{"x": {"value": x}, "y": {"value": y}} for x, y in list(p.exterior.coords)[:-1]]
        ign_shapes.append({"polyType": "POLYGON_OUT",
                           "polygon": {"units": "LAT_LON", "polygon": {"version": 1, "points": pts}}})

    demo = json.loads(DEMO.read_text())
    p = demo["project"]
    date = f"{case.start:%Y-%m-%d}"
    start, end = f"{date}T06:00:00-06:00", f"{date}T23:00:00-06:00"
    # O-1a curing as in FireSim's run (WISE default 60 %); O-1 is rare in these fires
    green = opts.greenup_doy <= case.day < opts.leafoff_doy
    curing = (opts.grass_cure_green if green else opts.grass_cure_dormant) / 100.0
    for fd in p["fuels"]["data"]:
        if fd["index"] == 31:
            fd["fuelData"]["data"]["fuel"]["fuelTypeModified"] = True
            fd["fuelData"]["data"]["fuel"]["spread"] = {"version": 1, "o1": {"version": 1, "parms": {
                "version": 1, "a": {"value": 190.0}, "b": {"value": 0.031}, "c": {"value": 1.4},
                "q": {"value": 1.0}, "bui0": {"value": 1.0}, "maxBe": {"value": 1.0},
                "curingDegree": {"value": curing}}}}
    p["ignitions"] = {"version": 1, "ignitions": [{
        "version": 1, "name": "ign", "comments": "", "color": 255, "fillColor": 0, "size": 3,
        "imported": False, "symbol": 0,
        "ignition": {"version": 1, "startTime": t(start), "ignitions": {"ignitions": ign_shapes}}}]}
    clat = dom.lat_max - 0.5 * dom.rows * dom.cell_lat
    clng = dom.lng_min + 0.5 * dom.cols * dom.cell_lng
    st = p["stations"]["stations"][0]
    st["name"] = "stn"
    st["station"]["location"]["point"] = {"x": {"value": clng}, "y": {"value": clat}}
    st["station"]["elevation"] = {"value": float(np.mean(elev))}
    strm = st["station"]["streams"][0]
    strm["name"] = "strm"
    strm["condition"] = {
        "version": 1, "dataImportedFromFile": True, "filename": "Inputs/weather.txt",
        "startTime": t(f"{day0:%Y-%m-%d}T00:00:00-06:00"),
        "hffmcTime": {"time": "13:00:00"}, "hffmc": {"value": case.ffmc}, "hffmcMethod": "LAWSON",
        "startingCodes": {"ffmc": {"value": case.ffmc}, "dmc": {"value": case.dmc},
                          "dc": {"value": case.dc}, "precipitation": {"value": 0.0}}}
    p["grids"] = {"version": 1, "filters": []}
    sc = p["scenarios"]["scenarios"][0]
    sc["name"] = "scen"
    s = sc["scenario"]
    s["startTime"], s["endTime"] = t(start), t(end)
    s["displayInterval"] = {"time": "01:00:00"}
    s["fbpOptions"] = {"terrainEffect": True, "windEffect": True}
    s["fgmOptions"].update({"spotting": False, "breaching": True, "stopAtGridEnd": True,
                            "purgeNonDisplayable": False})
    s["fmcOptions"] = {"nodataElev": {"value": float(np.mean(elev))}, "terrain": True,
                       "accurateLocation": True}
    s["fwiOptions"] = {"fwiSpacialInterp": False, "fwiFromSpacialWeather": False,
                       "historyOnEffectedFwi": False, "burningConditionsOn": False,
                       "fwiTemporalInterp": False}
    sc["temporalConditions"] = {"version": 1, "daily": [], "seasonal": []}
    sc["fireIndex"] = [{"name": "ign"}]
    sc["weatherIndex"] = [{"stationIndex": {"name": "stn"}, "streamIndex": {"name": "strm"}}]
    sc["filterIndex"] = []
    p["scenarios"]["scenarios"] = [sc]
    p["projectStartTime"] = t(f"{day0:%Y-%m-%d}T00:00:00-06:00")
    vec = p["outputs"]["vectors"][0]
    vec.update({"scenarioName": "scen", "filename": "Outputs/perim.shp", "streamOutput": False,
                "perimeterTime": {"startTime": t(start), "endTime": t(end)},
                "multiplePerimeters": True, "removeIslands": False, "mergeContacting": True})
    vec["metadata"].update({"areaUnits": "HECTARES"})
    p["outputs"]["vectors"] = [vec]
    p["outputs"]["summaries"] = []
    p["outputs"]["stats"] = []
    demo["settings"].pop("mqtt", None)
    demo["settings"]["hardware"] = {"cores": 4, "processes": 1}
    demo["settings"]["logfile"] = {"filename": "logfile.log", "verbosity": "WARN"}
    demo["name"] = name
    demo["inputFiles"] = []
    demo["outputLocation"] = []
    (d / "job.fgmj").write_text(json.dumps(demo, indent=1))
    return d, src_t, prior


def run_wise(d: Path) -> float:
    import time
    t0 = time.time()
    subprocess.run(["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}", "-v",
                    f"{d}:/work", "-e", "HOME=/tmp", "-w", "/work", "wise-local:1.0.6b6",
                    "wise", "-t", "/work/job.fgmj"], check=True, capture_output=True, timeout=7200)
    return time.time() - t0


def score_wise(case, d: Path, src_t) -> dict:
    import geopandas as gpd
    from rasterio.features import rasterize

    from firesim.validation.metrics import hausdorff_m, overlap_scores

    dom = case.domain
    g = gpd.read_file(d / "Outputs/perim.shp").to_crs(4326)
    prior = (dom.dob > 0) & (dom.dob < case.day)
    obs = (dom.dob == case.day) & ~prior
    out = {}
    for hour_label, w in (("8h", "14:00"), ("17h", "23:00")):
        sel = g[g["TIME"] == w] if "TIME" in g else g
        if len(sel) == 0:
            continue
        pred = rasterize([(geom, 1) for geom in sel.geometry], out_shape=(dom.rows, dom.cols),
                         transform=src_t, dtype=np.uint8).astype(bool) & ~prior
        s = overlap_scores(pred, obs, dom.cell_area_m2)
        out[hour_label] = {**s.as_dict(), "hausdorff_m": hausdorff_m(pred, obs, dom.dx, dom.dy)}
    best = None
    for time_label in sorted(set(g["TIME"])) if "TIME" in g else []:
        pred = rasterize([(geom, 1) for geom in g[g["TIME"] == time_label].geometry],
                         out_shape=(dom.rows, dom.cols), transform=src_t,
                         dtype=np.uint8).astype(bool) & ~prior
        s = overlap_scores(pred, obs, dom.cell_area_m2)
        if not math.isnan(s.f1) and (best is None or s.f1 > best["f1"]):
            best = {**s.as_dict(), "time": time_label,
                    "hausdorff_m": hausdorff_m(pred, obs, dom.dx, dom.dy)}
    out["oracle"] = best
    return out


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from validate import _load_fire  # noqa: E402  (same cached inputs as the FireSim runs)

    from firesim.validation.harness import RunOptions, build_case, run_fire_day

    opts = RunOptions(windows_h=(8.0, 17.0), oracle_max_h=17)
    out_path = ROOT / "runs_wise" / "wise_vs_firesim.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    for arg in sys.argv[1:]:
        fid, day = arg.split(":")
        day = int(day)
        dom, groups, rec = _load_fire(fid)
        case = build_case(dom, day, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
        d, src_t, _ = build_job(case, opts, f"{fid}_{day}")
        try:
            wise_s = run_wise(d)
            wise = score_wise(case, d, src_t)
        except Exception as exc:  # noqa: BLE001
            wise_s, wise = math.nan, {"error": repr(exc)}
        fs = run_fire_day(case, opts)
        row = {"fire_id": fid, "day": day, "wise_run_s": wise_s, "wise": wise,
               "firesim": {k: fs["members"]["det"][k] for k in ("8h", "17h", "oracle")},
               "firesim_run_s": fs["members"]["det"]["run_s"],
               "obs_growth_ha": fs["obs_growth_ha"]}
        with open(out_path, "a") as fh:
            fh.write(json.dumps(row, default=float) + "\n")
        w17 = wise.get("17h", {}) if isinstance(wise, dict) else {}
        print(f"{fid} day {day}: WISE F1(17h)={w17.get('f1')} FireSim F1(17h)="
              f"{row['firesim']['17h']['f1']:.3f}  WISE {wise_s:.0f}s FireSim "
              f"{row['firesim_run_s']:.0f}s", flush=True)


if __name__ == "__main__":
    main()
