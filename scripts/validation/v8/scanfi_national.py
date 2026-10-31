"""Local copy of the SCANFI v2 national layers read by the LR8 draw (owner decision 2026-10-30, PREREG_LR8C A4).

The draw back-casts NBAC burns with SCANFI v2 (release 20260119), epochs 2015 and 2020, the 13 layers of
``scripts.fuelgrid.backcast.LAYERS``. Until 2026-10-30 each fire read a clip of the national files from the NRCan
server (``inputs.scanfi_local``). The server slowed to about 2 fires/h, so the same files are downloaded once, unchanged,
from the same official open URLs (``backcast.SCANFI``), and the draw reads its clips from the local copy.

``python3 -m v8.scanfi_national download`` fetches each file in 16 MB byte ranges (4 threads; a range slower than
10 kB/s over 60 s is dropped and retried; resumable),
checks its size against Content-Length, writes its sha256, and records URL, size and sha256 in ``_about.json`` and
README.md of the folder. Licence: Open Government Licence - Canada (NRCan). Nothing here reads a sealed file or an
outcome.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import common as C

NATIONAL = C.DATA / "raw" / "scanfi_v2_national"
EPOCHS = (2015, 2020)
CHUNK = 16 * 1024 * 1024
THREADS = 4
STALL_BPS = 10_000   # a range read slower than this over STALL_S is dropped and retried (the server trickles some)
STALL_S = 60
MIN_FREE_GB = 20.0
PAUSE_FREE_GB = 25.0  # pause (resumable) when free space nears the 20 GB floor, draw output included


def urls() -> list[tuple[int, str, str]]:
    from scripts.fuelgrid.backcast import SCANFI, LAYERS
    return [(e, l, SCANFI.format(layer=l, epoch=e)) for e in EPOCHS for l in LAYERS]


def local_path(layer: str, epoch: int) -> Path:
    return NATIONAL / Path(urls_map()[(epoch, layer)]).name


def urls_map() -> dict:
    return {(e, l): u for e, l, u in urls()}


def _size(u: str) -> int:
    for k in range(10):
        try:
            r = requests.head(u, allow_redirects=True, timeout=60)
            r.raise_for_status()
            return int(r.headers["Content-Length"])
        except Exception:
            time.sleep(10 * (k + 1))
    raise RuntimeError(f"no Content-Length for {u}")


def _chunk(u: str, part: Path, a: int, b: int, lock: threading.Lock) -> None:
    for k in range(40):
        try:
            with requests.get(u, headers={"Range": f"bytes={a}-{b}"}, stream=True, timeout=(30, 60)) as r:
                if r.status_code != 206:
                    raise RuntimeError(f"HTTP {r.status_code}")
                buf = bytearray()
                t0 = time.time()
                for blk in r.iter_content(1 << 16):
                    buf += blk
                    dt = time.time() - t0
                    if dt > STALL_S and len(buf) / dt < STALL_BPS:
                        raise RuntimeError(f"stalled at {len(buf) / dt:.0f} B/s")
            if len(buf) != b - a + 1:
                raise RuntimeError(f"short read {len(buf)} of {b - a + 1}")
            with lock, open(part, "r+b") as f:
                f.seek(a)
                f.write(buf)
            return
        except Exception:
            time.sleep(min(120, 5 * (k + 1)))
    raise RuntimeError(f"chunk {a}-{b} of {u} failed 40 times")


def fetch(u: str) -> dict:
    out = NATIONAL / Path(u).name
    meta_p = out.with_suffix(".tif.json")
    if out.exists() and meta_p.exists():
        return json.loads(meta_p.read_text())
    n = _size(u)
    part = out.with_suffix(".tif.part")
    state_p = out.with_suffix(".tif.chunks")
    done = set(json.loads(state_p.read_text())) if state_p.exists() else set()
    if not part.exists():
        with open(part, "wb") as f:
            f.truncate(n)
    spans = [(a, min(n, a + CHUNK) - 1) for a in range(0, n, CHUNK)]
    todo = [s for s in spans if s[0] not in done]
    lock = threading.Lock()
    t = time.time()
    with cf.ThreadPoolExecutor(THREADS) as ex:
        futs = {ex.submit(_chunk, u, part, a, b, lock): a for a, b in todo}
        for fu in cf.as_completed(futs):
            fu.result()
            done.add(futs[fu])
            if shutil.disk_usage(NATIONAL).free / 1e9 < PAUSE_FREE_GB:  # the .part files are sparse: usage grows
                with lock:
                    state_p.write_text(json.dumps(sorted(done)))
                ex.shutdown(cancel_futures=True)
                raise SystemExit(f"PAUSED_DISK: {shutil.disk_usage(NATIONAL).free / 1e9:.1f} GB free < {PAUSE_FREE_GB}")
            with lock:
                state_p.write_text(json.dumps(sorted(done)))
    if part.stat().st_size != n:
        raise RuntimeError(f"size {part.stat().st_size} != {n} for {u}")
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for blk in iter(lambda: f.read(1 << 24), b""):
            h.update(blk)
    part.rename(out)
    state_p.unlink()
    rec = dict(url=u, file=out.name, bytes=n, sha256=h.hexdigest(),
               downloaded_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               seconds=round(time.time() - t))
    meta_p.write_text(json.dumps(rec, indent=1))
    return rec


def write_about(recs: list[dict]) -> None:
    about = dict(
        name="SCANFI v2 national layers (release 20260119), epochs 2015 and 2020, 13 layers",
        purpose="Local copy for the LR8 held-out draw (PREREG_LR8C A4; owner decision 2026-10-30): the same files the "
                "draw read by HTTP range from the NRCan server, downloaded unchanged.",
        source="Natural Resources Canada, Canadian Forest Service: SCANFI (Spatialized CAnadian National Forest "
               "Inventory) v2, https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/",
        licence="Open Government Licence - Canada",
        code="scripts/validation/v8/scanfi_national.py",
        files=recs)
    (NATIONAL / "_about.json").write_text(json.dumps(about, indent=1))
    rows = "\n".join(f"| {r['file']} | {r['bytes']:,} | `{r['sha256']}` |" for r in recs)
    (NATIONAL / "README.md").write_text(
        "# SCANFI v2 national layers (local copy)\n\n"
        f"{about['purpose']}\n\n- Source: {about['source']}\n- Licence: {about['licence']}\n"
        f"- Code: `{about['code']}` (16 MB byte ranges, size checked against Content-Length, sha256 below)\n"
        "- URL of each file: the source folder + file name (also in `_about.json` and each `<file>.json`).\n\n"
        "| File | Bytes | sha256 |\n|---|---|---|\n" + rows + "\n")


def download() -> None:
    NATIONAL.mkdir(parents=True, exist_ok=True)
    U = urls()
    need = sum(_size(u) for _, _, u in U if not (NATIONAL / Path(u).name).exists())
    free = shutil.disk_usage(NATIONAL).free
    if (free - need) / 1e9 < MIN_FREE_GB:
        raise SystemExit(f"would leave {(free - need) / 1e9:.1f} GB free < {MIN_FREE_GB}")
    recs = []
    for e, l, u in U:
        r = fetch(u)
        print(r["file"], r["bytes"], r["sha256"][:12], r["seconds"], "s", flush=True)
        recs.append(r)
    write_about(recs)
    print("DOWNLOAD_DONE", len(recs), flush=True)


if __name__ == "__main__":
    C.guard()
    download() if sys.argv[1:] == ["download"] else print(json.dumps(urls_map().__len__()))
