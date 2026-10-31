export interface TankLoad {
  tank_id: string;
  volume_m3: number;
  density_t_m3: number;
  grade?: string | null;
}

export interface CriterionResult {
  id: string;
  description: string;
  required: number;
  actual: number;
  unit: string;
  passed: boolean;
  limit?: "min" | "max";  // "max": the actual value must not exceed the required one (hull strength)
}

// Still-water shear force and bending moment at one reading point along the hull.
export interface StrengthPoint {
  frame: string;
  x: number;
  sf_t: number;
  bm_tm: number;          // > 0 hogging, < 0 sagging
  mode: "hog" | "sag";
  sf_pct: number;         // % of the sea limit
  bm_pct: number;
}

export interface StabilityResult {
  displacement_t: number;
  deadweight_t: number;
  kg_m: number;
  lcg_m: number;
  tcg_m: number;
  free_surface_correction_m: number;
  gm_solid_m: number;
  gm_fluid_m: number;
  draught_mean_m: number;
  draught_fwd_m: number;
  draught_aft_m: number;
  trim_m: number;
  list_deg: number | null;
  gz_curve: { anglesDeg: number[]; gzM: number[] };
  criteria: CriterionResult[];
  warnings: string[];
  all_passed: boolean;
  max_sf_pct?: number | null;   // peak shear force, % of the safe sea limit
  max_bm_pct?: number | null;   // peak bending moment, % of the safe sea limit
  strength?: StrengthPoint[] | null;
  ballast?: BallastPreview | null;  // manual checks: how to bring the trim back to normal
}

export interface Parcel {
  grade: string;
  quantity_t: number | null;
  quantity_m3: number | null;
  density_t_m3: number | null;
  quantity_bbl?: number | null;
  mass_basis?: "air" | "vacuum" | null;
  density_temp_c?: number | null;
  api?: number | null;
  temp_c?: number | null;        // cargo temperature at loading
  load_port: string | null;
  discharge_port: string | null;
}

// How an ordered quantity became tank volume (backend app/stowage/quantities.py).
export interface Conversion {
  ordered: string;
  basis: "air" | "vacuum";
  temp_c: number;
  temp_source: string;      // "order", "<port> average" or "typical for the grade"
  density15: number;
  density_source: string;   // starts with "order", or "typical for the grade"
  density_at_temp: number;
  vcf: number;
  volume15_m3: number;
  volume_m3: number;        // room taken in the tanks at cargo temperature
  tonnes: number;
  tonnes_air: number;
  tonnes_vacuum: number;
  assumed: string[];
}

export interface VesselSummary {
  id: string;
  name: string;
  sizeClass: string;
  origin: string;
  owned: boolean;
  cargoTanks: number;
}

export type TankKind = "cargo" | "slop" | "ballast" | "fuel_oil" | "diesel_oil" | "fresh_water";
export type SizeClass = "MR1" | "MR2" | "LR1" | "LR2" | "Handysize" | "Other";

export interface LayoutTank {
  id: string;
  name?: string | null;
  type: TankKind;
  side: "P" | "S" | "C";
  xAft: number;
  xFwd: number;
  capacity100?: number | null;
  coating?: string | null;
  lineId?: string | null;
  // Optional geometry overrides kept when a layout is edited
  yIn?: number | null;
  yOut?: number | null;
  zBottom?: number | null;
  zTop?: number | null;
  shapeFactor?: number | null;
  maxFillPct?: number | null;
}

export interface VesselLayout {
  name: string;
  sizeClass: SizeClass;
  lbp: number;
  breadth: number;
  depth: number;
  summerDraught: number;
  doubleBottom?: number | null;
  doubleSide?: number | null;
  loa?: number | null;
  tanks: LayoutTank[];
}

// What the AI returns, or a layout being edited: anything may still be missing.
export type DraftTank = { [K in keyof LayoutTank]?: LayoutTank[K] | null };
export type DraftLayout = { [K in keyof Omit<VesselLayout, "tanks">]?: VesselLayout[K] | null } & { tanks?: DraftTank[] | null };

export interface Segment {
  label: string;
  box: [number, number, number, number]; // normalised 0 to 1
  score?: number | null;
}

