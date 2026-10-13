# Run archive (phases 1 and 2)

Finished runs survive API deploys and restarts. Before this, a run lived only in the API's
memory: a deploy, a machine restart, or 40 newer runs (`FIRESIM_MAX_STORED_RUNS`) lost it,
and "Load Day N run" in the EOC console could only say "no longer on the server".
Design: storage proposal option A, phase 1, approved by the owner 2026-10-11 (report [R28] §6).
Code: `api/src/firesim_api/services/archive.py`.

## What is stored

When a run's thread ends in a terminal state (completed, failed or cancelled, and after its
ensemble if it has one), it is queued for the archive. A single worker thread writes it, so the
run itself is never slowed. If archiving fails, the error is logged and counted in
`/api/v1/health` → `archive.failures`. It never changes the run's result.

| Item | Where | Content |
|---|---|---|
| Run index | SQLite, `FIRESIM_ARCHIVE_DB` (WAL mode) | One row per run: id, kind (`single`, `multiday`, `perimeter_override`), status, created / finished / archived time, request JSON (for an override: the source config and the override request), git SHA, fuel and buildings source kind and label, area (ha), duration (h), frame count, number of days, ensemble status, object names and total bytes |
| `<id>/response.json.gz` | object store | The body `GET /simulations/{id}` returned when the run finished: frames with `day`, `day_fwi` and `cells_offset` exactly as served live |
| `<id>/arrival.json.gz` | object store | The `/arrival` body (grid runs only) |
| `<id>/ensemble.json.gz` | object store | The `/ensemble` body (runs with an ensemble only) |
| `<id>/meta.json` | object store | A copy of the index row, so the index can be rebuilt from the store |

Measured sizes: a 0.5 h uniform-fuel run is 2.7 kB of gzip JSON plus 1.5 kB of metadata. A live
2-day Edmonton-grid GET was 1.08 MB uncompressed ([R28]); gzip JSON typically compresses
that kind of body 5-10×.

**Privacy.** Only model output and the request are stored: no user, no address, no personal
data. The response is the live GET body, unchanged. Its per-building fields are the ones the
live GET already returns:

- `building_exposure_detail`: building centroid, distance and flux, within 500 m.
- `structure_spread_detail`: illustrative footprint polygons.

Both come from the public Microsoft footprints, have no address, owner or parcel data, and
are for map display only. No export or ICS 209 reads the archive. A reloaded run goes back
into the app, where the existing export rules apply.

## Reading

`GET /simulations/{id}`, `/arrival` and `/ensemble` look in memory first, then in the archive.

- **Archived GET.** It is validated through the same response model, so its shape is identical
  to the live GET. It carries `"archived": true`; live responses carry `"archived": false`.
- **Payloads without an index row.** If the index is gone (for example, an index on a
  temporary disk), the run is still read directly from the object store.
- **Unreadable store.** If the store can't be reached, the API answers 404 as before, never 500.
- **What the archive doesn't serve.** The WebSocket, `DELETE /ensemble` and a perimeter-override
  restart from an archived source still need the run in memory (open item).

In the app, "Load Day N run" shows "Run X · Loaded from archive". If a run is in neither memory
nor the archive, the app says so: "no longer on the server or in its run archive".

## Phase 2: saved-runs list (2026-10-12)

Owner decision 2026-10-12 (Travis Kennedy, EOC Chief of Plans): the list of runs is **shared
behind one passphrase**. Code: `api/src/firesim_api/routers/runs.py`; app: Setup → Saved runs
(`SavedRunsPanel.tsx`, `hooks/useSavedRuns.ts`, `services/savedRuns.ts`).

**Endpoints** (details in `docs/api-reference.md`):

- `GET /api/v1/simulations`: archived runs plus runs still in memory and not archived yet
  (`archived: false`), newest first; `limit` (≤ 200) / `offset`; filters `since` / `until`,
  a box `south`/`west`/`north`/`east` around the ignition point, `kind`.
- `PATCH /api/v1/simulations/{id}` `{"label": ...}`: set, rename or clear a label.
- `DELETE /api/v1/simulations/{id}`: remove a finished run (index row, label, payloads) and
  drop it from memory. Refused (409) while the run or its ensemble is still running.

