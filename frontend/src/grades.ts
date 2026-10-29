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

// Grades an order brings in that are not in the library; shown in a neutral colour.
const extra: Grade[] = [];

// An order's own density wins over an earlier one for the same extra grade.
export const addGrade = (id: string, name: string, density: number) => {
  if (GRADES.some((g) => g.id === id)) return;
  const known = extra.find((g) => g.id === id);
  if (known) known.density = density;
  else extra.push({ id, name, density, color: "var(--g-other)" });
};

// Called on sign-out, so one person's grades don't show up for the next.
export const resetGrades = () => { extra.length = 0; };

export const allGrades = () => GRADES.concat(extra);

export const gradeById = (id: string | null) => allGrades().find((g) => g.id === id);
