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
