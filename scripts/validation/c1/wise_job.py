"""Build and run one W.I.S.E. job for a FireSim validation fire-day (C1 comparator; black box).

W.I.S.E. 1.0.6-beta.6 (the official ``wise-ubuntu2204-1.0.6-beta.6.deb``) runs headless in the
Docker image ``wise-local:1.0.6b6`` (``~/dev/wildfire/wise-reference/``). Only documented job-file
(FGMJ) inputs are set; no WISE source is read or changed.

The job gets FireSim's own inputs for the fire-day (PREREG_COMPARATOR_C1 §4):

- fuel: the harness fuel grid after the seasonal D-1/M-1 -> D-2/M-2 choice, cell by cell, with
  M-1/M-2 percent conifer to the nearest 5 % LUT class and M-3/M-4 at 35 % dead fir; cells
  FireSim burns out at the start (inactive edges) are non-fuel; reprojected nearest-neighbour
  to EPSG:3400 at the harness resolution;
- elevation: the domain DEM (bilinear to EPSG:3400);
- start: the cells FireSim's run starts burning (``burning`` mask), one POLYGON_OUT per patch;
- weather: the same ERA5 hours as FireSim (17:00 the previous day onward when FireSim spins up
  the hourly FFMC), wind direction converted from true to grid north, daily FFMC/DMC/DC fixed
  at the case's codes, hourly FFMC by Van Wagner from 17:00 the previous day;
- burning period: the scenario runs only inside it (10:00-20:00), as FireSim's ROS x 0 outside;
- O-1a/O-1b curing as FireSim's; spotting off; perimeters every ``step_min`` minutes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

LOG = logging.getLogger("c1")

IMAGE = "wise-local:1.0.6b6"
DEMO = Path.home() / "dev/wildfire/vendor/wise_demo/demo_data/testjob/job.fgmj"
DEMO_LUT = Path.home() / "dev/wildfire/vendor/wise_demo/demo_data/testjob/Inputs/dataset.lut"
WISE_CRS = 3400  # NAD83 / Alberta 10-TM (Forest)
TZ = {"timezone": "MDT", "timezoneId": 131084}
UTC_OFFSET = "-06:00"
NON_FUEL = 119
MEM = "5g"  # docker --memory cap of one WISE container (<= 5 GB per job)

# FireSim fuel type -> code in the WISE demo LUT (M-1/M-2 by percent conifer, see fuel_code)
WISE_CODES = {"C1": 101, "C2": 102, "C3": 103, "C4": 104, "C5": 5, "C6": 6, "C7": 7,
              "D1": 11, "D2": 12, "M3": 735, "M4": 835,
              "O1a": 31, "O1b": 32, "S1": 21, "S2": 22, "S3": 23}
# FBP O-1 spread parameters (ST-X-3 Table 6); set only because WISE needs the full block to
# change the degree of curing
O1_PARMS = {31: (190.0, 0.0310, 1.40), 32: (250.0, 0.0350, 1.70)}


def t(s: str) -> dict:
    return dict(time=s, **TZ)


def fuel_code(ft, pc) -> int:
    """WISE LUT code of one FireSim cell (``ft`` FuelType or None, ``pc`` percent conifer)."""
    if ft is None:
        return NON_FUEL
    v = ft.value
    if v in ("M1", "M2"):
        p = 50.0 if pc is None else float(pc)
        k = int(min(95, max(5, 5 * round(p / 5.0))))
        return (400 if v == "M1" else 500) + k
    return WISE_CODES[v]


def grid_convergence_deg(lat: float, lng: float) -> float:
    """Grid bearing of true north in EPSG:3400 at (lat, lng): a true bearing B is grid B + this."""
    from pyproj import Transformer

    tr = Transformer.from_crs(4326, WISE_CRS, always_xy=True)
    x0, y0 = tr.transform(lng, lat)
    x1, y1 = tr.transform(lng, lat + 0.01)
    return math.degrees(math.atan2(x1 - x0, y1 - y0))


def build_job(d: Path, case, opts, setup: dict, burning: np.ndarray, burned_out: np.ndarray,
              member: dict | None = None, step_min: int = 30, cores: int = 4,
              hourly_ffmc: dict | None = None) -> dict:
    """Write a WISE job for ``case`` into the new directory ``d``; returns its metadata.

    ``setup`` is ``harness._setup(case, opts, Member())`` (fuel grid, config); ``burning`` the
    cells FireSim starts burning, ``burned_out`` the starting cells it makes non-fuel.
    ``member`` optionally overrides the inputs with an ensemble member's perturbation
    (``wind_dir_offset_deg``, ``wind_speed_factor``, ``ffmc``, ``dmc``, ``dc``, ``grass_cure``).
    ``hourly_ffmc`` ({local datetime of the hour: FFMC}) is FireSim's hourly FFMC for the run
    (``hourly_ffmc_of``): WISE reads an FFMC column of an hourly file as the hourly FFMC, so both
    models burn with the same hourly FFMC; hours outside it carry the nearest value.
    """
    import rasterio
    import shapely
    from pyproj import CRS
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    dom = case.domain
    shutil.rmtree(d, ignore_errors=True)
    (d / "Inputs").mkdir(parents=True)
    (d / "Outputs").mkdir()
    src_t = from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat)
    m = member or {}

    fg = setup["fuel_grid"]
    pcs = fg.pc
    codes = np.array([[fuel_code(fg.fuel_types[r][c], pcs[r][c] if pcs else None)
                       for c in range(fg.cols)] for r in range(fg.rows)], dtype=np.int16)
    codes[burned_out] = NON_FUEL
    res = min(dom.dx, dom.dy)
    dst_t, w, h = calculate_default_transform("EPSG:4326", f"EPSG:{WISE_CRS}", dom.cols, dom.rows,
                                              dom.lng_min, dom.lat_min, dom.lng_max, dom.lat_max,
                                              resolution=res)
    prof = dict(driver="GTiff", width=w, height=h, count=1, crs=f"EPSG:{WISE_CRS}", transform=dst_t)
    fuel_p = np.full((h, w), NON_FUEL, dtype=np.int16)
    reproject(codes, fuel_p, src_transform=src_t, src_crs="EPSG:4326", dst_transform=dst_t,
              dst_crs=f"EPSG:{WISE_CRS}", resampling=Resampling.nearest)
    fuel_p[fuel_p <= 0] = NON_FUEL  # outside the source grid; WISE rejects unknown codes
    with rasterio.open(d / "Inputs/fuels.tif", "w", dtype="int16", nodata=-9999, **prof) as f:
        f.write(fuel_p, 1)
    elev = dom.elevation if dom.elevation is not None else np.full((dom.rows, dom.cols), 500.0)
    elev_p = np.zeros((h, w), dtype=np.float32)
    reproject(elev.astype(np.float32), elev_p, src_transform=src_t, src_crs="EPSG:4326",
              dst_transform=dst_t, dst_crs=f"EPSG:{WISE_CRS}", resampling=Resampling.bilinear)
    with rasterio.open(d / "Inputs/elevation.tif", "w", dtype="float32", nodata=-9999, **prof) as f:
        f.write(elev_p, 1)
    (d / "Inputs/dataset.prj").write_text(CRS.from_epsg(WISE_CRS).to_wkt("WKT1_ESRI"))
    shutil.copy(DEMO_LUT, d / "Inputs/dataset.lut")

    # Weather: FireSim's hourly stream (spin-up hours + burn day), member-perturbed as FireSim's
    clat = dom.lat_max - 0.5 * dom.rows * dom.cell_lat
    clng = dom.lng_min + 0.5 * dom.cols * dom.cell_lng
    conv = grid_convergence_deg(clat, clng)
    d_dir = float(m.get("wind_dir_offset_deg", 0.0))
    k_ws = float(m.get("wind_speed_factor", 1.0))
    ffmc = float(m.get("ffmc", case.ffmc))
    dmc = float(m.get("dmc", case.dmc))
    dc = float(m.get("dc", case.dc))
    hourly = list(case.spinup if opts.ffmc_spinup else ()) + list(case.hourly)
    hrs = {case.start + timedelta(hours=hw.hours_from_start): hw for hw in hourly}
    first = min(hrs)
    day0 = datetime(first.year, first.month, first.day)
    lines = ["HOURLY,HOUR,TEMP,RH,WD,WS,PRECIP,FFMC,DMC,DC"]
    for k in range(72):
        ts = day0 + timedelta(hours=k)
        hw = hrs.get(ts) or (hrs[first] if ts < first else hrs[max(hrs)])
        wd = (hw.wind_direction + d_dir + conv) % 360.0
        hf = ffmc
        if hourly_ffmc:
            near = min(hourly_ffmc, key=lambda x: abs((x - ts).total_seconds()))
            hf = hourly_ffmc[near]
        lines.append(f"{ts:%Y-%m-%d},{ts:%H},{hw.temperature:.1f},{hw.relative_humidity:.0f},"
                     f"{wd:.0f},{max(0.0, hw.wind_speed * k_ws):.1f},{hw.precipitation:.2f},"
                     f"{hf:.2f},{dmc:.1f},{dc:.1f}")
    (d / "Inputs/weather.txt").write_text("\n".join(lines) + "\n")

    # Ignition: FireSim's burning start cells, one POLYGON_OUT per patch
    ign_shapes = []
    for g, _ in shapes(burning.astype(np.uint8), mask=burning, transform=src_t):
        p = shapely.Polygon(shapely.geometry.shape(g).exterior).simplify(0.25 * dom.cell_lng)
        if p.area <= 0:
            continue
        pts = [{"x": {"value": x}, "y": {"value": y}} for x, y in list(p.exterior.coords)[:-1]]
        ign_shapes.append({"polyType": "POLYGON_OUT",
                           "polygon": {"units": "LAT_LON", "polygon": {"version": 1, "points": pts}}})

    demo = json.loads(DEMO.read_text())
    p = demo["project"]
    date = case.start.date()
    if opts.burning_period:
        b0, b1 = opts.burning_period
    else:
        b0, b1 = case.start.hour, case.start.hour + max(opts.windows_h)
    s0 = datetime(date.year, date.month, date.day) + timedelta(hours=b0)
    s1 = datetime(date.year, date.month, date.day) + timedelta(hours=b1)
    out_end = case.start + timedelta(hours=max(max(opts.windows_h), opts.oracle_max_h))
    iso = lambda x: f"{x:%Y-%m-%dT%H:%M:%S}{UTC_OFFSET}"  # noqa: E731
    from firesim.validation.harness import grass_cure_for
    curing = float(m.get("grass_cure", grass_cure_for(case.day, opts))) / 100.0
    for fd in p["fuels"]["data"]:
        if fd["index"] in O1_PARMS:
            a, b, c = O1_PARMS[fd["index"]]
            fd["fuelData"]["data"]["fuel"]["fuelTypeModified"] = True
            fd["fuelData"]["data"]["fuel"]["spread"] = {"version": 1, "o1": {"version": 1, "parms": {
                "version": 1, "a": {"value": a}, "b": {"value": b}, "c": {"value": c},
                "q": {"value": 1.0}, "bui0": {"value": 1.0}, "maxBe": {"value": 1.0},
                "curingDegree": {"value": curing}}}}
    p["ignitions"] = {"version": 1, "ignitions": [{
        "version": 1, "name": "ign", "comments": "", "color": 255, "fillColor": 0, "size": 3,
        "imported": False, "symbol": 0,
        "ignition": {"version": 1, "startTime": t(iso(s0)), "ignitions": {"ignitions": ign_shapes}}}]}
    st = p["stations"]["stations"][0]
    st["name"] = "stn"
    st["station"]["location"]["point"] = {"x": {"value": clng}, "y": {"value": clat}}
    st["station"]["elevation"] = {"value": float(np.mean(elev))}
    strm = st["station"]["streams"][0]
    strm["name"] = "strm"
    hffmc_time = f"{first:%H}:00:00"
    strm["condition"] = {
        "version": 1, "dataImportedFromFile": True, "filename": "Inputs/weather.txt",
        "startTime": t(iso(day0)),
        "hffmcTime": {"time": hffmc_time}, "hffmc": {"value": ffmc}, "hffmcMethod": "VAN_WAGNER",
        "startingCodes": {"ffmc": {"value": ffmc}, "dmc": {"value": dmc},
                          "dc": {"value": dc}, "precipitation": {"value": 0.0}}}
    p["grids"] = {"version": 1, "filters": []}
    sc = p["scenarios"]["scenarios"][0]
    sc["name"] = "scen"
    s = sc["scenario"]
    s["startTime"], s["endTime"] = t(iso(s0)), t(iso(s1))
    s["displayInterval"] = {"time": f"{step_min // 60:02d}:{step_min % 60:02d}:00"}
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
    p["projectStartTime"] = t(iso(day0))
    vec = p["outputs"]["vectors"][0]
    vec.update({"scenarioName": "scen", "filename": "Outputs/perim.shp", "streamOutput": False,
                "perimeterTime": {"startTime": t(iso(s0)), "endTime": t(iso(s1))},
                "multiplePerimeters": True, "removeIslands": False, "mergeContacting": True})
    vec["metadata"].update({"areaUnits": "HECTARES"})
    p["outputs"]["vectors"] = [vec]
    p["outputs"]["summaries"] = []
    p["outputs"]["stats"] = [{"version": 1, "scenarioName": "scen", "streamName": "strm",
                              "filename": "Outputs/stats.csv", "streamOutput": False,
                              "fileType": "CSV", "globalColumns": [
                                  "DATE_TIME", "TEMPERATURE", "RELATIVE_HUMIDITY", "WIND_SPEED",
                                  "WIND_DIRECTION", "HFFMC", "HISI", "DMC", "DC", "BUI", "FFMC",
                                  "TOTAL_BURN_AREA", "MAX_ROS"]}]
    demo["settings"].pop("mqtt", None)
    demo["settings"]["hardware"] = {"cores": cores, "processes": 1}
    demo["settings"]["logfile"] = {"filename": "logfile.log", "verbosity": "WARN"}
    demo["name"] = d.name
    demo["inputFiles"] = []
    demo["outputLocation"] = []
    (d / "job.fgmj").write_text(json.dumps(demo, indent=1))
    return {"grid_convergence_deg": conv, "burn_start": iso(s0), "burn_end": iso(s1),
            "out_end": iso(out_end), "n_ignition_polys": len(ign_shapes), "curing": curing,
            "wise_grid": [h, w], "step_min": step_min}


def hourly_ffmc_of(case, config, fuel_grid) -> dict:
    """FireSim's hourly FFMC by local clock hour for a run configuration (its weather
    schedule: each hour uses the FFMC reached at the end of that hour)."""
    from firesim.spread.simulator import Simulator

    out = {}
    for t_min, cond in Simulator(config, fuel_grid=fuel_grid).weather_schedule():
        ts = case.start + timedelta(minutes=t_min)
        ts = ts.replace(minute=0, second=0, microsecond=0)
        out.setdefault(ts, float(cond.ffmc))
    return out


class LowMemoryStop(RuntimeError):
    """The host's available memory fell below the stop threshold during a WISE run."""


