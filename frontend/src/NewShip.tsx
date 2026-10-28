import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import BuildProgress, { type BuildStatus } from "./BuildProgress";
import HullPlan from "./HullPlan";
import Icon from "./Icons";
import type { VesselModel } from "./types/vessel-model";
import type { DraftLayout, DraftTank, DrawingFix, LayoutTank, Segment, SizeClass, TankKind, VesselLayout } from "./types/api";

interface Props {
  editId?: string;    // edit one of the user's ships
  copyFrom?: string;  // start from another ship's layout (e.g. the demo)
  onCancel: () => void;
  onSaved: (id: string) => void;
}

const SIZE_CLASSES: SizeClass[] = ["MR1", "MR2", "LR1", "LR2", "Handysize", "Other"];
const KINDS: TankKind[] = ["cargo", "slop", "ballast", "fuel_oil", "diesel_oil", "fresh_water"];
const PARTICULARS = [
  ["lbp", "LBP (m)"], ["breadth", "Breadth (m)"], ["depth", "Depth (m)"], ["summerDraught", "Summer draught (m)"],
] as const;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const EMPTY: DraftLayout = { name: "", sizeClass: "Other", lbp: null, breadth: null, depth: null, summerDraught: null, tanks: [] };

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

// Drop nulls so the backend sees only what was given; report what is still missing.
function complete(d: DraftLayout): { layout?: VesselLayout; missing: string[] } {
  const missing: string[] = [];
  if (!d.name?.trim()) missing.push("name");
  PARTICULARS.forEach(([k, label]) => { if (!isNum(d[k])) missing.push(label); });
  const tanks = d.tanks ?? [];
  if (!tanks.length) missing.push("at least one tank");
  tanks.forEach((t, i) => {
    if (!t.id || !t.type || !t.side || !isNum(t.xAft) || !isNum(t.xFwd)) missing.push(`tank row ${i + 1}`);
  });
  if (missing.length) return { missing };
  const clean = <T extends object>(o: T) =>
    Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "")) as T;
  return {
    missing,
    layout: { ...clean(d), sizeClass: d.sizeClass ?? "Other", tanks: tanks.map((t) => clean(t) as LayoutTank) } as VesselLayout,
  };
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export default function NewShip({ editId, copyFrom, onCancel, onSaved }: Props) {
  const [text, setText] = useState("");
  const [image, setImage] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [checks, setChecks] = useState<string[]>([]);
  const [fixes, setFixes] = useState<DrawingFix[]>([]);
  const [estimated, setEstimated] = useState<string[]>([]);
  const [reading, setReading] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftLayout>(EMPTY);
  const [preview, setPreview] = useState<VesselModel | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  // Progress of the current "Read with AI" run: which step, and how it ended.
  const [progress, setProgress] = useState<{ step: number; status: BuildStatus; startedAt: number; message?: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const loadLayout = (id: string, copy: boolean) =>
    api.layout(id)
      .then((l) => setDraft(copy ? { ...l, name: `Copy of ${l.name ?? "ship"}` } : l))
      .catch((e) => setReadError(`Couldn't load the layout: ${e.message}`));

  useEffect(() => {
    if (editId) loadLayout(editId, false);
    else if (copyFrom) loadLayout(copyFrom, true);
  }, [editId, copyFrom]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live preview, built by the backend from the layout so it matches what will be saved.
  useEffect(() => {
    const { layout, missing } = complete(draft);
    if (!layout) {
      setPreview(null); setPreviewing(false);
      setPreviewError(draft.tanks?.length ? `Still needed: ${missing.join(", ")}` : null);
      return;
    }
    let stale = false;
    setPreviewing(true);
    const t = setTimeout(() => {
      api.previewVessel(layout)
        .then((v) => { if (!stale) { setPreview(v); setPreviewError(null); } })
        .catch((e) => { if (!stale) { setPreview(null); setPreviewError(e.message); } })
        .finally(() => { if (!stale) setPreviewing(false); });
    }, 400);
    return () => { stale = true; clearTimeout(t); };
  }, [draft]);

  useEffect(() => {
    if (!progress || progress.status !== "running" || progress.step !== 3 || previewing) return;
    if (preview) {
      setProgress({ ...progress, status: "done", message: `${preview.tanks.filter((t) => t.type === "cargo").length} cargo tanks built. Check the layout below, then save.` });
    } else {
      setProgress({ ...progress, status: "needs-input", message: previewError ?? "Fill in the highlighted fields to build the vessel." });
    }
  }, [progress, previewing, preview, previewError]);

  const takeImage = async (file: File | undefined | null) => {
    setImageError(null); setSegments([]);
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) { setImageError("Use a PNG, JPEG or WebP image."); return; }
    if (file.size > MAX_IMAGE_BYTES) { setImageError("The image is larger than 5 MB."); return; }
    setImage(await readFile(file));
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const item = Array.from(e.clipboardData.items).find((i) => i.type.startsWith("image/"));
    if (item) { e.preventDefault(); takeImage(item.getAsFile()); }
  };

  const read = async () => {
    setReading(true); setReadError(null); setNotes([]); setChecks([]); setSegments([]); setFixes([]); setEstimated([]);
    const startedAt = Date.now();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setProgress({ step: 0, status: "running", startedAt });
    // The request is one call; the first step is the upload, so move on once it's under way.
    const sent = setTimeout(() => setProgress((p) => (p && p.step === 0 ? { ...p, step: 1 } : p)), image ? 900 : 300);
    try {
      const r = await api.readDrawing(text, image, ctrl.signal);
      clearTimeout(sent);
      setProgress({ step: 2, status: "running", startedAt });
      setDraft({ ...EMPTY, ...r.layout, tanks: r.layout.tanks ?? [] });
      setNotes(r.notes); setChecks(r.checks ?? []); setSegments(r.segments); setFixes(r.fixes ?? []); setEstimated(r.estimated ?? []);
      // Show the checks step briefly (it ran on the server), then wait for the vessel to build.
      setTimeout(() => setProgress((p) => (p && p.step === 2 ? { ...p, step: 3 } : p)), 600);
    } catch (e) {
      clearTimeout(sent);
      const cancelled = ctrl.signal.aborted;
      setProgress((p) => p && { ...p, status: "failed", message: cancelled ? "Cancelled." : (e as Error).message });
      if (!cancelled) setReadError((e as Error).message);
    } finally {
      setReading(false);
      abortRef.current = null;
    }
  };

  const cancelRead = () => abortRef.current?.abort();

  const setField = (patch: Partial<DraftLayout>) => {
    setEstimated((e) => e.filter((k) => !(k in patch)));  // an edited value is no longer an estimate
    setDraft((d) => ({ ...d, ...patch }));
  };

  // Put back the value the AI read, before the automatic correction.
  const undoFix = (f: DrawingFix) => {
    setDraft((d) => {
      if (f.tank === null) return { ...d, [f.field]: f.old as number | null };
      if (f.field === "removed") return { ...d, tanks: [...(d.tanks ?? []), f.old as DraftTank] };
      const tanks = (d.tanks ?? []).map((t) => {
        const target = f.field === "id" ? f.new : f.tank;
        if (t.id !== target) return t;
        if (f.field === "xAft/xFwd" || f.field === "position") {
          const [a, b] = f.old as (number | null)[];
          return { ...t, xAft: a, xFwd: b };
        }
        return { ...t, [f.field]: f.old };
      });
      return { ...d, tanks };
    });
    setFixes((all) => all.filter((x) => x !== f));
    setEstimated((e) => e.filter((k) => k !== f.field && !(f.field === "position" && f.tank && k.startsWith(`${f.tank}.`))));
  };
  const corrected = new Set(fixes.filter((f) => f.tank).map((f) => `${f.field === "id" ? f.new : f.tank}.${f.field}`));
  const setTank = (i: number, patch: Partial<DraftTank>) =>
    setDraft((d) => ({ ...d, tanks: (d.tanks ?? []).map((t, j) => (j === i ? { ...t, ...patch } : t)) }));
  const addTank = () => setDraft((d) => {
    const tanks = d.tanks ?? [];
    const last = tanks[tanks.length - 1];
    return { ...d, tanks: [...tanks, { id: "", type: "cargo", side: last?.side === "P" ? "S" : "P",
      xAft: last?.side === "P" ? last.xAft : last?.xFwd ?? null, xFwd: last?.side === "P" ? last.xFwd : null }] };
  });
  const removeTank = (i: number) => setDraft((d) => ({ ...d, tanks: (d.tanks ?? []).filter((_, j) => j !== i) }));

  const save = async () => {
    const { layout } = complete(draft);
    if (!layout) return;
    setSaving(true); setSaveError(null);
    try { onSaved((editId ? await api.updateVessel(editId, layout) : await api.saveVessel(layout)).id); }
    catch (e) { setSaveError((e as Error).message); }
    finally { setSaving(false); }
  };

  const num = (v: number | null | undefined, onChange: (n: number | null) => void, label: string, required = true,
    flagged = false) => (
    <input type="number" step="any" aria-label={label} value={v ?? ""}
      title={flagged ? "Filled in or corrected automatically. Check it against the drawing." : undefined}
      className={required && !isNum(v) ? "needs" : flagged ? "estimated" : undefined}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
  );

  const { layout: ready } = complete(draft);

  return (
    <>
      <header className="masthead">
        <div>
          <button className="link" onClick={onCancel}>← All ships</button>
          <h1>{editId ? "Edit ship" : "New ship"}</h1>
          <p className="muted">Paste a tank table or description, or add a drawing. The AI proposes a layout; you check it before saving.</p>
        </div>
        <p className="badge">Use synthetic drawings only. Do not upload real or confidential vessel data.</p>
      </header>

      <section className="reader card" aria-labelledby="reader-h" onPaste={onPaste}>
        <h2 id="reader-h">Source</h2>
        <div className="columns">
          <div>
            <label htmlFor="src-text" className="small muted">Text (tank table, capacity plan notes, description)</label>
            <textarea id="src-text" rows={7} value={text} onChange={(e) => setText(e.target.value)}
              placeholder={"e.g. LBP 176 m, breadth 32.2 m, depth 19.1 m, summer draught 12.2 m.\nCOT1P/S 141-153 m, 5,400 m³ each …"} />
          </div>
          <div>
            <span className="small muted">Drawing</span>
            <label htmlFor="src-image" className={`dropzone ${dragging ? "over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => { e.preventDefault(); setDragging(false); takeImage(e.dataTransfer.files?.[0]); }}>
              <Icon name="upload" size={28} />
              <strong>Drop a drawing here, or choose a file</strong>
              <span className="muted small">PNG, JPEG or WebP up to 5 MB. You can also paste an image.</span>
              <input id="src-image" type="file" accept="image/png,image/jpeg,image/webp" className="visually-hidden"
                onChange={(e) => takeImage(e.target.files?.[0])} />
            </label>
            {imageError && <p className="error">{imageError}</p>}
            {image && (
              <div className="drawing">
                <img src={image} alt="Uploaded drawing" />
                {segments.map((s, i) => (
                  <span key={i} className={`seg ${s.label.includes("hull") ? "hull" : ""}`} title={s.label}
                    style={{ left: `${s.box[0] * 100}%`, top: `${s.box[1] * 100}%`,
                      width: `${(s.box[2] - s.box[0]) * 100}%`, height: `${(s.box[3] - s.box[1]) * 100}%` }} />
                ))}
                <button className="link" onClick={() => { setImage(null); setSegments([]); }}>Remove image</button>
              </div>
            )}
          </div>
        </div>
        <div className="presets">
          <button className="primary" onClick={read} disabled={reading || (!text.trim() && !image)}>
            <Icon name="sparkle" size={18} /> {reading ? "Reading…" : "Read with AI"}
          </button>
          <button onClick={() => loadLayout("demo_mr2", true)}>Start from demo layout</button>
          <button onClick={() => { setDraft(EMPTY); setNotes([]); }}>Start blank</button>
        </div>
        {progress && (
          <BuildProgress step={progress.step} status={progress.status} startedAt={progress.startedAt}
            message={progress.message} onCancel={cancelRead} />
        )}
        {readError && !progress && <p className="error">{readError}</p>}
        {checks.length > 0 && (
          <div className="note-group">
            <h3><Icon name="gauge" size={16} /> Checks</h3>
            <ul className="warnings">{checks.map((n) => <li key={n}>{n}</li>)}</ul>
          </div>
        )}
        {notes.length > 0 && (
          <div className="note-group">
            <h3><Icon name="sparkle" size={16} /> From the AI</h3>
            <ul className="warnings ai">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </div>
        )}
        {fixes.length > 0 && (
          <div className="fixes" role="status">
            <h3><Icon name="edit" size={16} /> Automatic corrections ({fixes.length})</h3>
            <p className="muted small">Real drawings often have misread digits, duplicate labels or only an LOA. These were
              repaired by rule and are outlined in amber below. Undo any you don't agree with.</p>
            <ul>
              {fixes.map((f, i) => (
                <li key={i}>
                  <span>
                    <strong>{f.tank ?? "Ship"}</strong> {f.field === "capacity100" ? "capacity" : f.field === "removed" ? "set aside" : f.field}:{" "}
                    {f.field === "removed" ? null : <><span className="old">{f.old === null ? "missing" : String(f.old)}</span> → <strong>{String(f.new)}</strong></>}
                    <span className="muted small"> · {f.reason}</span>
                  </span>
                  <button className="link" onClick={() => undoFix(f)}>Undo</button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {segments.length > 0 && <p className="muted small">SAM 3.1 found {segments.length} regions; they were passed to the reader as position hints.</p>}
      </section>

      <section className="plan card" aria-labelledby="preview-h">
        <div className="row-between">
          <h2 id="preview-h">Preview</h2>
          {previewing && <span className="muted small building"><span className="spinner" aria-hidden="true" /> Building the vessel…</span>}
        </div>
        {preview ? (
          <div className={`preview-wrap ${previewing ? "is-building" : ""}`} aria-busy={previewing}>
            <HullPlan vessel={preview} state={{}} selected={null} onSelect={() => {}} />
          </div>
        ) : previewing ? (
          <div className="preview-skeleton" aria-busy="true" aria-label="Building the vessel">
            <svg viewBox="0 0 1000 230" aria-hidden="true">
              <path d="M40 130 L150 60 H860 Q960 60 980 115 Q960 170 860 170 H150 L40 100 Z" className="skeleton-hull" />
            </svg>
            <span className="muted small"><span className="spinner" aria-hidden="true" /> Building hydrostatics and tank tables…</span>
          </div>
        ) : <p className="muted">{previewError ?? "The tank plan appears here once the layout is complete."}</p>}
        {preview && previewError && <p className="error">{previewError}</p>}
      </section>

      <section className="card" aria-labelledby="layout-h">
        <h2 id="layout-h">Layout</h2>
        <div className="particulars">
          <label>Name
            <input value={draft.name ?? ""} className={!draft.name?.trim() ? "needs" : undefined}
              onChange={(e) => setField({ name: e.target.value })} />
          </label>
          <label>Size class
            <select value={draft.sizeClass ?? "Other"} onChange={(e) => setField({ sizeClass: e.target.value as SizeClass })}>
              {SIZE_CLASSES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
          {PARTICULARS.map(([k, label]) => (
            <label key={k}>{label}{num(draft[k], (n) => setField({ [k]: n }), label, true, estimated.includes(k))}</label>
          ))}
          <label>LOA (m)
            {num(draft.loa, (n) => setField({ loa: n }), "LOA (m)", false)}
          </label>
        </div>
        <p className="muted small">x is measured in metres from the aft perpendicular, forward positive. Highlighted fields need a value.</p>
        <div className="table-wrap">
          <table className="layout-table">
            <thead><tr><th>Id</th><th>Type</th><th>Side</th><th>x aft</th><th>x fwd</th><th>Capacity m³</th><th>Line</th><th /></tr></thead>
            <tbody>
              {(draft.tanks ?? []).map((t, i) => (
                <tr key={i}>
                  <td><input aria-label={`Tank ${i + 1} id`} value={t.id ?? ""} className={!t.id ? "needs" : undefined}
                    onChange={(e) => setTank(i, { id: e.target.value.toUpperCase() })} /></td>
                  <td>
                    <select aria-label={`Tank ${i + 1} type`} value={t.type ?? ""} className={!t.type ? "needs" : undefined}
                      onChange={(e) => setTank(i, { type: e.target.value as TankKind })}>
                      {!t.type && <option value="">?</option>}
                      {KINDS.map((k) => <option key={k} value={k}>{k.replace("_", " ")}</option>)}
                    </select>
                  </td>
                  <td>
                    <select aria-label={`Tank ${i + 1} side`} value={t.side ?? ""} className={!t.side ? "needs" : undefined}
                      onChange={(e) => setTank(i, { side: e.target.value as "P" | "S" | "C" })}>
                      {!t.side && <option value="">?</option>}
                      <option value="P">Port</option><option value="S">Stbd</option><option value="C">Centre</option>
                    </select>
                  </td>
                  <td>{num(t.xAft, (n) => { setEstimated((e) => e.filter((k) => k !== `${t.id}.xAft`)); setTank(i, { xAft: n }); },
                    `Tank ${i + 1} x aft`, true, estimated.includes(`${t.id}.xAft`))}</td>
                  <td>{num(t.xFwd, (n) => { setEstimated((e) => e.filter((k) => k !== `${t.id}.xFwd`)); setTank(i, { xFwd: n }); },
                    `Tank ${i + 1} x forward`, true, estimated.includes(`${t.id}.xFwd`))}</td>
                  <td>{num(t.capacity100, (n) => setTank(i, { capacity100: n }), `Tank ${i + 1} capacity`, false,
                    corrected.has(`${t.id}.capacity100`))}</td>
                  <td><input aria-label={`Tank ${i + 1} line`} className="short" value={t.lineId ?? ""}
                    onChange={(e) => setTank(i, { lineId: e.target.value || null })} /></td>
                  <td><button className="link" aria-label={`Remove tank ${i + 1}`} onClick={() => removeTank(i)}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">Leave capacity empty to use a typical tank shape. Bunker and fresh water tanks are added aft if the layout has none.</p>
        <div className="presets">
          <button onClick={addTank}>Add tank</button>
          <button className="primary" onClick={save} disabled={!ready || saving}>
            {saving && <span className="spinner" aria-hidden="true" />}
            {saving ? "Saving the ship…" : editId ? "Save changes" : "Save ship"}
          </button>
        </div>
        {saveError && <p className="error">{saveError}</p>}
      </section>
    </>
  );
}
