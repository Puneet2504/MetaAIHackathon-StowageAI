import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { addGrade, allGrades, gradeById } from "./grades";
import HullPlan, { type TankState } from "./HullPlan";
import Icon from "./Icons";
import StabilityPanel, { Explanation } from "./StabilityPanel";
import type { VoyageDetail as Voyage } from "./types/api";
import { Route } from "./Voyages";

interface Props {
  id: string;
  onBack: () => void;
  onPlanAgain: (vesselId: string, order: string) => void;
}

export default function VoyageDetail({ id, onBack, onPlanAgain }: Props) {
  const [v, setV] = useState<Voyage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.voyage(id).then((r) => {
      r.plan.allocation.forEach((a) => addGrade(a.grade_id, a.grade, a.density_t_m3));
      setV(r);
    }).catch((e) => setError(e.message));
  }, [id]);

  // The approved loads, as tank fill states for the read-only drawing.
  const state = useMemo(() => {
    const out: Record<string, TankState> = {};
    if (!v?.vessel) return out;
    for (const l of v.plan.loads) {
      const t = v.vessel.tanks.find((x) => x.id === l.tank_id);
      if (t && (t.type === "cargo" || t.type === "slop")) {
        out[t.id] = { grade: l.grade ?? null, fillPct: Math.round((1000 * l.volume_m3) / t.capacity100) / 10 };
      }
    }
    return out;
  }, [v]);

  const remove = async () => {
    if (!v || !window.confirm("Delete this voyage? This cannot be undone.")) return;
    try { await api.deleteVoyage(v.id); onBack(); }
    catch (e) { setError((e as Error).message); }
  };

  if (error) return <><p className="error">{error}</p><button onClick={onBack}>Back to voyages</button></>;
  if (!v) return <p className="muted">Loading voyage…</p>;

  return (
    <>
      <header className="masthead">
        <div>
          <button className="link" onClick={onBack}>← All voyages</button>
          <h1>{v.vessel_name ?? v.vessel_id}</h1>
          <Route from={v.voyage.load_ports} to={v.voyage.discharge_ports} />
          <p className="muted small">
            Approved {new Date(v.approved_at).toLocaleString()} · {v.voyage.total_t.toLocaleString()} t in total
          </p>
        </div>
        <div className="presets">
          {v.vessel && v.order_text && (
            <button className="primary small-btn" onClick={() => onPlanAgain(v.vessel_id, v.order_text!)}>
              <Icon name="sparkle" size={16} /> Plan again
            </button>
          )}
          <button onClick={remove}><Icon name="trash" size={16} /> Delete voyage</button>
        </div>
      </header>

      {v.vessel ? (
        <section className="plan card" aria-label="Approved tank plan">
          <HullPlan vessel={v.vessel} state={state} selected={null} onSelect={() => {}} />
          <ul className="legend">
            {allGrades().filter((g) => v.voyage.grades.some((x) => x.grade_id === g.id)).map((g) => (
              <li key={g.id}><span className="swatch" style={{ background: g.color }} />{g.name}
                <strong> {v.voyage.grades.find((x) => x.grade_id === g.id)!.tonnes.toLocaleString()} t</strong></li>
            ))}
          </ul>
        </section>
      ) : <p className="muted">The ship for this voyage has been deleted, so its drawing can't be shown.</p>}

      <div className="columns">
        <section className="card" aria-labelledby="cargo-h">
          <h2 id="cargo-h">Cargo</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Grade</th><th>Tanks</th><th>Lines</th><th className="num">Tonnes</th></tr></thead>
              <tbody>
                {v.plan.allocation.map((a) => (
                  <tr key={a.parcel}>
                    <td><span className="swatch" style={{ background: gradeById(a.grade_id)?.color }} />{a.grade}</td>
                    <td>{a.tanks.join(", ")}</td>
                    <td>{a.lines.join(", ")}</td>
                    <td className="num">{Math.round(a.allocated_m3 * a.density_t_m3).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {v.explanation && <Explanation text={v.explanation} />}
          {v.order_text && (
            <details className="manual">
              <summary>Original order</summary>
              <p className="order-text">{v.order_text}</p>
            </details>
          )}
        </section>
        <section className="card" aria-labelledby="stab-h">
          <h2 id="stab-h">Stability at approval</h2>
          {v.plan.result ? <StabilityPanel result={v.plan.result} /> : <p className="muted">No stability result stored.</p>}
        </section>
      </div>
    </>
  );
}