def mem_available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 ** 2
    return math.nan


def disk_free_gb(path: Path) -> float:
    """Free space (GB) on the filesystem holding ``path`` (what ``df`` reports as available)."""
    return shutil.disk_usage(path).free / 1024 ** 3


def run_wise(d: Path, timeout_s: int = 10800, stop_below_gb: float | None = None,
             disk_pause_gb: float | None = None) -> dict:
    """Run the job in ``d`` in its own container (``--memory`` capped); time and peak memory.
    With ``stop_below_gb``, the container is stopped (recorded as stopped for memory) if the
    host's available memory falls below it (owner rule for the 10 GB reruns). With
    ``disk_pause_gb``, the container is ``docker pause``d while free disk on ``d``'s filesystem
    is below it and resumed when it recovers; paused time is excluded from ``run_s`` and from
    the timeout and reported as ``paused_s``."""
    name = f"c1wise_{d.name}_{os.getpid()}"[:60]
    cmd = ["docker", "run", "--rm", "--name", name, "--memory", MEM, "--memory-swap", MEM,
           "-u", f"{os.getuid()}:{os.getgid()}", "-v", f"{d}:/work", "-e", "HOME=/tmp",
           "-w", "/work", IMAGE, "wise", "-t", "/work/job.fgmj"]
    peak = [0.0]
    stop = threading.Event()
    low = [False]
    paused = {"on": False, "since": 0.0, "total": 0.0}

    def sample():
        while not stop.wait(3.0):
            if stop_below_gb is not None and mem_available_gb() < stop_below_gb:
                low[0] = True
                if paused["on"]:
                    subprocess.run(["docker", "unpause", name], capture_output=True)
                subprocess.run(["docker", "stop", "-t", "5", name], capture_output=True)
                return
            if disk_pause_gb is not None:
                free = disk_free_gb(d)
                if not paused["on"] and free < disk_pause_gb:
                    if subprocess.run(["docker", "pause", name], capture_output=True).returncode == 0:
                        paused.update(on=True, since=time.time())
                        LOG.warning("%s paused: %.1f GB disk free (< %g)", name, free, disk_pause_gb)
                elif paused["on"] and free >= disk_pause_gb:
                    subprocess.run(["docker", "unpause", name], capture_output=True)
                    paused["total"] += time.time() - paused["since"]
                    paused["on"] = False
                    LOG.warning("%s resumed: %.1f GB disk free", name, free)
            r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name],
                               capture_output=True, text=True)
            v = r.stdout.strip().split("/")[0].strip()
            try:
                num = float("".join(ch for ch in v if ch.isdigit() or ch == "."))
                unit = v.lstrip("0123456789. ").lower()
                mb = num * {"kib": 1 / 1024, "mib": 1, "gib": 1024, "b": 1 / 1e6}.get(unit, 0)
                peak[0] = max(peak[0], mb)
            except ValueError:
                pass

    def paused_s() -> float:
        return paused["total"] + (time.time() - paused["since"] if paused["on"] else 0.0)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    timed_out = False
    while True:  # timeout counts running time only (paused time excluded)
        try:
            _, stderr = proc.communicate(timeout=10)
            break
        except subprocess.TimeoutExpired:
            if time.time() - t0 - paused_s() > timeout_s:
                timed_out = True
                subprocess.run(["docker", "unpause", name], capture_output=True)
                subprocess.run(["docker", "kill", name], capture_output=True)
                _, stderr = proc.communicate()
                break
    rc, err = (-9, "timeout") if timed_out else (proc.returncode, (stderr or "")[-400:])
    stop.set()
    th.join(timeout=5)
    p_s = paused_s()
    run_s = time.time() - t0 - p_s
    if low[0]:  # stopped for host memory: recorded as such (owner rule), not a WISE failure
        return {"ok": False, "rc": rc, "err": f"stopped for memory (< {stop_below_gb} GB available)",
                "stopped_for_memory": True, "run_s": run_s, "paused_s": p_s, "peak_mb": peak[0]}
    ok = rc == 0 and (d / "Outputs/perim.shp").exists()
    return {"ok": ok, "rc": rc, "err": err if not ok else "", "run_s": run_s, "paused_s": p_s,
            "peak_mb": peak[0]}


