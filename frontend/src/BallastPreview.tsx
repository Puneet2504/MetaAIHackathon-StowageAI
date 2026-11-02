import BallastLayout from "./BallastLayout";
import type { TankState } from "./HullPlan";
import Icon from "./Icons";
import type { BallastPreview as Preview } from "./types/api";
import type { VesselModel } from "./types/vessel-model";

// How much ballast water, in which tanks, brings the trim back to slightly by the stern (0.3-1.0 m).
// A preview from the stability engine: it doesn't change the plan.
const trim = (v: number) => `${Math.abs(v).toFixed(2)} m by the ${v >= 0 ? "stern" : "head"}`;

interface Props {
  preview: Preview;
  title?: string;
  vessel?: VesselModel | null;           // when given: the ship drawn with the water to take in
  cargo?: Record<string, TankState>;
  openLayout?: boolean;
}

export default function BallastPreview({ preview, title = "Ballast preview", vessel, cargo, openLayout = false }: Props) {
  return (
    <div className={`ballast-preview ${preview.reached ? "reached" : "partial"}`}>
      <p className="ballast-head">
        <Icon name="draught" size={16} /> <strong>{title}:</strong>{" "}
        trim {trim(preview.trim_before)} → <strong>{trim(preview.trim_after)}</strong>
        {preview.total_t > 0 && <> with {preview.total_t.toLocaleString()} t of ballast water</>}
      </p>
      {preview.tanks.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Ballast tank</th><th className="num">m³</th><th className="num">t</th><th className="num">Full</th></tr></thead>
            <tbody>
              {preview.tanks.map((t) => (
                <tr key={t.tank}><td>{t.tank}</td><td className="num">{t.m3.toLocaleString()}</td>
                  <td className="num">{t.t.toLocaleString()}</td><td className="num">{t.pct}%</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {preview.draught_fwd_m !== undefined && (
        <p className="muted small">
          After ballasting: draught {preview.draught_fwd_m?.toFixed(2)} m fwd / {preview.draught_aft_m?.toFixed(2)} m aft · GM{" "}
          {preview.gm_m?.toFixed(2)} m{preview.max_stress_pct != null && ` · hull stress ${Math.round(preview.max_stress_pct)}%`}
          {preview.passed === false && " · fails a stability check"}
        </p>
      )}
      {preview.note && <p className="small warn-text">{preview.note}</p>}
      {vessel && preview.tanks.length > 0 && (
        <details className="layout-details" open={openLayout}>
          <summary>Show on the ship</summary>
          <BallastLayout vessel={vessel} preview={preview} cargo={cargo} />
        </details>
      )}
      <p className="muted small">Target 0.3–1.0 m by the stern. A preview only: it doesn't change the plan.</p>
    </div>
  );
}
