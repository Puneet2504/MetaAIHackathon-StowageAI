import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { gradeById } from "./grades";
import Icon from "./Icons";
import type { VoyageListItem } from "./types/api";

interface Props {
  onOpen: (id: string) => void;
  onShips: () => void;
}

export function Route({ from, to }: { from: string[]; to: string[] }) {
  return (
    <p className="route">
      <span className="port">{from.join(" / ") || "Load port not given"}</span>
      <span className="route-line" aria-hidden="true"><Icon name="ship" size={16} /></span>
      <span className="port">{to.join(" → ") || "Discharge ports not given"}</span>
    </p>
  );
}

export default function Voyages({ onOpen, onShips }: Props) {
  const [voyages, setVoyages] = useState<VoyageListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => { api.voyages().then(setVoyages).catch((e) => setError(e.message)); }, []);

  const shown = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!voyages || !term) return voyages ?? [];
    return voyages.filter((v) => [v.vessel_name, v.vessel_id, ...v.voyage.load_ports, ...v.voyage.discharge_ports,
      ...v.voyage.grades.map((g) => g.grade)].some((s) => s?.toLowerCase().includes(term)));
  }, [voyages, q]);

  return (
    <section aria-labelledby="voyages-h">
      <div className="page-head row-between">
        <div>
          <p className="eyebrow">Approved plans</p>
          <h1 id="voyages-h">Voyages</h1>
          <p className="muted">Every voyage plan you approve is kept here with its cargo, route and stability check.</p>
        </div>
        {voyages && voyages.length > 0 && (
          <label className="search">
            <Icon name="search" size={16} />
            <input aria-label="Search voyages" placeholder="Ship, port or grade" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {!voyages && !error && <p className="muted">Loading voyages…</p>}
      {voyages && voyages.length === 0 && (
        <div className="empty card">
          <span className="new-icon"><Icon name="route" size={28} /></span>
          <h2>No voyages yet</h2>
          <p className="muted">Open a ship, plan a voyage and press <strong>Approve plan</strong>. It will appear here.</p>
          <button className="primary" onClick={onShips}>Go to ships</button>
        </div>
      )}
      {voyages && voyages.length > 0 && shown.length === 0 && <p className="muted">No voyages match “{q}”.</p>}
      <ul className="voyage-cards">
        {shown.map((v) => (
          <li key={v.id}>
            <button className="voyage-card" onClick={() => onOpen(v.id)}>
              <div className="row-between">
                <span className="voyage-ship">
                  <strong>{v.vessel_name ?? v.vessel_id}</strong>
                  {v.size_class && <span className="class-badge">{v.size_class}</span>}
                </span>
                <span className={`status-pill ${v.voyage.passed ? "pass" : "fail"}`}>
                  <Icon name={v.voyage.passed ? "approve" : "cross"} size={14} /> Approved
                </span>
              </div>
              <Route from={v.voyage.load_ports} to={v.voyage.discharge_ports} />
              <div className="chips">
                {v.voyage.grades.map((g, i) => (
                  <span key={i} className="chip">
                    <span className="swatch" style={{ background: gradeById(g.grade_id)?.color ?? "var(--g-other)" }} />
                    {g.grade} · {g.tonnes.toLocaleString()} t
                  </span>
                ))}
              </div>
              <div className="voyage-meta muted small">
                <span><Icon name="calendar" size={14} /> {new Date(v.approved_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</span>
                <span>{v.voyage.total_t.toLocaleString()} t in total</span>
                {v.voyage.gm_m !== null && <span>GM {v.voyage.gm_m.toFixed(2)} m</span>}
                {!!v.voyage.tanks_to_clean && <span className="clean-chip">{v.voyage.tanks_to_clean} tanks to clean
                  {v.voyage.cleaning_hours ? ` (~${v.voyage.cleaning_hours} h)` : ""}</span>}
                {v.voyage.trim_m !== null && <span>Trim {Math.abs(v.voyage.trim_m).toFixed(2)} m</span>}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