def arrival_from_perims(d: Path, case, start_mask: np.ndarray, meta: dict) -> np.ndarray:
    """Arrival minutes from the case start (inf = unburned) on the harness grid, from WISE's
    timed perimeters: a cell arrives at the first output time whose perimeter contains its
    centre. ``start_mask`` cells (FireSim's start area) are 0, so both models exclude the same
    start area when scored. After the burning period the last perimeter holds."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    dom = case.domain
    src_t = from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat)
    g = gpd.read_file(d / "Outputs/perim.shp").to_crs(4326)
    arr = np.full((dom.rows, dom.cols), np.inf)
    times = sorted(set(g["TIME"])) if "TIME" in g else []
    date = case.start.date()
    for tl in times:
        hh, mm = (int(x) for x in str(tl).split(":")[:2])
        ts = datetime(date.year, date.month, date.day, hh, mm)
        minutes = (ts - case.start).total_seconds() / 60.0
        sel = g[g["TIME"] == tl]
        mask = rasterize([(geom, 1) for geom in sel.geometry if geom is not None],
                         out_shape=(dom.rows, dom.cols), transform=src_t, dtype=np.uint8).astype(bool)
        new = mask & ~np.isfinite(arr)
        arr[new] = minutes
    arr[start_mask] = 0.0
    return arr
