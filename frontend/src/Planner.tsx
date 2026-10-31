import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { addGrade, allGrades, gradeById } from "./grades";
import HullPlan, { type TankState } from "./HullPlan";
import Icon from "./Icons";
import Cleaning from "./Cleaning";
import Quantities from "./Quantities";
import VoyageCheck from "./VoyageCheck";
import { SpaceSuggestions } from "./OpenSpace";
import StabilityPanel, { Explanation } from "./StabilityPanel";
import type { VesselModel } from "./types/vessel-model";
import type { ApprovedPlanSummary, MemoryItem, Parcel, PlanResponse, PlanStage, StabilityResult, StowageConstraints,
  OpenSpaceShip, SpaceAnswer, SpaceOffer, TankLoad, VoyageCheckReport } from "./types/api";

// Fixed bunkers and fresh water for the demo condition; only those tanks the ship has are used.
// The solver uses the same values on the server (app/stowage/solver.py).
const FIXED: TankLoad[] = [
  { tank_id: "FOP", volume_m3: 300, density_t_m3: 0.95, grade: "Fuel oil" },
  { tank_id: "FOS", volume_m3: 300, density_t_m3: 0.95, grade: "Fuel oil" },
  { tank_id: "FW", volume_m3: 200, density_t_m3: 1.0, grade: "Fresh water" },
];
const SAMPLE_ORDER =
  "Load Rotterdam: 20,000 mt ULSD, 12,000 mt Jet A-1. Discharge ULSD at Le Havre, Jet at Bilbao.";
// The sample order already read into parcels, so the solver can be shown without a model.
const SAMPLE_PARCELS: Parcel[] = [
  { grade: "ULSD", quantity_t: 20000, quantity_m3: null, density_t_m3: null, load_port: "Rotterdam", discharge_port: "Le Havre" },
  { grade: "Jet A-1", quantity_t: 12000, quantity_m3: null, density_t_m3: null, load_port: "Rotterdam", discharge_port: "Bilbao" },
];
const NO_CONSTRAINTS: StowageConstraints = { empty_tanks: [], fixed_grades: {}, max_trim_m: null, fill: null };
const STATUS_TEXT: Record<string, string> = {
  planned: "Plan ready", partial: "Plan ready, cargo left over", fails_criteria: "No passing plan found",
  cannot_segregate: "Cannot segregate these grades", nothing_to_plan: "Nothing to plan",
};

type Preset = "full" | "mixed" | "starboard" | "clear";
type StepState = "todo" | "active" | "done" | "failed";

// Where the one-click pipeline has got to: read → allocate → check → explain → approve.
function steps(planning: boolean, error: string | null, r: PlanResponse | null, approved: string | null) {
  const s: [string, string, StepState][] = [
    ["order", "Read order", "todo"], ["allocate", "Allocate", "todo"], ["gauge", "Check stability", "todo"],
    ["sparkle", "Explain", "todo"], ["approve", "Approve", "todo"],
  ];
  if (planning) { s[0][2] = "active"; return s; }
  if (error && !r) { s[0][2] = "failed"; return s; }
  if (!r) return s;
  const p = r.plan;
  s[0][2] = "done";
  s[1][2] = p.allocation.length ? "done" : "failed";
  s[2][2] = !p.result ? "todo" : p.status === "fails_criteria" ? "failed" : "done";
  s[3][2] = r.explanation ? "done" : "todo";
  s[4][2] = approved ? "done" : p.result && p.status !== "fails_criteria" ? "active" : "todo";
  return s;
}

function Stepper({ steps: list }: { steps: [string, string, StepState][] }) {
  return (
    <ol className="stepper" aria-label="Planning steps">
      {list.map(([icon, label, state]) => (
        <li key={label} className={`step ${state}`} aria-current={state === "active" ? "step" : undefined}>
          <span className="step-dot"><Icon name={state === "failed" ? "cross" : state === "done" ? "approve" : icon} size={16} /></span>
          <span className="step-label">{label}</span>
        </li>
      ))}
    </ol>
  );
}

interface Props {
  vesselId: string;
  onBack: () => void;
  onOpenVoyages: () => void;
  onOpenVoyage: (id: string) => void;
  initialOrder?: string;
}

