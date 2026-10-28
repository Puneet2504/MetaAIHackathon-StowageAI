import { useEffect, useState } from "react";
import Icon from "./Icons";

// The steps of turning a drawing or tank table into a vessel, in order.
export const BUILD_STEPS = [
  { icon: "upload", label: "Sending your drawing or text", hint: "" },
  { icon: "sparkle", label: "AI is reading the tank layout", hint: "Large drawings with many tanks can take a minute or two." },
  { icon: "gauge", label: "Checking and correcting", hint: "Estimating missing dimensions, fixing misread digits and duplicate labels." },
  { icon: "ship", label: "Building the vessel", hint: "Hydrostatics, tank tables and the tank plan preview." },
] as const;

export type BuildStatus = "running" | "done" | "needs-input" | "failed";

interface Props {
  step: number;            // index into BUILD_STEPS of the step in progress
  status: BuildStatus;
  startedAt: number;       // Date.now() when the read started
  message?: string | null; // shown when finished or failed
  onCancel?: () => void;
}

export default function BuildProgress({ step, status, startedAt, message, onCancel }: Props) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (status !== "running") return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [status]);

  const secs = Math.max(0, Math.round((now - startedAt) / 1000));
  // Overall bar: each finished step is a quarter; the AI step creeps forward while it waits.
  const within = step === 1 ? Math.min(0.9, secs / 90) : 0.5;
  const pct = status === "running" ? Math.round(((step + within) / BUILD_STEPS.length) * 100) : 100;

  return (
    <div className={`build-progress ${status}`} role="status" aria-live="polite">
      <div className="row-between">
        <strong className="build-title">
          {status === "running" && <span className="spinner" aria-hidden="true" />}
          {status === "done" && <Icon name="approve" size={18} />}
          {status === "needs-input" && <Icon name="edit" size={18} />}
          {status === "failed" && <Icon name="cross" size={18} />}
          {status === "running" ? "Creating the vessel…" : status === "done" ? "Vessel created" :
            status === "needs-input" ? "Layout read. A few fields need your input" : "Could not read the drawing"}
        </strong>
        <span className="muted small">
          {status === "running" ? `${secs}s` : `in ${secs}s`}
          {status === "running" && onCancel && <> · <button className="link" onClick={onCancel}>Cancel</button></>}
        </span>
      </div>
      <div className="build-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></div>
      <ol className="build-steps">
        {BUILD_STEPS.map((s, i) => {
          const state = status === "failed" && i === step ? "failed"
            : i < step || (status !== "running" && status !== "failed") ? "done"
            : i === step ? (status === "running" ? "active" : "done") : "todo";
          const shown = state;
          return (
            <li key={s.label} className={`step ${shown}`}>
              <span className="step-dot"><Icon name={shown === "done" ? "approve" : shown === "failed" ? "cross" : s.icon} size={14} /></span>
              <span>
                <span className="step-label">{s.label}</span>
                {shown === "active" && s.hint && <span className="muted small build-hint">{s.hint}</span>}
              </span>
            </li>
          );
        })}
      </ol>
      {message && status !== "running" && <p className={status === "failed" ? "error" : "muted small"}>{message}</p>}
    </div>
  );
}
