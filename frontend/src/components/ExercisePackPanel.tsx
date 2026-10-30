/**
 * Scenario packs in Setup (exercise mode only; docs/exercise-mode-spec.md §5, PR2): load one
 * of the repository's example packs, a pack file (import), or a pack stored on this server
 * (facilitator passphrase; or by its 32-hex ID without one); export the loaded pack as a file;
 * save it to the server. Loading sets Setup, the incident periods and the exercise clock, and
 * schedules the pack's injects (App.tsx `loadExercisePack`); it does not start the run.
 * Everything here is marked EXERCISE. No addresses, no evacuation tiers.
 */

import { memo, useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import type { PackSummary, ScenarioPack } from "../types/exercise";
import { checkPack, packFileName, parsePack } from "../exercise/packs";
import {
  deletePack, ExerciseAuthError, fetchExamplePacks, fetchPack, getExercisePassphrase, listPacks, savePack,
  setExercisePassphrase,
} from "../services/exercisePacks";
import { formatClock, formatDate, zoneAbbrev } from "../utils/time";
import "../styles/exercisePacks.css";

interface ExercisePackPanelProps {
  loaded: ScenarioPack | null;
  /** Approximations made when the pack was loaded (packLoadPlan notes) */
  notes: string[];
  onLoad: (pack: ScenarioPack) => void;
  onUnload: () => void;
}

const HEX32 = /^[0-9a-f]{32}$/;

function packLine(p: { startIso: string | null; durationHours: number | null }): string {
  const t = p.startIso ? new Date(p.startIso) : null;
  const when = t && Number.isFinite(t.getTime()) ? `${formatDate(t)} ${formatClock(t)} ${zoneAbbrev(t)}` : "";
  return [when, p.durationHours ? `${p.durationHours} h` : ""].filter(Boolean).join(" · ");
}

function ExercisePackPanel({ loaded, notes, onLoad, onUnload }: ExercisePackPanelProps) {
  const id = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [examples, setExamples] = useState<ScenarioPack[] | null>(null);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [unlocked, setUnlocked] = useState(() => !!getExercisePassphrase());
  const [pass, setPass] = useState("");
  const [stored, setStored] = useState<PackSummary[] | null>(null);
  const [openId, setOpenId] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    fetchExamplePacks()
      .then((p) => live && setExamples(p))
      .catch(() => live && setExamples([]));
    return () => {
      live = false;
    };
  }, []);

  const fail = useCallback((err: unknown) => {
    if (err instanceof ExerciseAuthError) {
      setUnlocked(false);
      setStored(null);
    }
    setMessage({ text: (err as Error).message, error: true });
  }, []);

  const refresh = useCallback(() => {
    listPacks().then(setStored).catch(fail);
  }, [fail]);
  useEffect(() => {
    if (unlocked) refresh();
  }, [unlocked, refresh]);

  const load = (raw: unknown, from: string) => {
    const r = checkPack(raw);
    if (r.error !== undefined) {
      setMessage({ text: r.error, error: true });
      return;
    }
    onLoad(r.pack);
    setMessage({ text: `Loaded ${from}` });
  };

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    const r = parsePack(await file.text());
    if (r.error !== undefined) setMessage({ text: r.error, error: true });
    else load(r.pack, `from ${file.name}`);
    if (fileRef.current) fileRef.current.value = "";
  };

  const exportFile = () => {
    if (!loaded) return;
    const blob = new Blob([`${JSON.stringify(loaded, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = packFileName(loaded);
    a.click();
    URL.revokeObjectURL(url);
  };

  const unlock = (e: FormEvent) => {
    e.preventDefault();
    setExercisePassphrase(pass.trim());
    setPass("");
    setMessage(null);
    setUnlocked(true);
  };

  const saveLoaded = async () => {
    if (!loaded) return;
    setBusy(true);
    try {
      const r = await savePack(loaded);
      setMessage({ text: `Saved on the server as ${r.id}` });
      refresh();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const openById = async (e: FormEvent) => {
    e.preventDefault();
    const v = openId.trim().toLowerCase();
    if (!HEX32.test(v)) {
      setMessage({ text: "A pack ID is 32 hexadecimal characters", error: true });
      return;
    }
    try {
      load(await fetchPack(v), `pack ${v}`);
    } catch (err) {
      fail(err);
    }
  };

  return (
    <div className="exercise-packs" data-testid="exercise-packs">
      <p className="exercise-packs-mark"><span className="exercise-mark-sm">EXERCISE</span> Scripted scenarios for training, not predictions.</p>

      {loaded ? (
        <div className="exercise-pack-loaded" data-testid="exercise-pack-loaded">
          <div className="exercise-pack-title">{loaded.title}</div>
          <div className="hint-sm">
            {packLine(loaded)} · {loaded.periods.length} period{loaded.periods.length === 1 ? "" : "s"} · {loaded.scheduledInjects.length} inject{loaded.scheduledInjects.length === 1 ? "" : "s"} scheduled
          </div>
          {notes.length > 0 && (
            <ul className="exercise-pack-notes hint-sm">
              {notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
          )}
          <details className="exercise-pack-details">
            <summary>Sources and limits</summary>
            <ul className="hint-sm">
              {loaded.sources.map((s) => <li key={s}>{s}</li>)}
              {(loaded.caveats ?? []).map((c) => <li key={c}>{c}</li>)}
            </ul>
          </details>
          <div className="exercise-pack-actions">
            <button type="button" className="btn-control" onClick={exportFile}>Export file</button>
            {unlocked && (
              <button type="button" className="btn-control" onClick={saveLoaded} disabled={busy}>Save to server</button>
            )}
            <button type="button" className="btn-control" onClick={onUnload}>Unload</button>
          </div>
        </div>
      ) : (
        <p className="hint-sm">No pack loaded. Loading one sets Setup, the operational periods and the exercise clock; press Run when the pre-brief is done.</p>
      )}

      <h4 className="exercise-packs-h">Example packs</h4>
      {examples === null ? (
        <p className="hint-sm">Loading…</p>
      ) : examples.length === 0 ? (
        <p className="hint-sm">No example packs on this server.</p>
      ) : (
        <ul className="exercise-pack-list">
          {examples.map((p) => (
            <li key={p.id}>
              <span className="exercise-pack-name">{p.title.replace(/^EXERCISE:\s*/, "")}</span>
              <span className="hint-sm">{packLine(p)}</span>
              <button type="button" className="btn-control" onClick={() => load(p, p.title)} aria-label={`Load pack ${p.title}`}>
                Load
              </button>
            </li>
          ))}
        </ul>
      )}

      <h4 className="exercise-packs-h">From a file</h4>
      <input
        ref={fileRef}
        id={`${id}-file`}
        type="file"
        accept="application/json,.json"
        className="visually-hidden"
        onChange={(e) => void importFile(e.target.files?.[0])}
        data-testid="exercise-pack-file"
      />
      <button type="button" className="btn-control" onClick={() => fileRef.current?.click()}>Import pack file…</button>

      <h4 className="exercise-packs-h">On this server</h4>
      <form className="exercise-pack-open" onSubmit={openById} aria-label="Open a pack by ID">
        <label className="field" htmlFor={`${id}-open`}>
          Pack ID
          <input id={`${id}-open`} value={openId} onChange={(e) => setOpenId(e.target.value)} spellCheck={false} autoComplete="off" />
        </label>
        <button type="submit" className="btn-control" disabled={!openId.trim()}>Open</button>
      </form>
      {!unlocked ? (
        <form className="exercise-pack-lock" onSubmit={unlock} aria-label="Unlock stored packs">
          <label className="field" htmlFor={`${id}-pass`}>
            Facilitator passphrase
            <input id={`${id}-pass`} type="password" autoComplete="current-password" value={pass} onChange={(e) => setPass(e.target.value)} />
          </label>
          <button type="submit" className="btn-primary btn-inline" disabled={!pass.trim()}>Show stored packs</button>
        </form>
      ) : stored === null ? (
        <p className="hint-sm">Loading…</p>
      ) : stored.length === 0 ? (
        <p className="hint-sm">No packs stored on this server.</p>
      ) : (
        <ul className="exercise-pack-list" aria-label="Stored packs">
          {stored.map((p) => (
            <li key={p.id}>
              <span className="exercise-pack-name">{p.title.replace(/^EXERCISE:\s*/, "")}</span>
              <span className="hint-sm">{packLine(p)}</span>
              <button type="button" className="btn-control" onClick={() => fetchPack(p.id).then((x) => load(x, p.title)).catch(fail)}>Load</button>
              <button
                type="button"
                className="btn-control"
                aria-label={`Delete pack ${p.title}`}
                onClick={() => {
                  if (window.confirm(`Delete the pack "${p.title}" from the server?`)) deletePack(p.id).then(refresh).catch(fail);
                }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}

      {message && (
        <div className={`hint-sm${message.error ? " text-danger" : ""}`} role={message.error ? "alert" : "status"}>{message.text}</div>
      )}
    </div>
  );
}

export default memo(ExercisePackPanel);
