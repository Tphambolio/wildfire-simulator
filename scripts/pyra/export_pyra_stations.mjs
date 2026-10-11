#!/usr/bin/env node
/**
 * Export Pyra's Alberta station list (ALBERTA_STATIONS, in Pyra's own sorted order) to the
 * FireSim API module api/src/firesim_api/services/pyra_stations.py.
 *
 * Pyra (https://github.com/Tphambolio/FWI) is read only: its engine is loaded in a Node vm
 * through Pyra's own test harness (tests/_harness.mjs); nothing in the Pyra checkout is written.
 *
 *   PYRA_DIR=/home/rpas/dev/FWI node scripts/pyra/export_pyra_stations.mjs
 *
 * The order matters: Pyra's pin-drop "nearest station" keeps the first of equally near
 * stations (strict <), so FireSim keeps the same order.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const PYRA_DIR = process.env.PYRA_DIR || '/home/rpas/dev/FWI';
const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', '..', 'api', 'src', 'firesim_api', 'services', 'pyra_stations.py');

function pyraCommit() {
  const head = readFileSync(join(PYRA_DIR, '.git', 'HEAD'), 'utf8').trim();
  if (!head.startsWith('ref: ')) return head;
  const ref = head.slice(5);
  try { return readFileSync(join(PYRA_DIR, '.git', ref), 'utf8').trim(); } catch (_) { /* packed */ }
  const packed = readFileSync(join(PYRA_DIR, '.git', 'packed-refs'), 'utf8');
  const line = packed.split('\n').find(l => l.endsWith(' ' + ref));
  return line ? line.split(' ')[0] : 'unknown';
}

const { makeContext } = await import(pathToFileURL(join(PYRA_DIR, 'tests', '_harness.mjs')).href);
const { run } = makeContext(join(PYRA_DIR, 'fwi.js'), { now: Date.now() });
const stations = JSON.parse(run('JSON.stringify(ALBERTA_STATIONS.map(s => [s.name, s.lat, s.lng]))'));
const sha = pyraCommit();

const rows = stations.map(([n, la, lo]) => `    (${JSON.stringify(n)}, ${la}, ${lo}),`).join('\n');
writeFileSync(out, `"""Pyra's Alberta station list (generated — do not edit by hand).

Exported from Pyra's \`fwi.js\` \`ALBERTA_STATIONS\` (Pyra's own sorted order) at Pyra commit
${sha} by \`scripts/pyra/export_pyra_stations.mjs\`. Pyra's station picker and pin-drop map use
this list; FireSim's "pyra" fire-weather tier picks the station from it the same way.
"""

PYRA_COMMIT = "${sha}"

# (name, lat, lng)
ALBERTA_STATIONS: tuple[tuple[str, float, float], ...] = (
${rows}
)
`);
console.log(`wrote ${stations.length} stations (Pyra ${sha.slice(0, 7)}) to ${out}`);
