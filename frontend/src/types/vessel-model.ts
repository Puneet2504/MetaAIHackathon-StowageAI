// Vessel model for the product tanker planner (schema v1.0)
// Units: metres, tonnes, m³, t/m³, knots, degrees.
// Axes: x from aft perpendicular (+ forward), y from centreline (+ starboard), z from baseline (+ up).
// Demo rule: populate only with synthetic data for a fictional vessel.

export interface VesselModel {
  id: string;
  schemaVersion: "1.0";
  source: { origin: "synthetic" | "stability_booklet" | "user_drawing"; notes?: string };
  particulars: Particulars;
  lightship: Lightship;
  hydrostatics: HydrostaticRow[];   // one row per draught (and trim, if trim-corrected)
  crossCurves: CrossCurves;          // KN values used to build the GZ curve
  tanks: Tank[];
  cargoSystem: CargoSystem;
  strength: StrengthLimits;
  draughtMarks: DraughtMarks;
  constants: Constant[];             // fixed items, e.g. crew, stores
  stabilityCriteria: CriterionRef[]; // pulled from the ship's own booklet
  layout?: { loa?: number | null };   // user ships: the layout they were built from
}

export interface Particulars {
  name: string;
  vesselType: "product_tanker";
  sizeClass: "MR1" | "MR2" | "LR1" | "LR2" | "Handysize" | "Other";
  lbp: number;
  breadth: number;
  depth: number;
  summerDraught: number;
  deadweight: number;
  serviceSpeedKn: number;
  seawaterDensity: number; // typically 1.025
}

export interface Lightship {
  weight: number;
  lcg: number;
  vcg: number;
  tcg: number;
  distribution: { xAft: number; xFwd: number; weight: number }[]; // for shear/bending
}

export interface HydrostaticRow {
  draught: number;
  trim?: number;         // omit for even-keel tables
  displacement: number;
  lcb: number;
  lcf: number;
  kmt: number;           // transverse metacentre above keel
  mctc: number;          // moment to change trim 1 cm
  tpc: number;           // tonnes per cm immersion
}

export interface CrossCurves {
  heelAnglesDeg: number[];
  rows: { displacement: number; kn: number[] }[]; // kn[i] matches heelAnglesDeg[i]
}

export type TankType =
  | "cargo" | "slop" | "ballast" | "fuel_oil" | "diesel_oil" | "fresh_water" | "lube_oil";

export interface Tank {
  id: string;                 // e.g. "COT3P"
  name: string;
  type: TankType;
  side: "P" | "S" | "C";
  xAft: number;
  xFwd: number;
  capacity100: number;        // m³ at 100 %
  maxFillPct: number;         // operational limit from the vessel's procedures
  coating?: string;           // cargo tanks only
  lineId?: string;            // cargo tanks only
  soundingTable: SoundingRow[];
}

export interface SoundingRow {
  ullage: number;             // or sounding, but be consistent per vessel
  volume: number;
  lcg: number;
  vcg: number;
  tcg: number;
  freeSurfaceInertia: number; // m⁴; engine multiplies by density for the moment
}

export interface CargoSystem {
  pumps: { id: string; ratedM3h: number }[];
  lines: { id: string; pumpId: string; tankIds: string[] }[];
  crossovers: { id: string; lineIds: [string, string]; valveCount: number }[];
  maxSegregations: number;
}

export interface StrengthLimits {
  readingPoints: {
    frame: string;
    x: number;
    sf: { seaMax: number; harbourMax: number };
    bm: { seaHogMax: number; seaSagMax: number; harbourHogMax: number; harbourSagMax: number };
  }[];
}

export interface DraughtMarks {
  forwardX: number;
  midshipX: number;
  aftX: number;
}

export interface Constant {
  name: string;
  weight: number;
  lcg: number;
  vcg: number;
  tcg: number;
}

export interface CriterionRef {
  id: string;             // e.g. "GZ_AREA_0_30"
  description: string;
  requiredValue: number;  // copy from the booklet; never hard-code in the engine
  unit: string;
}
