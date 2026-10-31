import type { ReactNode } from "react";
import BallastPreview from "./BallastPreview";
import Icon from "./Icons";
import type { VoyageCheckReport } from "./types/api";

// The voyage followed port by port: pipelines, trapped cargo, and the ship's condition after each port.
// The rules and swap search are code on the server; this card only shows the result.
interface Props {
  report: VoyageCheckReport;
  onApply?: () => void;   // apply the suggested swaps (hand-edited conditions only)
  applying?: boolean;
  children?: ReactNode;   // e.g. the approve button for a hand-edited condition
}

const pct = (v?: number | null) => (v == null ? "–" : `${Math.round(v)}%`);

export default function VoyageCheck({ report, onApply, applying, children }: Props) {
  const blocking = report.issues.filter((i) => i.severity === "block");
  const warnings = report.issues.filter((i) => i.severity === "warn");
  const notes = report.issues.filter((i) => i.severity === "note");
  const s = report.suggestion;
  return (
    <section className="card voyage-check" aria-labelledby="vcheck-h">
      <div className="row-between">
        <h2 id="vcheck-h"><Icon name="route" size={18} /> Voyage check</h2>
        <span className={`status-pill ${report.ok ? "pass" : "fail"}`}>
          <Icon name={report.ok ? "approve" : "cross"} size={14} />
          {report.ok ? " No cargo trapped" : ` ${blocking.length} ${blocking.length === 1 ? "problem" : "problems"} to fix`}
        </span>
      </div>
      <p className="muted small">
        Each port in turn: every cargo line carries one grade, every discharge port can pump its cargo out without
        touching another grade, and the ship stays safe after every port.
      </p>

      {report.ports.length > 0 && (
        <ol className="port-timeline">
          {report.ports.map((p, i) => (
            <li key={`${p.port}-${i}`} className={p.passed === null ? "empty" : p.passed ? "pass" : "fail"}
              title={p.passed === null ? "All cargo discharged: the ship leaves in ballast."
                : `GM ${p.gm_m?.toFixed(2)} m · trim ${Math.abs(p.trim_m ?? 0).toFixed(2)} m · shear ${pct(p.max_sf_pct)} · bending ${pct(p.max_bm_pct)}`}>
              <span className="port-dot"><Icon name={p.passed === null ? "anchor" : p.passed ? "approve" : "cross"} size={14} /></span>
              <span className="port-name">{i === 0 ? `Leave ${p.port}` : `After ${p.port}`}</span>
              <span className="muted small">{p.passed === null ? "empty" : p.aboard.join(", ")}</span>
            </li>
          ))}
        </ol>
      )}

      {report.ports.filter((p) => p.ballast).map((p) => (
        <details key={`b-${p.port}`} className="manual">
          <summary>Ballast preview leaving {p.port}</summary>
          <BallastPreview preview={p.ballast!} title={`Leaving ${p.port}`} />
        </details>
      ))}

      {blocking.length + warnings.length > 0 && (
        <ul className="check-issues">
          {[...blocking, ...warnings].map((i, k) => (
            <li key={k} className={i.severity}>
              <Icon name={i.severity === "block" ? "cross" : "gauge"} size={14} /> {i.message}
            </li>
          ))}
        </ul>
      )}
      {notes.length > 0 && (
        <details className="manual">
          <summary>Valve notes ({notes.length})</summary>
          <ul>{notes.map((n, k) => <li key={k}>{n.message}</li>)}</ul>
        </details>
      )}

      {s && (
        <div className="swap-suggestion">
          <h3><Icon name="sparkle" size={16} /> Suggested tank swaps</h3>
          <ol>
            {s.moves.map((m, k) => (
              <li key={k}>{m.kind === "swap"
                ? `Swap the cargo of ${m.from} and ${m.to} (${m.grade})`
                : `Move ${m.grade} from ${m.from} to ${m.to}`}</li>
            ))}
          </ol>
          <p className="muted small">
            Clears {s.resolves.join(", ").toLowerCase().replace(/_/g, " ")}; every port then passes
            {s.after.ports.filter((p) => p.passed !== null).map((p) => ` · ${p.port}: bending ${pct(p.max_bm_pct)}`).join("")}.
          </p>
          {onApply && (
            <button className="primary small-btn" onClick={onApply} disabled={applying}>
              {applying ? <span className="spinner" aria-hidden="true" /> : <Icon name="approve" size={16} />} Apply swaps
            </button>
          )}
        </div>
      )}
      {!report.ok && !s && (
        <p className="small">No tank swaps clear this on their own. Change the tanks by hand or replan with the AI planner.</p>
      )}
      {children}
    </section>
  );
}