**Passphrase gate.** All three need the header `X-FireSim-Passphrase`. The server compares the
SHA-256 of the header with the SHA-256 of `FIRESIM_RUNS_PASSPHRASE` (`hmac.compare_digest`) and
never logs either.

- **Variable unset:** the three endpoints answer 403 ("disabled on this server"), so nothing is
  listed until the owner sets it.
- **Wrong or missing passphrase:** 401.
- **Backoff:** failures are counted per client IP in memory (Fly's `Fly-Client-IP`, else the
  socket peer). After 5 failures each further one locks that IP out for 2, 4, 8 ... seconds
  (at most 300 s); requests during the lock get 429 with `Retry-After`. A success clears the
  count. The counts reset when the API restarts.
- `GET /simulations/{id}`, `/arrival` and `/ensemble` stay open: the EOC console reloads its
  periods' runs by ID.

Setting it (owner only; the value is never committed or logged):

```bash
fly secrets set --app firesim-api FIRESIM_RUNS_PASSPHRASE='<choose-a-passphrase>'
```

Setting a secret restarts the API machine, and runs in progress are lost.

**What the list shows.** For each run: id, label, kind, status, submitted and finished times,
scenario start, ignition point rounded to 3 decimals (about 100 m), area, duration, frames,
days, fuel and buildings source labels, and ensemble status and size. It shows no address, no
request body and no per-building data.

**Labels.** A label is optional plain text of at most 80 characters. Control and
direction-override characters are removed and whitespace is collapsed.

- **At run time:** request field `label` on `POST /simulations`, `/multiday` and
  `/perimeter-override`; the app's "Run label (optional)" field in Setup. It is stored in the
  request JSON.
- **Later:** `PATCH`, stored in the index table `run_labels` (id, label, updated time). Adding
  that table is the only schema migration: `CREATE TABLE IF NOT EXISTS`, run when the index
  opens, with no change to the `runs` table. Because it is a separate table, re-archiving a
  run (`INSERT OR REPLACE` into `runs`) never drops its label.
- A rename to an empty label hides the run-time label as well.
- Renamed labels live in the index only, not in `meta.json`: an index rebuilt from the store
  would show the run-time labels.

**The app.** Setup → Saved runs asks for the passphrase once and keeps it in `sessionStorage`
only (this tab, wrapped in try/catch, so blocked storage keeps it in memory). It is cleared
on any 401 or 403.

- **The list:** 20 runs a page, with the submitted time (America/Edmonton), label, kind,
  ignition point, area, duration or days, fuel source, and an "Ensemble" tag.
- **Filters:** "Near current map view" sends the map's box. From / To are Edmonton local days,
  inclusive.
- **Opening a run:** clicking a run reloads it through `restoreRun` (memory first, then the
  archive), so the timeline, perimeter, exposure and range of outcomes show. The row is tagged
  "On map · Loaded from archive". The reloaded run is **not** filed under the open operational
  period; "Attach" in the EOC console does that on purpose.
- **Deleting:** asks for confirmation first and removes the run for everyone.

## Retention

`run_retention()` runs at startup and then every `FIRESIM_ARCHIVE_RETENTION_HOURS` (24) in
the app lifespan. Tests can also call it directly. It deletes:

1. Runs whose `finished_at` is older than `FIRESIM_ARCHIVE_MAX_AGE_DAYS` (default 365, the
   1-year training retention in [R28] §6; EOC records follow the City records schedule).
2. The oldest runs while the total is over `FIRESIM_ARCHIVE_MAX_BYTES` (0 = no cap).
3. Store objects with no index row whose timestamp is past the maximum age.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FIRESIM_ARCHIVE` | on | `0` / `off` disables the archive (old behaviour) |
| `FIRESIM_ARCHIVE_DIR` | `/data/firesim-archive` if `/data` is writable, else `<tmp>/firesim-archive` | Local root: local-backend payloads and the default index location |
| `FIRESIM_ARCHIVE_DB` | `<dir>/archive.db` | SQLite index |
| `FIRESIM_ARCHIVE_BACKEND` | `local` | `local` or `s3` (S3-compatible: Cloudflare R2) |
| `FIRESIM_ARCHIVE_S3_BUCKET`, `_ENDPOINT`, `_REGION` (`auto`), `_PREFIX` (`runs/`) | | Bucket settings. R2 endpoint: `https://<account-id>.r2.cloudflarestorage.com` |
| `FIRESIM_ARCHIVE_S3_ACCESS_KEY_ID`, `FIRESIM_ARCHIVE_S3_SECRET_ACCESS_KEY` | | Credentials (Fly secrets only, never in the repo) |
| `FIRESIM_ARCHIVE_MAX_AGE_DAYS` / `_MAX_BYTES` / `_RETENTION_HOURS` | 365 / 0 / 24 | Retention |
| `FIRESIM_LITESTREAM` | on | `0` skips Litestream even when credentials are set |
| `FIRESIM_RUNS_PASSPHRASE` | unset (list disabled) | Shared passphrase for the saved-runs list, rename and delete (phase 2; Fly secret only) |

