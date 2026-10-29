import { useState } from "react";
import { api } from "./api";
import Icon from "./Icons";
import { Explanation } from "./StabilityPanel";
import type { CleaningReport, TankCleaning } from "./types/api";

// Tanks that change grade, the cleaning each needs (fixed rules), and a port checklist the AI writes on request.
const LEVEL_CLASS = ["none", "drain", "wash", "full"];
const usd = (n: number) => `USD ${n.toLocaleString()}`;

function group(changes: TankCleaning[]) {
  const out = new Map<string, { rows: TankCleaning[] }>();
  for (const c of changes) {
    const key = `${c.prev}>${c.next}`;
    out.set(key, { rows: [...(out.get(key)?.rows ?? []), c] });
  }
  return [...out.values()];
}

export default function Cleaning({ report }: { report: CleaningReport }) {
  const [checklist, setChecklist] = useState<{ text: string; source: "ai" | "plain" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const write = async () => {
    setBusy(true); setError(null);
    try { const r = await api.cleaningChecklist(report); setChecklist({ text: r.explanation, source: r.source }); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const last = report.last_voyage;
  return (
    <section className="card cleaning" aria-labelledby="cleaning-h">
      <div className="row-between">
        <h2 id="cleaning-h"><Icon name="free" size={18} /> Grade changes &amp; cleaning</h2>
        {report.tanks_to_clean > 0 && <span className="status-pill fail">{report.tanks_to_clean} tanks to clean</span>}
      </div>
      {report.saving && (
        <p className="saving" role="status">
          <Icon name="approve" size={16} /> Tanks chosen with the {report.saving.port} loading in mind: saves about{" "}
          <strong>{report.saving.hours} h</strong> at port and <strong>{usd(report.saving.cost_usd)}</strong> of cleaning
          ({report.saving.tanks} fewer tanks to clean) compared with {report.saving.compared_to}.
        </p>
      )}
      {report.total && report.total.hours > 0 && (
        <p className="small"><strong>Estimated cleaning:</strong> about {report.total.hours} h at port, {usd(report.total.cost_usd)}
          <span className="muted"> (typical figures: two tanks cleaned at a time)</span></p>
      )}
      {report.ports.length === 0 ? (
        <p className="muted small">
          No tank changes grade{last ? ` since the last voyage (${last.date})` : ""}, so no cleaning is needed.
          {!last && " There is no earlier voyage for this ship to compare with."}
        </p>
      ) : (
        <>
          <p className="muted small">
            Cleaning levels come from fixed rules for each change of grade; the surveyor and the cargo's own
            cleaning guide have the final say.
          </p>
          {report.ports.map((p) => (
            <div key={`${p.port}-${p.when}`} className="cleaning-port">
              <h3>{p.port} <span className="muted small">· {p.when} · last cargo: {p.source}
                {p.estimate && ` · est. ${p.estimate.hours} h, ${usd(p.estimate.cost_usd)}`}</span></h3>
              <ul className="cleaning-list">
                {group(p.changes).map(({ rows }) => {
                  const c = rows[0];
                  return (
                    <li key={`${c.prev}-${c.next}`}>
                      <details>
                        <summary>
                          <span className={`level-pill ${LEVEL_CLASS[c.level]}`}>{c.label}</span>
                          <strong>{rows.map((r) => r.tank).join(", ")}</strong>
                          <span> {c.prev_name} → {c.next_name}</span>
                          <span className="muted small"> · {c.reason}</span>
                        </summary>
                        {c.steps.length > 0 && <><h4>Steps</h4><ol>{c.steps.map((s) => <li key={s}>{s}</li>)}</ol></>}
                        {c.checks.length > 0 && <><h4>Checks</h4><ul>{c.checks.map((s) => <li key={s}>{s}</li>)}</ul></>}
                      </details>
                    </li>
                  );
                })}
              </ul>
              <details className="manual">
                <summary>Checks at {p.port}</summary>
                <ul>{p.checks.map((s) => <li key={s}>{s}</li>)}</ul>
              </details>
            </div>
          ))}
          <button onClick={write} disabled={busy}>
            {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="sparkle" size={16} />}
            {busy ? " Writing the checklist…" : " Write port checklist with AI"}
          </button>
          {error && <p className="error">{error}</p>}
          {checklist && <Explanation text={checklist.text} source={checklist.source} />}
        </>
      )}
    </section>
  );
}
