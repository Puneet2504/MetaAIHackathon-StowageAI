import { useEffect, useState } from "react";
import { api } from "./api";
import type { View } from "./App";
import Icon from "./Icons";
import { ShipThumb } from "./Illustrations";
import type { VesselSummary } from "./types/api";

interface Props {
  onOpen: (v: View) => void;
}

const ORIGIN: Record<string, string> = { synthetic: "Built-in demo", user_drawing: "Your drawing", stability_booklet: "Booklet" };

export default function ShipLibrary({ onOpen }: Props) {
  const [ships, setShips] = useState<VesselSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.vessels().then(setShips)
    .catch((e) => setError(`Couldn't load ships. Is the backend running on port 8000? (${e.message})`));
  useEffect(() => { load(); }, []);

  const remove = async (s: VesselSummary) => {
    if (!window.confirm(`Delete ${s.name}? This cannot be undone.`)) return;
    try { await api.deleteVessel(s.id); load(); }
    catch (e) { setError((e as Error).message); }
  };

  return (
    <section aria-labelledby="ships-h">
      <div className="page-head">
        <div>
          <p className="eyebrow">Fleet</p>
          <h1 id="ships-h">Ships</h1>
          <p className="muted">Open a ship to plan a voyage, or create your own from pasted text or a drawing.</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!ships && !error && <p className="muted">Loading ships…</p>}
      <ul className="ship-cards">
        {ships?.map((s) => (
          <li key={s.id} className="ship-card">
            <button className="ship-card-open" onClick={() => onOpen({ kind: "planner", id: s.id })}
              aria-label={`Open ${s.name}`}>
              <ShipThumb cargoTanks={s.cargoTanks} owned={s.owned} />
            </button>
            <div className="ship-card-body">
              <div className="row-between">
                <h2>{s.name}</h2>
                <span className="class-badge">{s.sizeClass}</span>
              </div>
              <div className="chips">
                <span className="chip"><Icon name="allocate" size={14} />{s.cargoTanks} cargo tanks</span>
                <span className={`chip ${s.owned ? "chip-accent" : ""}`}><Icon name="ship" size={14} />{ORIGIN[s.origin] ?? s.origin}</span>
              </div>
              <div className="presets">
                <button className="primary small-btn" onClick={() => onOpen({ kind: "planner", id: s.id })}>Plan a voyage</button>
                {s.owned
                  ? <>
                      <button onClick={() => onOpen({ kind: "edit", id: s.id })}>Edit</button>
                      <button onClick={() => remove(s)}>Delete</button>
                    </>
                  : <button onClick={() => onOpen({ kind: "edit", copyFrom: s.id })}>Copy as new</button>}
              </div>
            </div>
          </li>
        ))}
        {ships && (
          <li className="ship-card new-card">
            <button onClick={() => onOpen({ kind: "edit" })}>
              <span className="new-icon"><Icon name="plus" size={28} /></span>
              <strong>New ship</strong>
              <span className="muted small">From a tank table, description or drawing</span>
            </button>
          </li>
        )}
      </ul>
    </section>
  );
}