export default function Planner({ vesselId, onBack, onOpenVoyages, onOpenVoyage, initialOrder }: Props) {
  const [vessel, setVessel] = useState<VesselModel | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [state, setState] = useState<Record<string, TankState>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<StabilityResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<string | null>(null);
  const [explanationSource, setExplanationSource] = useState<"ai" | "plain">("ai");
  const [explaining, setExplaining] = useState(false);
  const [order, setOrder] = useState(initialOrder ?? SAMPLE_ORDER);
  const [memoryOff, setMemoryOff] = useState<string[]>([]);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [approved, setApproved] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [stageView, setStageView] = useState(0);  // 0: departure condition; n: after the n-th reload port
  // The order's parcels and text stay known after hand edits, so a hand-edited condition can be checked port by port.
  const [orderParcels, setOrderParcels] = useState<{ parcels: Parcel[]; order: string } | null>(null);
  const [manualCheck, setManualCheck] = useState<VoyageCheckReport | null>(null);
  const [applying, setApplying] = useState(false);
  // Open space on this ship's current voyage, and what the AI says still fits (for charterers placing an order).
  const [space, setSpace] = useState<OpenSpaceShip | null>(null);
  const [spaceAnswer, setSpaceAnswer] = useState<SpaceAnswer | null>(null);
  const [askingSpace, setAskingSpace] = useState(false);
  const [addingSpace, setAddingSpace] = useState<number | null>(null);
  const [spaceAdded, setSpaceAdded] = useState<string | null>(null);
  const approvingRef = useRef(false);  // set at once, so a fast second click can't approve twice
  // The order text the current plan was made from; saved with the voyage on approval.
  const [plannedOrder, setPlannedOrder] = useState<string | null>(null);
  const [history, setHistory] = useState<ApprovedPlanSummary[]>([]);
  // Every plan, check or manual change takes a new number; a reply is only shown if it is still the latest,
  // so a slow reply can't overwrite newer tanks or results.
  const reqRef = useRef(0);
  const planReq = useRef(0);
  const checkReq = useRef(0);
  const resultRef = useRef<StabilityResult | null>(null);
  resultRef.current = result;

  useEffect(() => {
    api.vessel(vesselId)
      .then(setVessel)
      .catch((e) => setLoadError(`Couldn't load the vessel. Is the backend running on port 8000? (${e.message})`));
    api.listPlans(vesselId).then(setHistory).catch(() => setHistory([]));
    api.openSpaceFor(vesselId).then(setSpace).catch(() => setSpace(null));
  }, [vesselId]);

  const askSpace = async () => {
    setAskingSpace(true);
    try { setSpaceAnswer(await api.openSpaceSuggest(vesselId)); }
    catch (e) { setSpaceAnswer(null); setPlanError((e as Error).message); }
    finally { setAskingSpace(false); }
  };

  // Add a verified suggestion straight into the ship's current voyage (no new voyage, existing cargo stays put).
  const addToVoyage = async (s: SpaceOffer) => {
    const voyage = space?.voyage;
    if (!voyage || !window.confirm(`Add ${Math.round(s.quantity_t).toLocaleString()} mt ${s.grade} at ${s.port} for ${s.discharge_port} to the current voyage?`)) return;
    setAddingSpace(spaceAnswer?.ai?.suggestions.indexOf(s) ?? 0);
    try {
      await api.addToVoyage(voyage.id, s);
      setSpaceAdded(`${s.order_line} Added to the current voyage.`);
      setSpaceAnswer(null);
      setSpace(await api.openSpaceFor(vesselId));
      api.listPlans(vesselId).then(setHistory).catch(() => {});
    } catch (e) { setPlanError((e as Error).message); }
    finally { setAddingSpace(null); }
  };

  // Add a suggestion to the order box; the current voyage's order goes in too, so its cargo is replanned with it.
  const addToOrder = (line: string) => {
    const current = space?.voyage?.order_text?.trim() ?? "";
    setOrder((o) => (current && !o.includes(current) ? `${current} ${line}` : `${o.trim()} ${line}`.trim()));
  };

  const cargoTanks = useMemo(
    () => (vessel?.tanks ?? []).filter((t) => t.type === "cargo" || t.type === "slop"),
    [vessel]
  );
  const fixed = useMemo(
    () => FIXED.filter((f) => vessel?.tanks.some((t) => t.id === f.tank_id)),
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
    reqRef.current++;
    setManualCheck(null);
    setState(next);
    setResult(null);
    setExplanation(null);
    setPlan(null);
  };

  useEffect(() => { if (vessel) applyPreset("clear"); }, [vessel]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (id: string, patch: Partial<TankState>) => {
    reqRef.current++;
    setManualCheck(null);
    setApproved(null);
    setState((s) => ({ ...s, [id]: { ...s[id], ...patch } }));
    setResult(null);
    setExplanation(null);
    setCheckError(null);
    setPlan(null); // a manual change means the AI plan no longer applies
  };

  const buildLoads = (from: Record<string, TankState> = state): TankLoad[] => {
    const loads: TankLoad[] = [];
    cargoTanks.forEach((t) => {
      const s = from[t.id];
      const g = gradeById(s?.grade ?? null);
      if (!g || !s.fillPct) return;
      loads.push({ tank_id: t.id, volume_m3: (t.capacity100 * s.fillPct) / 100, density_t_m3: s.density ?? g.density, grade: g.id });
    });
    return [...loads, ...fixed];
  };

  const totals = useMemo(() => {
    const byGrade: Record<string, number> = {};
    cargoTanks.forEach((t) => {
      const s = state[t.id];
      const g = gradeById(s?.grade ?? null);
      if (g && s.fillPct) byGrade[g.id] = (byGrade[g.id] ?? 0) + (t.capacity100 * s.fillPct / 100) * (s.density ?? g.density);
    });
    return byGrade;
  }, [state, cargoTanks]);


  // Show a plan from the solver: tank states, stability result and explanation.
  const showPlan = (r: PlanResponse) => {
    setPlan(r);
    setApproved(null);
    setOrderParcels({ parcels: r.parcels, order: plannedOrder ?? order });
    setStageView(0);
    setManualCheck(null);
    r.plan.allocation.forEach((a) => addGrade(a.grade_id, a.grade, a.density_t_m3));
    const density: Record<string, number> = Object.fromEntries(r.plan.allocation.map((a) => [a.grade_id, a.density_t_m3]));
    const next: Record<string, TankState> = {};
    cargoTanks.forEach((t) => { next[t.id] = { grade: null, fillPct: 0 }; });
    r.plan.loads.forEach((l) => {
      const t = cargoTanks.find((c) => c.id === l.tank_id);
      if (t) next[t.id] = { grade: l.grade ?? null, fillPct: Math.round((1000 * l.volume_m3) / t.capacity100) / 10,
        density: l.grade ? density[l.grade] ?? l.density_t_m3 : undefined };
    });
    setState(next);
    setResult(r.plan.result);
    setExplanation(r.explanation);
    setExplanationSource(r.explanation_source);
    setCheckError(null);
  };

  const runPlan = async (opts: { parcels?: Parcel[]; instructions?: string; memoryOff?: string[] } = {}) => {
    // A new order starts clean; changes and memory toggles build on the current plan.
    const fresh = !opts.instructions && !opts.memoryOff;
    const n = ++reqRef.current;
    planReq.current = n;
    if (fresh) setMemoryOff([]);
    setPlanning(true); setPlanError(null);
    try {
      const r = await api.planVoyage({
        vessel_id: vesselId,
        order_text: opts.parcels ? undefined : order,
        parcels: opts.parcels ?? (fresh ? undefined : plan?.parcels),
        instructions: opts.instructions,
        constraints: fresh ? NO_CONSTRAINTS : plan?.constraints ?? NO_CONSTRAINTS,
        memory_off: fresh ? [] : opts.memoryOff ?? memoryOff,
      });
      if (n !== reqRef.current) return;
      showPlan(r);
      if (fresh) {
        const text = opts.parcels ? SAMPLE_ORDER : order;
        setPlannedOrder(text);
        setOrderParcels({ parcels: r.parcels, order: text });
      }
      setInstruction("");
    } catch (e) { if (n === reqRef.current) setPlanError((e as Error).message); }
    finally { if (planReq.current === n) setPlanning(false); }
  };

  const approve = async () => {
    if (!plan || approvingRef.current) return;
    approvingRef.current = true;
    setApproving(true);
    try {
      const r = await api.approvePlan({ vessel_id: vesselId, order_text: plannedOrder ?? order, parcels: plan.parcels,
        constraints: plan.applied_constraints, plan: plan.plan, explanation: plan.explanation });
      setApproved(r.id);
      api.listPlans(vesselId).then(setHistory).catch(() => {});
    } catch (e) { setPlanError((e as Error).message); }
    finally { approvingRef.current = false; setApproving(false); }
  };

  const reject = () => { setPlan(null); setApproved(null); setMemoryOff([]); applyPreset("clear"); };

  // Stability of the condition on departure, and, when the order is known, the voyage check through its ports.
  const runCheck = async (from: Record<string, TankState> = state) => {
    const n = ++reqRef.current;
    checkReq.current = n;
    setChecking(true); setCheckError(null); setExplanation(null); setManualCheck(null);
    const loads = buildLoads(from);
    try {
      const [r, v] = await Promise.all([
        api.check(vesselId, loads),
        orderParcels ? api.checkVoyage({ vessel_id: vesselId, parcels: orderParcels.parcels, loads }) : Promise.resolve(null),
      ]);
      if (n === reqRef.current) { setResult(r); setManualCheck(v?.check ?? null); }
    } catch (e) { if (n === reqRef.current) { setResult(null); setCheckError((e as Error).message); } }
    finally { if (checkReq.current === n) setChecking(false); }
  };

  // Put the suggested tank swaps into the tanks, then check everything again.
  const applySwaps = async () => {
    const swap = manualCheck?.suggestion;
    if (!swap) return;
    setApplying(true);
    const next: Record<string, TankState> = {};
    cargoTanks.forEach((t) => {
      const l = swap.loads.find((x) => x.tank_id === t.id);
      next[t.id] = l ? { grade: l.grade ?? null, fillPct: Math.round((1000 * l.volume_m3) / t.capacity100) / 10, density: l.density_t_m3 }
        : { grade: null, fillPct: 0 };
    });
    setState(next);
    await runCheck(next);
    setApplying(false);
  };

  // A hand-edited condition that passes stability and the voyage check can be approved; the server checks it again.
  const approveManual = async () => {
    if (!orderParcels || approvingRef.current) return;
    approvingRef.current = true;
    setApproving(true);
    try {
      const r = await api.approveManual({ vessel_id: vesselId, order_text: orderParcels.order, parcels: orderParcels.parcels,
        loads: buildLoads() });
      setApproved(r.id);
      api.listPlans(vesselId).then(setHistory).catch(() => {});
    } catch (e) { setCheckError((e as Error).message); }
    finally { approvingRef.current = false; setApproving(false); }
  };

  const runExplain = async () => {
    if (!result) return;
    const target = result;
    setExplaining(true);
    try {
      const r = await api.explain(target);
      if (resultRef.current === target) { setExplanation(r.explanation); setExplanationSource(r.source); }
    } catch (e) { if (resultRef.current === target) setExplanation(`Explanation unavailable: ${(e as Error).message}`); }
    finally { setExplaining(false); }
  };

  if (loadError) return <><p className="error">{loadError}</p><button onClick={onBack}>Back to ships</button></>;
  if (!vessel) return <p className="muted">Loading vessel…</p>;

  const p = plan?.plan;
  const stages: PlanStage[] = p?.stages ?? [];
  const stage = stageView > 0 ? stages[stageView - 1] ?? null : null;
  // The hull shows the chosen condition: departure from the first port, or after a reload port.
  const hullState: Record<string, TankState> = !stage ? state : Object.fromEntries(cargoTanks.map((t) => {
    const l = stage.loads.find((x) => x.tank_id === t.id);
    return [t.id, l ? { grade: l.grade ?? null, fillPct: Math.round((1000 * l.volume_m3) / t.capacity100) / 10, density: l.density_t_m3 }
      : { grade: null, fillPct: 0 }];
  }));
  const c = plan?.applied_constraints;
  // Remembered rules: the ones used in this plan, plus any switched off for it.
  const memoryChips: MemoryItem[] = plan ? plan.memory_used : [];
  const toggleMemory = (id: string) => {
    const next = memoryOff.includes(id) ? memoryOff.filter((x) => x !== id) : [...memoryOff, id];
    setMemoryOff(next);
    if (plan) runPlan({ memoryOff: next });
  };
  // While a plan is shown, the legend gives the tonnes it placed in the order's basis; after manual edits, tank totals.
  const shownTotals: Record<string, number> = p && p.allocation.every((a) => a.tonnes !== undefined)
    ? p.allocation.reduce((acc, a) => ({ ...acc, [a.grade_id]: (acc[a.grade_id] ?? 0) + a.tonnes! }), {} as Record<string, number>)
    : totals;
  const hasConstraints = !!c && (c.empty_tanks.length > 0 || Object.keys(c.fixed_grades).length > 0 || c.max_trim_m !== null || !!c.fill);
  const modelMissing = planError?.includes("No model configured");

  return (
    <>
      <header className="masthead">
        <div>
          <button className="link" onClick={onBack}>← All ships</button>
          <h1>{vessel.particulars.name}</h1>
          <div className="chips">
            <span className="class-badge">{vessel.particulars.sizeClass}</span>
            <span className="chip">LBP {vessel.particulars.lbp} m</span>
            <span className="chip">Breadth {vessel.particulars.breadth} m</span>
            <span className="chip">Summer draught {vessel.particulars.summerDraught} m</span>
          </div>
        </div>
        <p className="badge" title={vessel.source.notes}>
          {vessel.source.origin === "user_drawing" ? "Estimated hydrostatics." : "Synthetic demo data."} Not for real loading decisions.
        </p>
      </header>

      <section className="voyage card" aria-labelledby="voyage-h">
        <div className="row-between">
          <h2 id="voyage-h">Voyage plan</h2>
          <Stepper steps={steps(planning, planError, plan, approved)} />
        </div>
        <p className="muted small">
          Paste the charterer's order. The AI reads it, the solver places every parcel within the cargo lines and
          fill limits, and the stability engine checks each candidate. You approve the result.
        </p>
        <textarea value={order} onChange={(e) => setOrder(e.target.value)} rows={3} aria-label="Voyage order text" />
        <button className="primary" onClick={() => runPlan()} disabled={planning || order.trim().length < 10}>
          <Icon name="sparkle" size={18} /> {planning ? "Planning…" : "Plan this voyage"}
        </button>
        {planError && <p className="error">{planError}</p>}
        {space?.voyage && (
          <details className="manual open-space-panel">
            <summary>Open space on this ship's current voyage</summary>
            <p className="muted small">
              Empty tanks after each port: {space.voyage.facts.ports.map((p) => `${p.port}: ${p.empty.map((e) => e.tanks).join(", ") || "none"}`).join(" · ")}.
              Adding a suggestion puts the current voyage's order in the box too, so all its cargo is replanned together.
            </p>
            {!spaceAnswer && (
              <button className="small-btn" onClick={askSpace} disabled={askingSpace}>
                {askingSpace ? <span className="spinner" aria-hidden="true" /> : <Icon name="sparkle" size={14} />}
                {askingSpace ? " The AI is checking…" : " Ask AI what fits"}
              </button>
            )}
            {spaceAnswer && <SpaceSuggestions answer={spaceAnswer} onUse={addToOrder} useLabel="Add to order"
              onAdd={addToVoyage} adding={addingSpace} />}
            {spaceAdded && <p className="saving" role="status"><Icon name="approve" size={16} /> {spaceAdded}</p>}
          </details>
        )}
        {plan && (memoryChips.length > 0 || memoryOff.length > 0) && (
          <div className="memory-row">
            <span className="small muted"><Icon name="memory" size={14} /> Using memory:</span>
            {memoryChips.map((m) => (
              <button key={m.id} className="memory-chip on" onClick={() => toggleMemory(m.id)} disabled={planning}
                title="Remembered rule. Click to leave it out of this plan.">{m.text} <Icon name="cross" size={12} /></button>
            ))}
            {memoryOff.map((id) => (
              <button key={id} className="memory-chip" onClick={() => toggleMemory(id)} disabled={planning} title="Switched off for this plan. Click to use it again.">
                Rule switched off <Icon name="plus" size={12} />
              </button>
            ))}
          </div>
        )}
        {plan && (plan.remembered.length > 0 || plan.forgotten.length > 0) && (
          <p className="memory-toast" role="status">
            <Icon name="memory" size={16} />
            {plan.remembered.length > 0 && <>Remembered for next time: {plan.remembered.map((m) => m.text).join("; ")}. </>}
            {plan.forgotten.length > 0 && <>Forgotten: {plan.forgotten.map((m) => m.text).join("; ")}.</>}
          </p>
        )}
        {modelMissing && (
          <p className="small">
            <button className="link" onClick={() => runPlan({ parcels: SAMPLE_PARCELS })} disabled={planning}>Plan the sample order without AI</button>
            {" "}(uses the sample order already read into parcels).
          </p>
        )}
      </section>

      <section className="plan card" aria-labelledby="plan-h">
        <h2 id="plan-h" className="visually-hidden">Tank plan</h2>
        {stages.length > 0 && (
          <div className="presets stage-picker" role="group" aria-label="Condition shown">
            {[`Departure ${plan?.parcels.find((x) => x.load_port)?.load_port ?? "first port"}`, ...stages.map((s) => `After ${s.port}`)]
              .map((label, i) => (
                <button key={label} className={stageView === i ? "primary small-btn" : "small-btn"} aria-pressed={stageView === i}
                  onClick={() => setStageView(i)}>{label}</button>
              ))}
          </div>
        )}
        <HullPlan vessel={vessel} state={hullState} selected={selected} onSelect={setSelected} />
        <ul className="legend">
          {allGrades().map((g) => (
            <li key={g.id}><span className="swatch" style={{ background: g.color }} />{g.name}
              {shownTotals[g.id] ? <strong> {Math.round(shownTotals[g.id]).toLocaleString()} t</strong> : null}
            </li>
          ))}
        </ul>
      </section>

      <div className="columns">
        <section className="card" aria-labelledby="alloc-h">
          {p ? (
            <>
              <h2 id="alloc-h">{STATUS_TEXT[p.status] ?? p.status}</h2>
              <p>{p.message}</p>
              {p.candidates_checked > 0 && (
                <p className="muted small">Checked {p.candidates_checked} of {p.candidates_total} candidate allocations with the stability engine.</p>
              )}
              {p.allocation.length > 0 && (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Grade</th><th>Lines</th><th>Tanks</th><th className="num">m³</th></tr></thead>
                    <tbody>
                      {[...p.allocation.map((a) => ({ a, at: "" })),
                        ...stages.flatMap((s) => s.allocation.map((a) => ({ a, at: s.port })))].map(({ a, at }) => (
                        <tr key={a.parcel}>
                          <td><span className="swatch" style={{ background: gradeById(a.grade_id)?.color }} />{a.grade}
                            {at && <span className="muted small"> · loaded at {at}</span>}</td>
                          <td>{a.lines.join(", ")}</td>
                          <td>{a.tanks.join(", ")}</td>
                          <td className="num">
                            {Math.round(a.allocated_m3).toLocaleString()}
                            {a.unallocated_m3 > 0.5 && <span className="error"> ({Math.round(a.unallocated_m3).toLocaleString()} left over)</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <Quantities allocation={[...p.allocation, ...stages.flatMap((s) => s.allocation)]} />
              {hasConstraints && c && (
                <p className="muted small">
                  Constraints: {[
                    c.empty_tanks.length ? `empty ${c.empty_tanks.join(", ")}` : "",
                    ...Object.entries(c.fixed_grades).map(([t, g]) => `${t} carries ${g}`),
                    c.max_trim_m !== null ? `trim at most ${c.max_trim_m} m` : "",
                    c.fill === "even" ? "cargo spread evenly over all tanks" : c.fill === "full" ? "tanks filled to 98%" : "",
                  ].filter(Boolean).join("; ")}
                </p>
              )}
              {[...plan!.parcel_notes, ...p.warnings].length > 0 && (
                <ul className="warnings">{[...plan!.parcel_notes, ...p.warnings].map((w, i) => <li key={i}>{w}</li>)}</ul>
              )}
              {approved
                ? (
                  <div className="approved-box">
                    <p className="verdict pass"><Icon name="approve" size={20} /> Approved and saved as a voyage.</p>
                    <button className="primary small-btn" onClick={() => onOpenVoyage(approved)}>View in Voyages</button>
                  </div>
                )
                : (
                  <div className="approve">
                    <div className="presets">
                      <button className="primary" onClick={approve}
                        disabled={!p.result || p.status === "fails_criteria" || approving || planning
                          || (p.voyage_check ? !p.voyage_check.ok : false)}>
                        {approving && <span className="spinner" aria-hidden="true" />}{approving ? "Approving…" : "Approve plan"}
                      </button>
                      <button onClick={reject} disabled={approving}>Reject</button>
                    </div>
                    <form className="changes" onSubmit={(e) => { e.preventDefault(); if (instruction.trim()) runPlan({ instructions: instruction }); }}>
                      <input aria-label="Ask for changes" value={instruction} onChange={(e) => setInstruction(e.target.value)}
                        placeholder="Ask for changes, e.g. keep tank 3 empty (add 'always' to remember it)" />
                      <button type="submit" disabled={planning || approving || !instruction.trim()}>Replan</button>
                    </form>
                  </div>
                )}
            </>
          ) : (
            <>
              <h2 id="alloc-h">Allocation</h2>
              <p className="muted">Plan a voyage to see where each grade goes.</p>
            </>
          )}

          <details className="manual">
            <summary>Adjust manually</summary>
            <div className="row-between">
              <span className="muted small">Changing a tank here replaces the AI plan with your own condition.</span>
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
                            onChange={(e) => update(t.id, { grade: e.target.value || null, fillPct: e.target.value ? (s.fillPct || 98) : 0, density: undefined })}>
                            <option value="">Empty</option>
                            {allGrades().map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
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
            <p className="muted small">
              {fixed.length
                ? `Bunkers and fresh water are fixed for the demo: ${fixed.map((f) => `${f.tank_id} ${f.volume_m3} m³`).join(", ")}.`
                : "This ship has no standard bunker or fresh water tanks, so none are included."}
            </p>
            <button className="primary" onClick={() => runCheck()} disabled={checking}>
              {checking ? "Checking…" : "Check stability"}
            </button>
            {checkError && <p className="error">{checkError}</p>}
          </details>

          {history.length > 0 && (
            <details className="manual">
              <summary>Approved voyages for this ship ({history.length})</summary>
              <ul className="history">
                {history.map((h) => (
                  <li key={h.id}>
                    <button className="link" onClick={() => onOpenVoyage(h.id)}>{new Date(h.approved_at).toLocaleString()}</button>
                    : {h.grades.join(", ")}
                  </li>
                ))}
              </ul>
              <button className="link" onClick={onOpenVoyages}>All voyages →</button>
            </details>
          )}
        </section>

        <section className="card" aria-labelledby="result-h" aria-live="polite">
          <h2 id="result-h">Stability</h2>
          {!result && !checkError && <p className="muted">Plan a voyage, or adjust the tanks manually and check stability.</p>}
          {stage && (
            <>
              <p className="muted small">Condition on leaving {stage.port}, after discharging {stage.discharged.join(", ") || "cargo"} and
                loading {stage.allocation.map((a) => a.grade).join(", ")}.</p>
              {stage.result ? <StabilityPanel result={stage.result} /> : <p className="error">{stage.message}</p>}
              {stage.warnings.length > 0 && <ul className="warnings">{stage.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
            </>
          )}
          {result && !stage && (
            <>
              <StabilityPanel result={result} />
              {!plan && result.warnings.length > 0 && (
                <ul className="warnings">{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              )}
              {!explanation && (
                <button onClick={runExplain} disabled={explaining}>
                  <Icon name="sparkle" size={16} /> {explaining ? "Explaining…" : "Explain in plain words"}
                </button>
              )}
              {explanation && <Explanation text={explanation} source={explanationSource} />}
            </>
          )}
        </section>
      </div>
      {p?.voyage_check && <VoyageCheck report={p.voyage_check} />}
      {!plan && manualCheck && (
        <VoyageCheck report={manualCheck} onApply={applySwaps} applying={applying}>
          {approved ? (
            <div className="approved-box">
              <p className="verdict pass"><Icon name="approve" size={20} /> Approved and saved as a voyage.</p>
              <button className="primary small-btn" onClick={() => onOpenVoyage(approved)}>View in Voyages</button>
            </div>
          ) : (
            <div className="presets">
              <button className="primary" onClick={approveManual}
                disabled={!manualCheck.ok || !result?.all_passed || approving || checking}
                title={!manualCheck.ok ? "Fix the problems above first" : !result?.all_passed ? "The condition fails its stability checks" : undefined}>
                {approving && <span className="spinner" aria-hidden="true" />}{approving ? "Approving…" : "Approve this condition"}
              </button>
              <span className="muted small">Approves the tanks as you set them, for the order: {orderParcels?.order.slice(0, 80)}</span>
            </div>
          )}
        </VoyageCheck>
      )}
      {p?.cleaning && <Cleaning key={JSON.stringify(p.cleaning).length + (plan?.parcels.length ?? 0)} report={p.cleaning} />}
    </>
  );
}
