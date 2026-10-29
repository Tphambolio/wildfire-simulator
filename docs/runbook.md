# FireSim operations runbook

For whoever is on watch while the apps run unattended. Monitoring for both FireSim and Pyra lives in
the Pyra repository (Tphambolio/FWI: `.github/workflows/uptime.yml`, `tools/uptime/check.mjs`,
`docs/RUNBOOK.md`); it polls `/api/v1/health`, `/api/v1/version` and the Vercel front end every
30 minutes and opens one GitHub issue labelled `uptime` per incident.

## What is running

| Part | Where | Size / cost | Deploys |
|---|---|---|---|
| API + engine | Fly.io app `firesim-api`, region `yyz`, one machine, shared CPU, 2 GB RAM; suspends when idle (`auto_stop_machines = "suspend"`, `min_machines_running = 0`) and wakes on the first request | about $1–3/month when idle | GitHub Actions `deploy` job on every push to `master` that touches `api/`, `engine/` or `data/`: `flyctl deploy --remote-only --build-arg GIT_SHA=…` (about 6 min). A deploy restarts the API and loses runs in progress. |
| Run archive | Fly volume `firesim_archive`, 1 GB at `/data`; SQLite index + payloads, capped at 800 MB (`FIRESIM_ARCHIVE_MAX_BYTES`), oldest dropped first; retention sweep every 24 h | about $0.15/month | survives deploys; **not** backed up off the volume (R2 + Litestream are designed but need an owner-created key; `docs/run-archive.md`) |
| Front end | Vercel project `wildfire-simulator`, builds `frontend/` from `master`; PR previews behind Vercel login | free tier | every push to `master` |
| Secrets | Fly secret `FIRESIM_RUNS_PASSPHRASE` (saved-runs gate; value known to the owner only) | — | `flyctl secrets set` redeploys |

Data files (Edmonton 10 m fuel grid, Alberta 100 m grid, DEM, buildings, hamlets, assets) are baked
into the API image by `api/Dockerfile`; nothing is fetched at runtime except weather (Open-Meteo GEM,
CWFIS) when a run asks for it.

## First five minutes

```bash
export PATH="$HOME/.fly/bin:$PATH"          # flyctl is not on PATH on the workstation
flyctl auth whoami || flyctl auth login     # login expires
flyctl status  -a firesim-api               # machine state, image, version
flyctl logs    -a firesim-api --no-tail | tail -100
curl -s https://firesim-api.fly.dev/api/v1/health ; echo
curl -s https://firesim-api.fly.dev/api/v1/version ; echo
```

A healthy answer is `{"status":"healthy", …, "archive":{"enabled":true,"durable":true,…}}` and a
`git_sha` equal to the head of `master`.

## Triage

| Symptom | Likely cause | Do |
|---|---|---|
| Health times out or 502/503, machine `stopped`/`suspended` and not waking | Fly platform incident, or the machine crashed on start (bad image, missing data file) | https://status.flyio.net. Then `flyctl machine list -a firesim-api`; `flyctl machine start <id>`; if it loops, `flyctl logs` shows the traceback → **rollback** (below). |
| Health OK but runs fail or hang; logs show `MemoryError`/OOM kill | A large multi-day ensemble on the single 2 GB machine | Runs are serialised (`FIRESIM_MAX_CONCURRENT_ENSEMBLES = 1`). One-off relief: `flyctl scale memory 4096 -a firesim-api` (doubles the machine cost while set; scale back after). Persistent: reduce the request window, not the machine. |
| `archive.durable` is `false` or `failures` > 0 | Volume not mounted (machine recreated without it) or full | `flyctl volumes list -a firesim-api`; the volume must show attached to the running machine. If the machine was recreated, `flyctl machine update <id> --mount-volume firesim_archive:/data` or destroy the machine and `flyctl deploy` (the config mounts it). Full: lower `FIRESIM_ARCHIVE_MAX_BYTES` or `flyctl volumes extend`. |
| Saved runs return 401 for everyone | Wrong passphrase, or the secret was unset by a redeploy from another config | The value is not written anywhere; the owner sets it again: `flyctl secrets set -a firesim-api FIRESIM_RUNS_PASSPHRASE='…'`. Shared EOC links (`?run=…`) do not need it. |
| Front end loads but every run errors with CORS | API redeployed with a different `FIRESIM_CORS_ORIGINS` or the Vercel domain changed | Set the origin in `fly.toml [env]` and deploy; previews use their own domain and are expected to fail CORS. |
| Front end 404/500 or shows an old build | Vercel build failed (type error) or Vercel incident | Vercel dashboard → Deployments: read the build log; **Promote** the last good production deployment; fix on a branch. `cd frontend && npm run build` must be zero-error before merging. |
| Deploy job red in Actions | Fly token expired/rotated, remote builder failed, or tests failed | Open the run. Token: regenerate (`flyctl tokens create deploy -a firesim-api`) and store as the `FLY_API_TOKEN` repository secret. Builder: re-run the job. Tests: fix on a branch. |
| Weather fetch failures in logs (Open-Meteo 429/5xx, CWFIS down) | Upstream outage or free-tier throttling | Runs still work with manual weather. Repeated 429 means the free tier is being throttled; the business plan requires moving to ECCC GeoMet or a paid plan before commercial use. |
| Times in the UI one hour off after 2026-11-01 | Stale browser tz data | Handled by `frontend/src/utils/time.ts` (dated Alberta rule, fixed UTC−6). If seen, it is an old cached bundle: hard-reload. |
| Fly bill higher than expected | Machine not suspending (a WebSocket kept it awake), or memory scaled up and not reverted | `flyctl status`; `flyctl scale show -a firesim-api`; return to `shared-cpu-1x`, 2 GB. |

## Rollback

```bash
flyctl releases -a firesim-api                       # find the last good version (vNNN)
flyctl releases -a firesim-api --image               # image refs per release
flyctl deploy -a firesim-api --image <good-image-ref>   # redeploy that image, no rebuild
```
Front end: Vercel → Deployments → previous production build → Promote to Production. Then revert the
offending commit on `master` by PR so the next deploy is clean (master is protected; the owner merges).

## Release checklist

1. On a branch: `make test` (engine + API, pytest) — one full FireSim suite at a time on the
   workstation (`flock /tmp/firesim-tests.lock …`); `cd frontend && npx tsc --noEmit && npm test`;
   Playwright e2e where the change touches the map or the run flow.
2. PR; required checks `engine-tests (3.11)`, `engine-tests (3.12)`, `frontend` green; the owner merges.
3. Watch the `deploy` job; `curl …/api/v1/version` shows the merged SHA; the uptime check passes.
4. `docs/PROJECT_RECORD.md` work-log row; a dated validation scorecard for tagged releases
   (`docs/validation-scorecard.md`).

## Seasonal and standing items

- **Before April:** confirm the Alberta Wildfire fuel grid and ECCC endpoints still answer; re-run the
  small validation fixture (`engine/tests/validation`); check `FIRESIM_AB_FWI_LST_OFFSET_H` against
  the CWFIS noon-hour probe result in the Pyra repo (`tools/tz/cwfis_noon_probe.mjs`).
- **Monthly:** `flyctl auth whoami` still valid on the workstation; archive bytes vs cap in `/health`.
- **Open:** off-volume backup of the run archive (R2 key, owner); front-end error reporting is not
  collected anywhere — users report by the feedback path described in the business plan.

## Access needed

Fly.io account (owner's e-mail) with the `firesim-api` app; GitHub repository admin (Actions secrets
`FLY_API_TOKEN`); Vercel project access. The saved-runs passphrase is the owner's alone.
