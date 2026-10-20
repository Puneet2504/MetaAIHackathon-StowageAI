// Demo grade library. Densities are typical example values; replace with
// the actual density from the cargo documents for any real plan.
export interface Grade {
  id: string;
  name: string;
  density: number; // t/m3 at 15 C, demo value
  color: string;   // CSS variable name
}

export const GRADES: Grade[] = [
  { id: "ULSD", name: "Diesel (ULSD)", density: 0.845, color: "var(--g-ulsd)" },
  { id: "JET", name: "Jet A-1", density: 0.8, color: "var(--g-jet)" },
  { id: "GASOLINE", name: "Gasoline", density: 0.745, color: "var(--g-gasoline)" },
  { id: "NAPHTHA", name: "Naphtha", density: 0.7, color: "var(--g-naphtha)" },
];

export const gradeById = (id: string | null) => GRADES.find((g) => g.id === id);
