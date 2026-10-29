import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import Icon from "./Icons";
import type { MemoryItem, UsageSummary } from "./types/api";

const KIND: Record<string, { icon: string; label: string }> = {
  constraint_empty: { icon: "allocate", label: "Keep empty" },
  constraint_fixed: { icon: "allocate", label: "Tank grade" },
  max_trim: { icon: "trim", label: "Trim limit" },
  fill_mode: { icon: "allocate", label: "Fill rule" },
  grade_alias: { icon: "order", label: "Grade name" },
  note: { icon: "memory", label: "Note" },
  explain_style: { icon: "sparkle", label: "Explanations" },
};

function UsageCard({ u }: { u: UsageSummary }) {
  const max = Math.max(1, ...u.days.map((d) => d.prompt_tokens + d.completion_tokens));
  const t = u.today;
  const saved = t.cache_hits + t.skipped;
  return (
    <section className="card usage" aria-labelledby="usage-h">
      <h2 id="usage-h">AI usage</h2>
      <dl className="figures">
        <div className="stat"><span><dt>Tokens today</dt><dd>{(t.prompt_tokens + t.completion_tokens).toLocaleString()}</dd>
          <span className="stat-meaning">{t.prompt_tokens.toLocaleString()} in · {t.completion_tokens.toLocaleString()} out</span></span></div>
        <div className="stat"><span><dt>Model calls today</dt><dd>{t.calls}</dd></span></div>
        <div className="stat"><span><dt>Calls saved today</dt><dd>{saved}</dd>
          <span className="stat-meaning">{t.cache_hits} from cache · {t.skipped} handled by code</span></span></div>
      </dl>
      <svg className="usage-chart" viewBox="0 0 280 90" role="img" aria-label="Tokens used per day, last 7 days">
        {u.days.map((d, i) => {
          const h = ((d.prompt_tokens + d.completion_tokens) / max) * 64;
          return (
            <g key={d.date}>
              <rect x={i * 40 + 8} y={72 - h} width={24} height={Math.max(h, 1)} rx={3} className="usage-bar" />
              <text x={i * 40 + 20} y={86} textAnchor="middle" className="gz-label">
                {new Date(d.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "short" })}
              </text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}

export default function Memory() {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [draftTrim, setDraftTrim] = useState("");

  // Only the latest list request is shown, so a slow reload can't bring back an item just deleted.
  const loadSeq = useRef(0);
  const load = () => {
    const n = ++loadSeq.current;
    api.memory().then((r) => { if (n === loadSeq.current) setItems(r); }).catch((e) => setError(e.message));
    api.usage().then(setUsage).catch(() => {});
  };
  useEffect(load, []);

  // Run a change, then reload. Returns whether it worked, so a form only closes on success.
  const act = async (fn: () => Promise<unknown>): Promise<boolean> => {
    setError(null);
    try { await fn(); load(); return true; } catch (e) { setError((e as Error).message); return false; }
  };

  const forget = (id: string) => {
    setItems((all) => all && all.filter((x) => x.id !== id));
    act(() => api.deleteMemory(id));
  };

  const saveEdit = async (i: MemoryItem) => {
    const trim = i.kind === "max_trim" ? Number(draftTrim) : null;
    const patch = trim !== null && Number.isFinite(trim) && trim > 0 && trim !== Number(i.value)
      ? { value: trim, text: `Trim at most ${trim} m` } : { text: draft };
    if (await act(() => api.patchMemory(i.id, patch))) setEditing(null);
  };

  const groups = new Map<string, MemoryItem[]>();
  for (const i of items ?? []) {
    const name = i.scope_name ?? (i.scope === "global" ? "All ships" : i.scope);
    groups.set(name, [...(groups.get(name) ?? []), i]);
  }
  const order = [...groups.keys()].sort((a, b) => (a === "All ships" ? -1 : b === "All ships" ? 1 : a.localeCompare(b)));

  return (
    <section aria-labelledby="memory-h">
      <div className="page-head">
        <p className="eyebrow">Assistant</p>
        <h1 id="memory-h">Memory</h1>
        <p className="muted">
          What the assistant remembers for you. It learns only when you say so, for example
          “<em>always keep tank 3 empty on this ship</em>” or “<em>remember GO 10 means ULSD</em>”, and forgets
          when you say “<em>forget the tank 3 rule</em>”. Remembered rules are applied before planning, at no AI cost.
        </p>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="columns">
        <div>
          {!items && !error && <p className="muted">Loading memory…</p>}
          {items && items.length === 0 && (
            <div className="empty card">
              <span className="new-icon"><Icon name="memory" size={28} /></span>
              <h2>Nothing remembered yet</h2>
              <p className="muted">In the planner, add “always” or “from now on” to a change request to make it stick.</p>
            </div>
          )}
          {order.map((scope) => (
            <section key={scope} className="card memory-group" aria-label={scope}>
              <h2>{scope}</h2>
              <ul className="memory-list">
                {groups.get(scope)!.map((i) => (
                  <li key={i.id}>
                    <span className="stat-icon"><Icon name={KIND[i.kind]?.icon ?? "memory"} size={16} /></span>
                    <div className="memory-body">
                      <span className="muted small">{KIND[i.kind]?.label ?? i.kind}</span>
                      {editing === i.id ? (
                        <form className="changes" onSubmit={(e) => { e.preventDefault(); saveEdit(i); }}>
                          <input aria-label="Memory text" value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus />
                          {i.kind === "max_trim" && (
                            <input aria-label="Trim limit in metres" type="number" step="0.1" min="0.1" value={draftTrim}
                              onChange={(e) => setDraftTrim(e.target.value)} />
                          )}
                          <button type="submit">Save</button>
                          <button type="button" onClick={() => setEditing(null)}>Cancel</button>
                        </form>
                      ) : (
                        <strong>{i.text}</strong>
                      )}
                      <span className="muted small">
                        Updated {new Date(i.updated_at).toLocaleDateString()} · used {i.uses} {i.uses === 1 ? "time" : "times"}
                      </span>
                    </div>
                    {editing !== i.id && (
                      <div className="presets">
                        <button className="icon-button" aria-label={`Edit ${i.text}`} onClick={() => { setEditing(i.id); setDraft(i.text); setDraftTrim(String(i.value ?? "")); }}>
                          <Icon name="edit" size={16} />
                        </button>
                        <button className="icon-button" aria-label={`Forget ${i.text}`} onClick={() => forget(i.id)}>
                          <Icon name="trash" size={16} />
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {items && items.length > 0 && (
            <button onClick={() => { if (window.confirm("Forget everything the assistant remembers for you?")) act(api.clearMemory); }}>
              <Icon name="trash" size={16} /> Clear all memory
            </button>
          )}
        </div>

        <div>
          <form className="card memory-add" onSubmit={(e) => { e.preventDefault(); if (note.trim()) act(() => api.addMemoryNote(note)).then((ok) => { if (ok) setNote(""); }); }}>
            <h2>Add a note</h2>
            <p className="muted small">A fact the assistant should keep in mind when reading orders, e.g. a berth's draught limit. Notes never change a plan by themselves, except a rule about how full to fill tanks (e.g. “spread the cargo evenly”), which the planner applies.</p>
            <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} aria-label="New memory note" maxLength={300} />
            <button className="primary small-btn" type="submit" disabled={note.trim().length < 2}><Icon name="plus" size={16} /> Remember this</button>
          </form>
          {usage && <UsageCard u={usage} />}
        </div>
      </div>
    </section>
  );
}
