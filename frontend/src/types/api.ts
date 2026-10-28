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
}

export interface Parcel {
  grade: string;
  quantity_t: number | null;
  quantity_m3: number | null;
  density_t_m3: number | null;
  load_port: string | null;
  discharge_port: string | null;
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
}

export type MemoryKind = "constraint_empty" | "constraint_fixed" | "max_trim" | "grade_alias" | "note" | "explain_style";

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