// One automatic correction made to what was read from a drawing (undoable in the editor).
export interface DrawingFix {
  tank: string | null;     // tank id the fix applies to (null = ship particulars)
  field: string;           // "lbp", "id", "capacity100", "xFwd", "xAft/xFwd", "side", ...
  old: unknown;
  new: unknown;
  reason: string;
}

export interface ReadDrawingOut {
  layout: DraftLayout;
  notes: string[];
  segments: Segment[];
  checks: string[];        // rule-based checks; `notes` are the AI's own
  fixes: DrawingFix[];
  estimated: string[];     // fields filled in by estimate, e.g. "lbp"
}

export interface StowageConstraints {
  empty_tanks: string[];
  fixed_grades: Record<string, string>;
  max_trim_m: number | null;
  fill?: "even" | "full" | null; // "even": each grade spread over all its tanks at one level
}

export interface Allocation {
  parcel: number;
  grade: string;
  grade_id: string;
  density_t_m3: number;
  requested_m3: number;
  allocated_m3: number;
  unallocated_m3: number;
  lines: string[];
  tanks: string[];
  tonnes?: number;          // placed, in the order's basis (older voyages lack it)
  conversion?: Conversion;
}

// A tank that changes grade, and what it needs first (backend app/stowage/cleaning.py).
export interface TankCleaning {
  tank: string;
  prev: string;
  prev_name: string;
  next: string;
  next_name: string;
  source: string;
  level: 0 | 1 | 2 | 3;
  key: "none" | "drain" | "wash" | "full_wash";
  label: string;
  reason: string;
  steps: string[];
  checks: string[];
}

export interface CleaningEstimate {
  hours: number;      // time at the port for cleaning (typical estimate)
  cost_usd: number;
  tanks?: number;
}

export interface CleaningReport {
  ports: { port: string; when: string; source: string; changes: TankCleaning[]; checks: string[]; estimate?: CleaningEstimate }[];
  last_voyage: { id: string; date: string; last_port: string | null } | null;
  tanks_to_clean: number;
  total?: CleaningEstimate;
  // Cleaning saved by choosing the first port's tanks with a later port's cargo in mind.
  saving?: (CleaningEstimate & { tanks: number; port: string; compared_to: string }) | null;
}

// Open space on a ship's current voyage (backend app/stowage/open_space.py and the AI task open_space).
export interface SpacePort {
  port: string;
  later_ports: string[];
  on_board: string[];
  grades_on_board: string[];
  empty: { tanks: string; line: string | null; m3: number; last: string | null }[];
  free_lines: string[];
}

export interface OpenSpaceShip {
  vessel_id: string;
  vessel_name: string | null;
  size_class: string | null;
  voyage: null | {
    id: string;
    approved_at: string;
    order_text: string | null;
    route: { load_ports: string[]; discharge_ports: string[] };
    facts: { ship: string; max_grades: number; lines: Record<string, string>; ports: SpacePort[]; grades: Record<string, number> };
  };
}

export interface SpaceOffer {
  port: string;
  discharge_port: string;
  grade: string;
  tanks: string[];
  quantity_m3: number;
  quantity_t: number;
  cleaning: string;
  reason: string;
  checks: string[];          // what the AI checked
  order_line: string;        // e.g. "Load Le Havre: 9,800 mt Jet A-1 for Bilbao."
  check: { verified: boolean; label: string; why: string };  // safety label from the voyage check
}

export interface SpaceAnswer extends OpenSpaceShip {
  ai: { summary: string; suggestions: SpaceOffer[] } | null;
  message: string | null;
}

// The voyage check (backend app/stowage/voyage_check.py): pipelines and the ship after every port.
export interface VoyageIssue {
  code: string;            // LINE_SHARED, PUMP_SHARED, CROSSOVER, DISCHARGE_BLOCKED, PORT_CONDITION, GRADE_AMBIGUOUS, ...
  severity: "block" | "warn" | "note";
  port: string;
  message: string;
  tanks: string[];
  lines: string[];
}

export interface PortCondition {
  port: string;
  kind: "load" | "discharge" | "reload";
  aboard: string[];
  passed: boolean | null;  // null: the ship leaves empty (in ballast)
  gm_m?: number;
  trim_m?: number;
  max_sf_pct?: number | null;
  max_bm_pct?: number | null;
  failed: string[];
  ballast?: BallastPreview | null;
}

