import type { Tank, VesselModel } from "./types/vessel-model";
import { gradeById } from "./grades";

export interface TankState {
  grade: string | null;
  fillPct: number;
}

interface Props {
  vessel: VesselModel;
  state: Record<string, TankState>;
  selected: string | null;
  onSelect: (id: string) => void;
}

const W = 1000, H = 230, PAD_X = 40, PAD_Y = 30;

// Plan view, bow to the right, starboard at the bottom.
export default function HullPlan({ vessel, state, selected, onSelect }: Props) {
  const { lbp, breadth } = vessel.particulars;
  const sx = (x: number) => PAD_X + (x / lbp) * (W - 2 * PAD_X);
  const sy = (y: number) => H / 2 + (y / (breadth / 2)) * (H / 2 - PAD_Y);

  const bowStart = lbp * 0.86;
  const hull = [
    `M ${sx(0)} ${sy(-breadth / 2 * 0.85)}`,
    `L ${sx(lbp * 0.15)} ${sy(-breadth / 2)}`,
    `L ${sx(bowStart)} ${sy(-breadth / 2)}`,
    `Q ${sx(lbp * 0.99)} ${sy(-breadth / 2)} ${sx(lbp)} ${sy(0)}`,
    `Q ${sx(lbp * 0.99)} ${sy(breadth / 2)} ${sx(bowStart)} ${sy(breadth / 2)}`,
    `L ${sx(lbp * 0.15)} ${sy(breadth / 2)}`,
    `L ${sx(0)} ${sy(breadth / 2 * 0.85)}`,
    "Z",
  ].join(" ");

  const tcgOf = (t: Tank) => t.soundingTable[t.soundingTable.length - 1].tcg;
  const yRange = (t: Tank): [number, number] => {
    const tcg = tcgOf(t);
    if (t.type === "ballast" && t.side !== "C") return [Math.abs(tcg) - 1, Math.abs(tcg) + 1].map((v) => v * Math.sign(tcg)) as [number, number];
    return tcg >= 0 ? [0, 2 * tcg] : [2 * tcg, 0];
  };

  const drawn = vessel.tanks.filter((t) => ["cargo", "slop", "ballast"].includes(t.type) && t.side !== "C");

  return (
    <svg className="hull" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`Tank plan of ${vessel.particulars.name}, bow to the right`}>
      <path d={hull} className="hull-outline" />
      {drawn.map((t) => {
        const [y0, y1] = yRange(t);
        const x = sx(t.xAft), w = sx(t.xFwd) - sx(t.xAft);
        const y = sy(Math.min(y0, y1)), h = Math.abs(sy(y1) - sy(y0));
        const s = state[t.id];
        const grade = gradeById(s?.grade ?? null);
        const isCargo = t.type !== "ballast";
        const fill = s?.fillPct ?? 0;
        return (
          <g key={t.id} className={`tank ${isCargo ? "cargo" : "ballast"} ${selected === t.id ? "selected" : ""}`}
            onClick={() => isCargo && onSelect(t.id)}
            role={isCargo ? "button" : undefined}
            tabIndex={isCargo ? 0 : undefined}
            onKeyDown={(e) => { if (isCargo && (e.key === "Enter" || e.key === " ")) onSelect(t.id); }}
            aria-label={isCargo ? `${t.name}, ${grade ? grade.name : "empty"}, ${fill}% full` : undefined}>
            <rect x={x + 1} y={y + 1} width={w - 2} height={h - 2} className="tank-shell" />
            {grade && fill > 0 && (
              <rect x={x + 1} y={y + 1} width={w - 2} height={h - 2}
                style={{ fill: grade.color, opacity: 0.35 + 0.65 * (fill / 100) }} />
            )}
            {isCargo && w > 30 && (
              <>
                <text x={x + w / 2} y={y + h / 2 - 6} className="tank-id">{t.id}</text>
                <text x={x + w / 2} y={y + h / 2 + 12} className="tank-fill">{fill > 0 ? `${fill}%` : "empty"}</text>
              </>
            )}
          </g>
        );
      })}
      <text x={sx(lbp) - 4} y={PAD_Y - 10} className="axis-note" textAnchor="end">Bow</text>
      <text x={sx(0) + 4} y={H - 8} className="axis-note">Starboard side at the bottom</text>
    </svg>
  );
}
