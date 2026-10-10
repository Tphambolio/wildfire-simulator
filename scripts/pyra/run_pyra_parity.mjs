#!/usr/bin/env node
/**
 * Run Pyra's own browser engine (read only) on the parity fixture and print Pyra's results.
 *
 *   PYRA_DIR=/home/rpas/dev/FWI node scripts/pyra/run_pyra_parity.mjs api/tests/fixtures/pyra_parity.json
 *
 * Each case pins the clock and serves the fixture's recorded inputs (CWFIS features filtered by
 * the request's bbox, Pyra's cwfis_prev.json, the GEM hourly payload) through Pyra's own Node test
 * harness (tests/_harness.mjs: vm context, America/Edmonton viewer clock, URL-routed fetch mock).
 * The station is chosen as Pyra's pin-drop does (nearest in ALBERTA_STATIONS, first of ties), then
 * Pyra's initFWI(lat, lng, name) runs exactly as on the station page; its result (_lastFWI) is
 * printed. "equations" cases call Pyra's calculateFWI directly.
 *
 * Output (stdout): {"pyra_commit": ..., "cases": {id: {...}}, "equations": [...]}
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const PYRA_DIR = process.env.PYRA_DIR || '/home/rpas/dev/FWI';
const fixturePath = process.argv[2];
if (!fixturePath) { console.error('usage: run_pyra_parity.mjs <fixture.json>'); process.exit(2); }
const fx = JSON.parse(readFileSync(fixturePath, 'utf8'));

const { makeContext, lit } = await import(pathToFileURL(join(PYRA_DIR, 'tests', '_harness.mjs')).href);
const enginePath = join(PYRA_DIR, 'fwi.js');

function pyraCommit() {
  const head = readFileSync(join(PYRA_DIR, '.git', 'HEAD'), 'utf8').trim();
  if (!head.startsWith('ref: ')) return head;
  try { return readFileSync(join(PYRA_DIR, '.git', head.slice(5)), 'utf8').trim(); } catch (_) { return 'unknown'; }
}

/** CWFIS WFS mock: the feature set filtered by the request's CQL bbox, first `count`. */
function cwfisFor(set, transform) {
  return url => {
    if (set === 'error') return new Error('CWFIS down (fixture)');
    const feats = structuredClone(fx.feature_sets[set] ?? []);
    if (transform === 'rep_date_today') feats.forEach(f => { if (f.properties.rep_date) f.properties.rep_date = `${fx.today}T12:00:00Z`; });
    if (transform === 'strip_codes') feats.forEach(f => { for (const k of ['ffmc', 'dmc', 'dc', 'isi', 'bui', 'fwi']) f.properties[k] = null; });
    const u = new URL(url);
    const cql = (u.searchParams.get('CQL_FILTER') || '').replace(/\+/g, ' ');
    const m = /lat BETWEEN (\S+) AND (\S+) AND lon BETWEEN (\S+) AND (\S+)/.exec(cql);
    const count = +(u.searchParams.get('count') || 1e9);
    const inBox = m ? feats.filter(f => {
      const p = f.properties;
      return +p.lat >= +m[1] && +p.lat <= +m[2] && +p.lon >= +m[3] && +p.lon <= +m[4];
    }) : feats;
    return { type: 'FeatureCollection', features: inBox.slice(0, count) };
  };
}

function openMeteoFor(gemSet) {
  return url => {
    if (url.includes('/v1/elevation')) return { elevation: [700] };
    const u = new URL(url);
    // Only Pyra's fetchWeather request (past_days=1 & forecast_days=2) is served; other
    // Open-Meteo calls (forecast chart, hourly) are not part of the station-page codes.
    if (u.searchParams.get('past_days') !== '1' || u.searchParams.get('forecast_days') !== '2') return { $status: 404 };
    const key = `${u.searchParams.get('latitude')},${u.searchParams.get('longitude')}`;
    const p = fx.gem_sets[gemSet]?.[key];
    return p ?? { $status: 404 };
  };
}

const out = { pyra_commit: pyraCommit(), cases: {}, equations: [] };

for (const c of fx.cases) {
  const now = Date.parse(c.now);
  const prev = c.prev === 'error' ? { $status: 500 } : structuredClone(fx.prev_sets[c.prev]);
  if (c.prev_rep_date) Object.values(prev.stations).forEach(v => { v.repDate = `${c.prev_rep_date}T12:00:00Z`; });
  const { run } = makeContext(enginePath, {
    now,
    mocks: { cwfis: cwfisFor(c.cwfis, c.cwfis_transform), prev, openmeteo: openMeteoFor(c.gem) },
  });
  const st = JSON.parse(run(`JSON.stringify((() => {
    let n = null, m = Infinity;
    getStationList().forEach(s => { const d = _haversineKm(${c.lat}, ${c.lng}, s.lat, s.lng); if (d < m) { m = d; n = s; } });
    return { name: n.name, lat: n.lat, lng: n.lng, distKm: m };
  })())`));
  await run(`initFWI(${st.lat}, ${st.lng}, ${lit(st.name)})`);
  const r = JSON.parse(run('JSON.stringify(_lastFWI ?? null)'));
  out.cases[c.id] = {
    station: st,
    inactive: !!r?._inactive,
    ffmc: r?.ffmc ?? null, dmc: r?.dmc ?? null, dc: r?.dc ?? null,
    isi: r?.isi ?? null, bui: r?.bui ?? null, fwi: r?.fwi ?? null,
    peak: r?.peak ? { wind: r.peak.wind, isi: r.peak.isi, fwi: r.peak.fwi } : null,
    weather: r?.weather ? { temp: r.weather.temp, rh: r.weather.rh, wind: r.weather.wind, rain: r.weather.rain,
      source: r.weather.source, preNoonForecast: !!r.weather.preNoonForecast, fwiFromCWFIS: !!r.weather.fwiFromCWFIS,
      stationName: r.weather.stationName ?? null, repDate: r.weather.repDate ?? null } : null,
    carry: r?._cachedFWI ? { src: r._cachedFWI.src, obsDate: r._cachedFWI.obsDate, ageDays: r._cachedFWI.ageDays,
      final: r._cachedFWI.final, stationName: r._cachedFWI.stationName ?? null } : null,
  };
}

// Equations only: Pyra calculateFWI (non-CWFIS branch) on fixed inputs
{
  const { run } = makeContext(enginePath, { now: Date.parse('2026-07-15T18:00:00Z') });
  for (const e of fx.equations ?? []) {
    const w = { temp: e.temp, rh: e.rh, wind: e.wind, rain: e.rain, month: e.month, fwiFromCWFIS: false };
    const r = JSON.parse(run(`JSON.stringify(calculateFWI(${lit(w)}, ${lit(e.prev)}))`));
    out.equations.push({ id: e.id, ffmc: r.ffmc, dmc: r.dmc, dc: r.dc, isi: r.isi, bui: r.bui, fwi: r.fwi });
  }
}

process.stdout.write(JSON.stringify(out, null, 1) + '\n');