export interface SwapSuggestion {
  moves: { kind: "move" | "swap"; grade: string; from: string; to: string }[];
  resolves: string[];
  loads: TankLoad[];       // the condition after the swaps
  after: { ok: boolean; ports: { port: string; passed: boolean | null; max_sf_pct?: number | null; max_bm_pct?: number | null; trim_m?: number }[] };
}

export interface VoyageCheckReport {
  ok: boolean;
  ports: PortCondition[];
  issues: VoyageIssue[];
  suggestion: SwapSuggestion | null;
}

// A later port where emptied tanks are reloaded: the cargo loaded there and the condition on leaving.
export interface PlanStage {
  port: string;
  discharged: string[];
  status: string;
  message: string;
  allocation: Allocation[];
  loads: TankLoad[];
  result: StabilityResult | null;
  warnings: string[];
}

// Ballast that brings the trim back to slightly by the stern (backend app/stowage/ballast.py). A preview only.
export interface BallastPreview {
  tanks: { tank: string; m3: number; t: number; pct: number }[];
  total_t: number;
  trim_before: number;
  trim_after: number;
  draught_fwd_m?: number;
  draught_aft_m?: number;
  gm_m?: number;
  max_stress_pct?: number | null;
  passed?: boolean;
  reached: boolean;
  note: string | null;
}

// Cargo added to an approved voyage from Open space.
export interface Amendment {
  at: string;
  by: string;
  line: string;
  port: string;
  tanks: string[];
  grade: string;
  quantity_t: number;
}

export interface StowagePlan {
  status: "planned" | "partial" | "fails_criteria" | "cannot_segregate" | "nothing_to_plan";
  message: string;
  loads: TankLoad[];
  allocation: Allocation[];
  result: StabilityResult | null;
  candidates_checked: number;
  candidates_total: number;
  warnings: string[];
  stages?: PlanStage[];
  cleaning?: CleaningReport;
  voyage_check?: VoyageCheckReport;
  manual?: boolean;
  amendments?: Amendment[];        // a hand-edited condition approved after passing the voyage check
}

export type MemoryKind = "constraint_empty" | "constraint_fixed" | "max_trim" | "fill_mode" | "grade_alias" | "note" | "explain_style";

export interface MemoryItem {
  id: string;
  scope: string;          // "global" or a vessel id
  scope_name?: string;    // "All ships" or the ship's name (from GET /memory)
  kind: MemoryKind;
  key: string | null;
  value: string | number | null;
  text: string;
  source: string;
  created_at: string;
  updated_at: string;
  uses: number;
}

export interface PlanResponse {
  parcels: Parcel[];
  parcel_notes: string[];
  constraints: StowageConstraints;          // this plan's own constraints
  applied_constraints: StowageConstraints;  // the above plus remembered ones
  memory_used: MemoryItem[];
  remembered: MemoryItem[];
  forgotten: MemoryItem[];
  plan: StowagePlan;
  explanation: string | null;
  explanation_source: "ai" | "plain";
}

export interface VoyageSummary {
  load_ports: string[];
  discharge_ports: string[];
  grades: { grade: string; grade_id: string; tonnes: number }[];
  total_t: number;
  gm_m: number | null;
  trim_m: number | null;
  passed: boolean;
  status: string | null;
  tanks_to_clean?: number;
  cleaning_hours?: number;
}

export interface VoyageListItem {
  id: string;
  vessel_id: string;
  vessel_name: string | null;
  size_class: string | null;
  approved_at: string;
  order_text: string | null;
  voyage: VoyageSummary;
}

export interface VoyageDetail extends VoyageListItem {
  parcels: Parcel[];
  constraints: StowageConstraints;
  plan: StowagePlan;
  explanation: string | null;
  vessel: import("./vessel-model").VesselModel | null;
}

export interface UsageDay {
  date: string;
  prompt_tokens: number;
  completion_tokens: number;
  calls: number;
  cache_hits: number;
  skipped: number;
}

export interface UsageSummary {
  today: UsageDay;
  days: UsageDay[];
  total: Omit<UsageDay, "date">;
}

export interface ApprovedPlanSummary {
  id: string;
  vessel_id: string;
  approved_at: string;
  order_text: string | null;
  status: string;
  grades: string[];
}
