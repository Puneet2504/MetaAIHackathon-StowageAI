import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { GRADES, gradeById } from "./grades";
import HullPlan, { type TankState } from "./HullPlan";
import GzChart from "./GzChart";
import type { VesselModel } from "./types/vessel-model";
import type { Parcel, StabilityResult, TankLoad } from "./types/api";

const VESSEL_ID = "demo_mr2";
// Fixed bunkers and fresh water for the demo condition.
const FIXED: TankLoad[] = [
  { tank_id: "FOP", volume_m3: 300, density_t_m3: 0.95, grade: "Fuel oil" },
  { tank_id: "FOS", volume_m3: 300, density_t_m3: 0.95, grade: "Fuel oil" },
  { tank_id: "FW", volume_m3: 200, density_t_m3: 1.0, grade: "Fresh water" },
];
const SAMPLE_ORDER =
  "Load Rotterdam: 20,000 mt ULSD, 12,000 mt Jet A-1. Discharge ULSD at Le Havre, Jet at Bilbao.";

type Preset = "full" | "mixed" | "starboard" | "clear";

export default function App() {
  const [vessel, setVessel] = useState<VesselModel | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [state, setState] = useState<Record<string, TankState>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<StabilityResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<string | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [order, setOrder] = useState(SAMPLE_ORDER);
  const [parcels, setParcels] = useState<Parcel[] | null>(null);
  const [orderNotes, setOrderNotes] = useState<string[]>([]);
  const [orderError, setOrderError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);

  useEffect(() => {
    api.vessel(VESSEL_ID)
      .then(setVessel)
      .catch((e) => setLoadError(`Couldn't load the vessel. Is the backend running on port 8000? (${e.message})`));
  }, []);

  const cargoTanks = useMemo(
    () => (vessel?.tanks ?? []).filter((t) => t.type === "cargo" || t.type === "slop"),
    [vessel]
  );

  const applyPreset = (p: Preset) => {
    const next: Record<string, TankState> = {};
    cargoTanks.forEach((t) => {
      const no = Number(t.id.replace(/\D/g, "")) || 0;
      if (t.type === "slop" || p === "clear") next[t.id] = { grade: null, fillPct: 0 };
      else if (p === "full") next[t.id] = { grade: "ULSD", fillPct: 98 };
      else if (p === "mixed") next[t.id] = { grade: no <= 2 ? "JET" : no <= 4 ? "ULSD" : "GASOLINE", fillPct: 98 };
      else next[t.id] = t.side === "S" ? { grade: "ULSD", fillPct: 98 } : { grade: null, fillPct: 0 };
    });
    setState(next);
    setResult(null);
    setExplanation(null);
  };

  useEffect(() => { if (vessel) applyPreset("mixed"); }, [vessel]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (id: string, patch: Partial<TankState>) => {
    setState((s) => ({ ...s, [id]: { ...s[id], ...patch } }));
    setResult(null);
    setExplanation(null);
  };

  const buildLoads = (): TankLoad[] => {
    const loads: TankLoad[] = [];
    cargoTanks.forEach((t) => {
      const s = state[t.id];
      const g = gradeById(s?.grade ?? null);
      if (!g || !s.fillPct) return;
      loads.push({ tank_id: t.id, volume_m3: (t.capacity100 * s.fillPct) / 100, density_t_m3: g.density, grade: g.id });
    });
    return [...loads, ...FIXED];
  };

  const totals = useMemo(() => {
    const byGrade: Record<string, number> = {};
    cargoTanks.forEach((t) => {
      const s = state[t.id];
      const g = gradeById(s?.grade ?? null);
      if (g && s.fillPct) byGrade[g.id] = (byGrade[g.id] ?? 0) + (t.capacity100 * s.fillPct / 100) * g.density;
    });
    return byGrade;
  }, [state, cargoTanks]);

  const runCheck = async () => {
    setChecking(true); setCheckError(null); setExplanation(null);
    try { setResult(await api.check(VESSEL_ID, buildLoads())); }
    catch (e) { setResult(null); setCheckError((e as Error).message); }
    finally { setChecking(false); }
  };

  const runExplain = async () => {
    if (!result) return;
    setExplaining(true);
    try { setExplanation((await api.explain(result)).explanation); }
    catch (e) { setExplanation(`Explanation unavailable: ${(e as Error).message}`); }
    finally { setExplaining(false); }
  };

  const readOrder = async () => {
    setReading(true); setOrderError(null); setParcels(null); setOrderNotes([]);
    try { const r = await api.parseOrder(order); setParcels(r.parcels); setOrderNotes(r.notes ?? []); }
    catch (e) { setOrderError((e as Error).message); }
    finally { setReading(false); }
  };

  if (loadError) return <main className="shell"><p className="error">{loadError}</p></main>;
  if (!vessel) return <main className="shell"><p className="muted">Loading vessel…</p></main>;

  const failed = result?.criteria.filter((c) => !c.passed) ?? [];

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <h1>{vessel.particulars.name}</h1>
          <p className="muted">
            {vessel.particulars.sizeClass} product tanker, {vessel.particulars.lbp} m by {vessel.particulars.breadth} m,
            summer draught {vessel.particulars.summerDraught} m
          </p>
        </div>
        <p className="badge" title={vessel.source.notes}>Synthetic demo data. Not for real loading decisions.</p>
      </header>

      <section className="plan" aria-labelledby="plan-h">
        <h2 id="plan-h" className="visually-hidden">Tank plan</h2>
        <HullPlan vessel={vessel} state={state} selected={selected} onSelect={setSelected} />
        <ul className="legend">
          {GRADES.map((g) => (
            <li key={g.id}><span className="swatch" style={{ background: g.color }} />{g.name}
              {totals[g.id] ? <strong> {Math.round(totals[g.id]).toLocaleString()} t</strong> : null}
            </li>
          ))}
        </ul>
      </section>

      <div className="columns">
        <section aria-labelledby="loads-h">
          <div className="row-between">
            <h2 id="loads-h">Tank loads</h2>
            <div className="presets" role="group" aria-label="Example conditions">
              <button onClick={() => applyPreset("mixed")}>Three grades</button>
              <button onClick={() => applyPreset("full")}>Full diesel</button>
              <button onClick={() => applyPreset("starboard")}>Starboard only</button>
              <button onClick={() => applyPreset("clear")}>Clear</button>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Tank</th><th>Grade</th><th>Fill %</th><th className="num">m³</th></tr></thead>
              <tbody>
                {cargoTanks.map((t) => {
                  const s = state[t.id] ?? { grade: null, fillPct: 0 };
                  return (
                    <tr key={t.id} className={selected === t.id ? "selected" : ""} onClick={() => setSelected(t.id)}>
                      <td>{t.id}</td>
                      <td>
                        <select aria-label={`Grade in ${t.id}`} value={s.grade ?? ""}
                          onChange={(e) => update(t.id, { grade: e.target.value || null, fillPct: e.target.value ? (s.fillPct || 98) : 0 })}>
                          <option value="">Empty</option>
                          {GRADES.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                        </select>
                      </td>
                      <td>
                        <input aria-label={`Fill percentage of ${t.id}`} type="number" min={0} max={100} step={1}
                          value={s.fillPct} disabled={!s.grade}
                          onChange={(e) => update(t.id, { fillPct: Math.max(0, Math.min(100, Number(e.target.value))) })} />
                      </td>
                      <td className="num">{Math.round(t.capacity100 * s.fillPct / 100).toLocaleString()}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted small">Bunkers and fresh water are fixed for the demo (fuel oil 600 m³, fresh water 200 m³).</p>
          <button className="primary" onClick={runCheck} disabled={checking}>
            {checking ? "Checking…" : "Check stability"}
          </button>
          {checkError && <p className="error">{checkError}</p>}
        </section>

        <section aria-labelledby="result-h" aria-live="polite">
          <h2 id="result-h">Stability</h2>
          {!result && !checkError && <p className="muted">Set the tank loads, then select Check stability.</p>}
          {result && (
            <>
              <p className={`verdict ${result.all_passed ? "pass" : "fail"}`}>
                {result.all_passed
                  ? `Passes all ${result.criteria.length} criteria`
                  : `Fails ${failed.length || "the"} ${failed.length === 1 ? "criterion" : "criteria"}`}
              </p>
              <dl className="figures">
                <div><dt>Displacement</dt><dd>{result.displacement_t.toLocaleString()} t</dd></div>
                <div><dt>Draught fwd / aft</dt><dd>{result.draught_fwd_m.toFixed(2)} / {result.draught_aft_m.toFixed(2)} m</dd></div>
                <div><dt>Trim</dt><dd>{Math.abs(result.trim_m).toFixed(2)} m {result.trim_m >= 0 ? "by stern" : "by head"}</dd></div>
                <div><dt>List</dt><dd>{result.list_deg === null ? "unstable" : `${Math.abs(result.list_deg).toFixed(1)}° ${result.list_deg > 0 ? "stbd" : result.list_deg < 0 ? "port" : ""}`}</dd></div>
                <div><dt>GM (fluid)</dt><dd>{result.gm_fluid_m.toFixed(2)} m</dd></div>
                <div><dt>Free surface</dt><dd>{result.free_surface_correction_m.toFixed(2)} m</dd></div>
              </dl>
              <GzChart angles={result.gz_curve.anglesDeg} gz={result.gz_curve.gzM} />
              <ul className="criteria">
                {result.criteria.map((c) => (
                  <li key={c.id} className={c.passed ? "pass" : "fail"}>
                    <span>{c.passed ? "Pass" : "Fail"}</span>
                    {c.description}: {c.actual.toFixed(c.unit === "deg" ? 0 : c.unit === "m" ? 2 : 3)} {c.unit} (min {c.required})
                  </li>
                ))}
              </ul>
              {result.warnings.length > 0 && (
                <ul className="warnings">{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
              )}
              <button onClick={runExplain} disabled={explaining}>{explaining ? "Explaining…" : "Explain with AI"}</button>
              {explanation && <p className="explanation">{explanation}</p>}
            </>
          )}
        </section>
      </div>

      <section className="order" aria-labelledby="order-h">
        <h2 id="order-h">Voyage order</h2>
        <p className="muted small">Paste a charterer's order. The AI reads it into parcels; you confirm them before planning.</p>
        <textarea value={order} onChange={(e) => setOrder(e.target.value)} rows={3} aria-label="Voyage order text" />
        <button onClick={readOrder} disabled={reading || order.trim().length < 10}>
          {reading ? "Reading…" : "Read order with AI"}
        </button>
        {orderError && <p className="error">{orderError}</p>}
        {parcels && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Grade</th><th className="num">Quantity</th><th>Load</th><th>Discharge</th></tr></thead>
              <tbody>
                {parcels.map((p, i) => (
                  <tr key={i}>
                    <td>{p.grade}</td>
                    <td className="num">{p.quantity_t != null ? `${p.quantity_t.toLocaleString()} t` : p.quantity_m3 != null ? `${p.quantity_m3.toLocaleString()} m³` : "missing"}</td>
                    <td>{p.load_port ?? "missing"}</td>
                    <td>{p.discharge_port ?? "missing"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {orderNotes.length > 0 && <ul className="warnings">{orderNotes.map((n) => <li key={n}>{n}</li>)}</ul>}
          </div>
        )}
      </section>
    </main>
  );
}
