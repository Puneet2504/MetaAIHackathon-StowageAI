import { useEffect, useState } from "react";
import { api } from "./api";
import Icon from "./Icons";
import type { OpenSpaceShip, SpaceAnswer, SpaceOffer } from "./types/api";
import { Route } from "./Voyages";

// For charterers: each ship's current voyage, what is empty after each port, and what the AI says still fits.
// The AI chooses the grade, tanks and quantity and checks them; the voyage check only adds a label.

interface SuggestionProps {
  answer: SpaceAnswer;
  onUse: (line: string) => void;      // open in the planner, or add to the order box
  useLabel: string;
  onAdd?: (s: SpaceOffer) => void;    // add straight into the current voyage (verified suggestions only)
  adding?: number | null;             // index of the suggestion being added
}

export function SpaceSuggestions({ answer, onUse, useLabel, onAdd, adding }: SuggestionProps) {
  if (!answer.ai) return <p className="muted small">{answer.message}</p>;
  return (
    <div className="space-ai">
      <p className="space-summary"><Icon name="sparkle" size={16} /> {answer.ai.summary}</p>
      {answer.ai.suggestions.length === 0 && <p className="muted small">The AI found nothing more that fits on this voyage.</p>}
      <ul className="space-offers">
        {answer.ai.suggestions.map((s, i) => (
          <li key={i} className={s.check.verified ? "verified" : "unverified"}>
            <div className="row-between">
              <strong>{s.grade} · {Math.round(s.quantity_t).toLocaleString()} mt</strong>
              <span className={`status-pill ${s.check.verified ? "pass" : "fail"}`} title={s.check.why}>
                <Icon name={s.check.verified ? "approve" : "gauge"} size={14} /> {s.check.label}
              </span>
            </div>
            <p className="small">
              Load at <strong>{s.port}</strong> for <strong>{s.discharge_port}</strong> ·{" "}
              {Math.round(s.quantity_m3).toLocaleString()} m³ in {s.tanks.join(", ")} · cleaning: {s.cleaning}
            </p>
            <p className="muted small">{s.reason}</p>
            {!s.check.verified && <p className="small warn-text">{s.check.why}</p>}
            <details>
              <summary className="small">What the AI checked</summary>
              <ul className="small">{s.checks.map((c, k) => <li key={k}>{c}</li>)}</ul>
            </details>
            <div className="presets">
              {onAdd && s.check.verified && (
                <button className="primary small-btn" onClick={() => onAdd(s)} disabled={adding != null}>
                  {adding === i ? <span className="spinner" aria-hidden="true" /> : <Icon name="plus" size={14} />} Add to this voyage
                </button>
              )}
              <button className="small-btn" onClick={() => onUse(s.order_line)}>{useLabel}</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface ShipProps {
  ship: OpenSpaceShip;
  auto: boolean;
  onPlan: (vesselId: string, order: string) => void;
  onOpenVoyage: (id: string) => void;
}

function ShipSpace({ ship: initial, auto, onPlan, onOpenVoyage }: ShipProps) {
  const [ship, setShip] = useState(initial);
  const [answer, setAnswer] = useState<SpaceAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const [adding, setAdding] = useState<number | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const v = ship.voyage;

  // Put a verified suggestion straight into the current voyage, then show what is left.
  const add = async (s: SpaceOffer) => {
    if (!v || !window.confirm(`Add ${Math.round(s.quantity_t).toLocaleString()} mt ${s.grade} at ${s.port} for ${s.discharge_port} `
      + `to ${ship.vessel_name}'s current voyage?`)) return;
    setAdding(answer?.ai?.suggestions.indexOf(s) ?? 0); setError(null);
    try {
      await api.addToVoyage(v.id, s);
      setAdded(`${s.order_line} Added to the voyage.`);
      setAnswer(null);
      setShip(await api.openSpaceFor(ship.vessel_id));
    } catch (e) { setError((e as Error).message); }
    finally { setAdding(null); }
  };

  const ask = async () => {
    setAsking(true); setError(null);
    try { setAnswer(await api.openSpaceSuggest(ship.vessel_id)); }
    catch (e) { setError((e as Error).message); }
    finally { setAsking(false); }
  };
  useEffect(() => { if (auto && v) ask(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="card open-ship" aria-label={ship.vessel_name ?? ship.vessel_id}>
      <div className="row-between">
        <span className="voyage-ship">
          <strong>{ship.vessel_name}</strong>
          {ship.size_class && <span className="class-badge">{ship.size_class}</span>}
        </span>
        {v && <span className="muted small">Current voyage approved {new Date(v.approved_at).toLocaleDateString()}</span>}
      </div>
      {!v ? (
        <p className="muted">No current voyage: the whole ship is open. Plan a voyage for it from the Ships tab.</p>
      ) : (
        <>
          <Route from={v.route.load_ports} to={v.route.discharge_ports} />
          <div className="space-ports">
            {v.facts.ports.map((p) => (
              <div key={p.port} className="space-port">
                <h3>At {p.port} <span className="muted small">· cargo for {p.later_ports.join(" or ")}</span></h3>
                <p className="small"><span className="muted">On board:</span> {p.on_board.length ? p.on_board.join("; ") : "nothing"}</p>
                {p.empty.length ? (
                  <div className="chips">
                    {p.empty.map((e) => (
                      <span key={e.tanks} className="chip" title={`Line ${e.line}${e.last ? `, last cargo ${e.last}` : ""}`}>
                        {e.tanks} · {e.m3.toLocaleString()} m³{e.last ? ` · last ${e.last}` : ""}
                      </span>
                    ))}
                  </div>
                ) : <p className="muted small">No empty tanks.</p>}
                <p className="muted small">Free lines: {p.free_lines.length ? p.free_lines.join(", ") : "none"}</p>
              </div>
            ))}
          </div>
          {!answer && (
            <button className="primary small-btn" onClick={ask} disabled={asking}>
              {asking ? <span className="spinner" aria-hidden="true" /> : <Icon name="sparkle" size={16} />}
              {asking ? " The AI is checking tanks, lines and cleaning…" : " Ask AI what fits"}
            </button>
          )}
          {error && <p className="error">{error}</p>}
          {added && (
            <p className="saving" role="status">
              <Icon name="approve" size={16} /> {added}{" "}
              <button className="link" onClick={() => onOpenVoyage(v.id)}>View the voyage</button>
            </p>
          )}
          {answer && (
            <SpaceSuggestions answer={answer} useLabel="Open in planner" onAdd={add} adding={adding}
              onUse={(line) => onPlan(ship.vessel_id, `${v.order_text ?? ""} ${line}`.trim())} />
          )}
        </>
      )}
    </section>
  );
}

export default function OpenSpace({ onPlan, onOpenVoyage }: { onPlan: (vesselId: string, order: string) => void;
  onOpenVoyage: (id: string) => void }) {
  const [ships, setShips] = useState<OpenSpaceShip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.openSpace().then(setShips).catch((e) => setError(e.message)); }, []);
  const withVoyage = (ships ?? []).filter((s) => s.voyage);
  const firstId = withVoyage[0]?.vessel_id;
  return (
    <section aria-labelledby="space-h">
      <div className="page-head">
        <p className="eyebrow">For charterers</p>
        <h1 id="space-h">Open space</h1>
        <p className="muted">
          Each ship's current voyage: which tanks are empty after every port, and what the AI says you could still book,
          with the quantity. Every suggestion is then checked port by port; book it to have the planner place it.
        </p>
      </div>
      {error && <p className="error">{error}</p>}
      {!ships && !error && <p className="muted">Loading ships…</p>}
      {ships && [...withVoyage, ...ships.filter((s) => !s.voyage)].map((s) => (
        <ShipSpace key={s.vessel_id} ship={s} auto={s.vessel_id === firstId} onPlan={onPlan} onOpenVoyage={onOpenVoyage} />
      ))}
    </section>
  );
}