Fallbacks:

- **No settings and no volume:** the archive uses a
  temporary directory and logs a warning. It survives process restarts but not deploys.
  `/api/v1/health` reports `"durable": false`.
- **`s3` without a bucket, credentials or `boto3`:** the archive falls back to `local` and
  logs a warning.
- **The directory can't be opened:** the archive is disabled and logs a warning. The API
  always starts.

## Litestream (index backup)

The image includes Litestream 0.5.17 (SHA-256 checked) and starts through
`api/deploy/entrypoint.sh`. When `FIRESIM_ARCHIVE_DB` and all `FIRESIM_ARCHIVE_S3_*` settings
are present, the entrypoint:

1. Checks the config (`api/deploy/litestream.yml`).
2. Restores the index from `s3://<bucket>/litestream/archive.db`, but only if no local DB
   exists. The restore is limited to 120 s, so an unreachable bucket doesn't hold up the API.
3. Runs uvicorn under `litestream replicate -exec`.

Without those settings it starts uvicorn directly and logs a warning. This was tested locally
against an S3 stub (moto server):

- payloads were written under `runs/<id>/`;
- the index was replicated;
- a new container with an empty volume restored the index;
- the run was served `archived: true`, identical to the live body except for that flag.

## Deployed setup (2026-10-12)

The owner chose a volume only, with no Cloudflare R2 and Fly spend under about $1/month: the
1 GB volume `firesim_archive` (yyz, about $0.15/month; Fly keeps daily snapshots for 5 days) is
mounted at `/data` in `fly.toml`, the archive uses `/data/firesim-archive` with the local
backend, and `FIRESIM_ARCHIVE_MAX_BYTES` caps it at 800 MB. `/api/v1/health` reports
`"durable": true, "backend": "local"`. Without R2 a lost volume loses the archive (the snapshots
are the only backup).

## Optional: R2 offsite copy (not deployed)

```bash
# 1. R2 bucket (Cloudflare dashboard, or: wrangler r2 bucket create firesim-run-archive)
#    and an R2 API token with Object Read & Write on that bucket only.
# 2. Volume (1 GB, same region as the machine)
fly volumes create firesim_archive --app firesim-api --region yyz --size 1
# 3. Secrets (values from the R2 token; never committed)
fly secrets set --app firesim-api \
  FIRESIM_ARCHIVE_BACKEND=s3 \
  FIRESIM_ARCHIVE_S3_BUCKET=firesim-run-archive \
  FIRESIM_ARCHIVE_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com \
  FIRESIM_ARCHIVE_S3_ACCESS_KEY_ID=... \
  FIRESIM_ARCHIVE_S3_SECRET_ACCESS_KEY=...
# 4. fly.toml (then deploy as usual)
#   [mounts]
#     source = "firesim_archive"
#     destination = "/data"
#   [env]
#     FIRESIM_ARCHIVE_DB = "/data/firesim-archive/archive.db"
```

A volume pins the app to one machine in yyz. That is the current setup (`min_machines_running = 0`,
one machine). R2 holds the payloads and the Litestream replica, so a lost volume loses
nothing: the next boot restores the index.

**Cost.** R2 storage costs $0.015/GB-month beyond the 10 GB free tier, and egress is free.
Class A operations (writes) cost $4.50 per million beyond 1 M/month free. A Fly volume costs
$0.15/GB-month. At 1,000 runs a month of about 200 kB each, that is about 0.2 GB a month,
2.4 GB after a year. Litestream adds a few writes per archived run plus periodic compaction, well under the 1 M free writes. The total is about $0.15 a
month (the volume), with R2 inside its free tier.
