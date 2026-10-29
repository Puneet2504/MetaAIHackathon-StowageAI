import GzChart from "./GzChart";
import Icon from "./Icons";
import { CRITERION_MEANING, STAT_MEANING } from "./plainTerms";
import type { StabilityResult } from "./types/api";

function Stat({ icon, label, value, meaning }: { icon: string; label: string; value: string; meaning: string }) {
  return (
    <div className="stat" title={meaning}>
      <span className="stat-icon"><Icon name={icon} size={18} /></span>
      <span><dt>{label}</dt><dd>{value}</dd><span className="stat-meaning">{meaning}</span></span>
    </div>
  );
}

// Verdict, figures, GZ curve and criteria. Each figure and check has a one-line plain meaning.
export default function StabilityPanel({ result }: { result: StabilityResult }) {
  const failed = result.criteria.filter((c) => !c.passed);
  const lean = result.list_deg;
  return (
    <>
      <p className={`verdict-banner ${result.all_passed ? "pass" : "fail"}`}>
        <Icon name={result.all_passed ? "approve" : "cross"} size={24} />
        {result.all_passed
          ? `Passes all ${result.criteria.length} stability checks`
          : `Fails ${failed.length || "the"} ${failed.length === 1 ? "check" : "checks"}`}
      </p>
      <dl className="figures">
        <Stat icon="displacement" label="Displacement" value={`${result.displacement_t.toLocaleString()} t`} meaning={STAT_MEANING.displacement} />
        <Stat icon="draught" label="Draught fwd / aft" value={`${result.draught_fwd_m.toFixed(2)} / ${result.draught_aft_m.toFixed(2)} m`} meaning={STAT_MEANING.draught} />
        <Stat icon="trim" label="Trim" value={`${Math.abs(result.trim_m).toFixed(2)} m ${result.trim_m >= 0 ? "by stern" : "by head"}`} meaning={STAT_MEANING.trim} />
        <Stat icon="list" label="List" value={lean === null ? "unstable" : `${Math.abs(lean).toFixed(1)}° ${lean > 0 ? "stbd" : lean < 0 ? "port" : ""}`} meaning={STAT_MEANING.list} />
        <Stat icon="stability" label="GM (fluid)" value={`${result.gm_fluid_m.toFixed(2)} m`} meaning={STAT_MEANING.gm} />
        <Stat icon="free" label="Free surface" value={`${result.free_surface_correction_m.toFixed(2)} m`} meaning={STAT_MEANING.free} />
      </dl>
      <GzChart angles={result.gz_curve.anglesDeg} gz={result.gz_curve.gzM} />
      <ul className="criteria">
        {result.criteria.map((c) => (
          <li key={c.id} className={c.passed ? "pass" : "fail"}>
            <div className="criterion-head">
              <span className="criterion-mark"><Icon name={c.passed ? "approve" : "cross"} size={14} label={c.passed ? "Pass" : "Fail"} /></span>
              <span className="criterion-name">{c.description}</span>
              <span className="criterion-value">
                {c.actual.toFixed(c.unit === "deg" ? 0 : c.unit === "m" ? 2 : 3)} {c.unit}
                <span className="muted"> / min {c.required}</span>
              </span>
            </div>
            {CRITERION_MEANING[c.id] && <p className="criterion-meaning">{CRITERION_MEANING[c.id]}</p>}
            {/* bar: the required value sits at 50 %, so a full bar is twice the minimum */}
            <div className="criterion-bar" aria-hidden="true">
              <span style={{ width: `${Math.max(2, Math.min(100, (c.actual / (2 * c.required)) * 100))}%` }} />
              <i />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

// Shows "Verdict: / Why: / What to do:" text as a short structured card.
export function Explanation({ text, source }: { text: string; source?: "ai" | "plain" }) {
  const sections: { title: string; lines: string[] }[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const head = line.match(/^(Verdict|Why|What to do|Before arrival|At berth before loading|Before sailing):\s*(.*)$/i);
    if (head) sections.push({ title: head[1], lines: head[2] ? [head[2]] : [] });
    else if (sections.length) sections[sections.length - 1].lines.push(line.replace(/^[-•*]\s*/, ""));
    else sections.push({ title: "", lines: [line] });
  }
  return (
    <div className="explanation">
      <p className="explanation-tag">
        <Icon name="sparkle" size={14} /> {source === "plain" ? "Plain-language summary" : "Explained by AI in plain words"}
      </p>
      {sections.map((s, i) => (
        <div key={i} className="explanation-part">
          {s.title && <strong>{s.title}</strong>}
          {s.title.toLowerCase() === "verdict" || s.lines.length === 1
            ? <p>{s.lines.join(" ")}</p>
            : <ul>{s.lines.map((l, j) => <li key={j}>{l}</li>)}</ul>}
        </div>
      ))}
    </div>
  );
}
