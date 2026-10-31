import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { addGrade, allGrades, gradeById } from "./grades";
import HullPlan, { type TankState } from "./HullPlan";
import Icon from "./Icons";
import Cleaning from "./Cleaning";
import Quantities from "./Quantities";
import VoyageCheck from "./VoyageCheck";
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
  const [deleting, setDeleting] = useState(false);
  const deletingRef = useRef(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [stageView, setStageView] = useState(0);  // 0: departure condition; n: after the n-th reload port

  useEffect(() => {
    api.voyage(id).then((r) => {
      [...r.plan.allocation, ...(r.plan.stages ?? []).flatMap((s) => s.allocation)]
        .forEach((a) => addGrade(a.grade_id, a.grade, a.density_t_m3));
      setV(r);
    }).catch((e) => setError(e.message));
  }, [id]);

  const stages = v?.plan.stages ?? [];
  const stage = stageView > 0 ? stages[stageView - 1] ?? null : null;

  // The approved loads of the chosen condition, as tank fill states for the read-only drawing.
  const state = useMemo(() => {
    const out: Record<string, TankState> = {};
    if (!v?.vessel) return out;
    for (const l of stage ? stage.loads : v.plan.loads) {
      const t = v.vessel.tanks.find((x) => x.id === l.tank_id);
      if (t && (t.type === "cargo" || t.type === "slop")) {
        out[t.id] = { grade: l.grade ?? null, fillPct: Math.round((1000 * l.volume_m3) / t.capacity100) / 10,
          density: l.density_t_m3 };
      }
    }
    return out;
  }, [v, stage]);

  const remove = async () => {
    if (!v || deletingRef.current || !window.confirm("Delete this voyage? This cannot be undone.")) return;
    deletingRef.current = true;
    setDeleting(true); setDeleteError(null);
    try { await api.deleteVoyage(v.id); onBack(); }
    catch (e) { setDeleteError((e as Error).message); setDeleting(false); deletingRef.current = false; }
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
            {v.plan.manual && <span className="chip manual-chip">Adjusted by hand</span>}
          </p>
        </div>
        <div className="presets">
          {v.vessel && v.order_text && (
            <button className="primary small-btn" onClick={() => onPlanAgain(v.vessel_id, v.order_text!)}>
              <Icon name="sparkle" size={16} /> Plan again
            </button>
          )}
          <button onClick={remove} disabled={deleting}>
            <Icon name="trash" size={16} /> {deleting ? "Deleting…" : "Delete voyage"}
          </button>
        </div>
      </header>
      {deleteError && <p className="error">{deleteError}</p>}

      {v.vessel ? (
        <section className="plan card" aria-label="Approved tank plan">
          {stages.length > 0 && (
            <div className="presets stage-picker" role="group" aria-label="Condition shown">
              {[`Departure ${v.parcels.find((x) => x.load_port)?.load_port ?? "first port"}`, ...stages.map((s) => `After ${s.port}`)]
                .map((label, i) => (
                  <button key={label} className={stageView === i ? "primary small-btn" : "small-btn"} aria-pressed={stageView === i}
                    onClick={() => setStageView(i)}>{label}</button>
                ))}
            </div>
          )}
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
                {[...v.plan.allocation.map((a) => ({ a, at: "" })),
                  ...stages.flatMap((s) => s.allocation.map((a) => ({ a, at: s.port })))].map(({ a, at }) => (
                  <tr key={a.parcel}>
                    <td><span className="swatch" style={{ background: gradeById(a.grade_id)?.color }} />{a.grade}
                      {at && <span className="muted small"> · loaded at {at}</span>}</td>
                    <td>{a.tanks.join(", ")}</td>
                    <td>{a.lines.join(", ")}</td>
                    <td className="num">{Math.round(a.tonnes ?? a.allocated_m3 * a.density_t_m3).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Quantities allocation={[...v.plan.allocation, ...(v.plan.stages ?? []).flatMap((s) => s.allocation)]} />
          {(v.plan.amendments ?? []).length > 0 && (
            <div className="amendments">
              <h3 className="small">Added later</h3>
              <ul className="small">
                {v.plan.amendments!.map((a, i) => (
                  <li key={i}>{a.line} <span className="muted">· {a.tanks.join(", ")} · {new Date(a.at).toLocaleString()}</span></li>
                ))}
              </ul>
            </div>
          )}
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
          {stage ? (
            <>
              <p className="muted small">Condition on leaving {stage.port}, after discharging {stage.discharged.join(", ") || "cargo"} and
                loading {stage.allocation.map((a) => a.grade).join(", ")}.</p>
              {stage.result ? <StabilityPanel result={stage.result} /> : <p className="error">{stage.message}</p>}
            </>
          ) : v.plan.result ? <StabilityPanel result={v.plan.result} /> : <p className="muted">No stability result stored.</p>}
        </section>
      </div>
      {v.plan.voyage_check && <VoyageCheck report={v.plan.voyage_check} />}
      {v.plan.cleaning && <Cleaning report={v.plan.cleaning} />}
    </>
  );
}
