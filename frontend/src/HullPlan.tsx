import { useId } from "react";
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

const W = 1000, H = 230, PAD_X = 30, PAD_Y = 30;
const LABEL_MIN_W = 34, LABEL_MIN_H = 22, FILL_MIN_H = 40;

// Plan view, bow to the right, starboard at the bottom.
// The hull is shaped around the ship's own tanks: the parallel body covers the cargo block,
// the bow and stern taper only beyond it, and tanks are clipped to the outline.
export default function HullPlan({ vessel, state, selected, onSelect }: Props) {
  const clipId = `hull-clip-${useId().replace(/:/g, "")}`;
  const { lbp, breadth } = vessel.particulars;
  const loa = vessel.layout?.loa;
  const drawn = vessel.tanks.filter((t) => ["cargo", "slop", "ballast"].includes(t.type) && t.side !== "C");

  // Length span: from the stern to the bow tip, including the overhangs past the perpendiculars.
  const overhang = loa && loa > lbp ? (loa - lbp) / 2 : lbp * 0.03;
  const aftMost = Math.min(0, ...drawn.map((t) => t.xAft));
  const fwdMost = Math.max(lbp, ...drawn.map((t) => t.xFwd));
  const xs = aftMost - overhang, xe = fwdMost + overhang;
  const sx = (x: number) => PAD_X + ((x - xs) / (xe - xs)) * (W - 2 * PAD_X);
  const sy = (y: number) => H / 2 + (y / (breadth / 2)) * (H / 2 - PAD_Y);

  // Parallel body: from the aft end of the first tank to the forward end of the cargo block.
  const cargo = drawn.filter((t) => t.type === "cargo");
  const bodyAft = Math.min(lbp * 0.15, ...(drawn.length ? drawn.map((t) => t.xAft) : [lbp * 0.15]));
  const sternStart = Math.max(xs + (xe - xs) * 0.04, bodyAft);
  const cargoFwd = cargo.length ? Math.max(...cargo.map((t) => t.xFwd)) : lbp * 0.86;
  const bowStart = Math.min(Math.max(lbp * 0.8, cargoFwd - (xe - xs) * 0.01), xe - (xe - xs) * 0.08);
  const half = breadth / 2;
  const hull = [
    `M ${sx(xs)} ${sy(-half * 0.85)}`,
    `L ${sx(sternStart)} ${sy(-half)}`,
    `L ${sx(bowStart)} ${sy(-half)}`,
    `Q ${sx(xe - (xe - xs) * 0.01)} ${sy(-half)} ${sx(xe)} ${sy(0)}`,
    `Q ${sx(xe - (xe - xs) * 0.01)} ${sy(half)} ${sx(bowStart)} ${sy(half)}`,
    `L ${sx(sternStart)} ${sy(half)}`,
    `L ${sx(xs)} ${sy(half * 0.85)}`,
    "Z",
  ].join(" ");

  // Accommodation over the engine room, only where no tank is.
  const firstTank = drawn.length ? Math.min(...drawn.map((t) => t.xAft)) : lbp * 0.15;
  const houseAft = xs + (xe - xs) * 0.015, houseFwd = firstTank - (xe - xs) * 0.008;
  const showHouse = houseFwd - houseAft >= (xe - xs) * 0.04;

  const tcgOf = (t: Tank) => t.soundingTable[t.soundingTable.length - 1].tcg;
  const yRange = (t: Tank): [number, number] => {
    const tcg = tcgOf(t);
    if (t.type === "ballast" && t.side !== "C") return [Math.abs(tcg) - 1, Math.abs(tcg) + 1].map((v) => v * Math.sign(tcg)) as [number, number];
    return tcg >= 0 ? [0, 2 * tcg] : [2 * tcg, 0];
  };

  const tipX = sx(xe);
  const waveLen = Math.max(0, Math.min(70, W - tipX - 4));

  return (
    <svg className="hull" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`Tank plan of ${vessel.particulars.name}, bow to the right`}>
      <defs>
        <linearGradient id="hull-sea" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" className="sea-top" />
          <stop offset="1" className="sea-bottom" />
        </linearGradient>
        <filter id="hull-shadow" x="-5%" y="-20%" width="110%" height="140%">
          <feDropShadow dx="0" dy="4" stdDeviation="5" className="hull-shadow" />
        </filter>
        <clipPath id={clipId}><path d={hull} /></clipPath>
      </defs>
      <rect width={W} height={H} rx={10} fill="url(#hull-sea)" />
      {[0.22, 0.5, 0.78].map((f) => (
        <path key={f} className="sea-line" d={`M0 ${H * f} q 25 -4 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0`} />
      ))}
      {waveLen > 10 && (
        <path d={`M ${tipX} ${sy(0)} q ${waveLen * 0.43} -18 ${waveLen} -26 M ${tipX} ${sy(0)} q ${waveLen * 0.43} 18 ${waveLen} 26`}
          className="bow-wave" />
      )}
      <path d={hull} className="hull-deck" filter="url(#hull-shadow)" />
      {showHouse && (
        <rect x={sx(houseAft)} y={sy(-half * 0.62)} width={sx(houseFwd) - sx(houseAft)}
          height={sy(half * 0.62) - sy(-half * 0.62)} rx={4} className="hull-house" />
      )}
      <line x1={sx(sternStart)} x2={sx(bowStart)} y1={sy(0)} y2={sy(0)} className="hull-centreline" />
      <g clipPath={`url(#${clipId})`}>
        {drawn.map((t) => {
          const [y0, y1] = yRange(t);
          const x = sx(t.xAft), w = sx(t.xFwd) - sx(t.xAft);
          const y = sy(Math.min(y0, y1)), h = Math.abs(sy(y1) - sy(y0));
          const s = state[t.id];
          const grade = gradeById(s?.grade ?? null);
          const isCargo = t.type !== "ballast";
          const fill = s?.fillPct ?? 0;
          const showId = isCargo && w >= LABEL_MIN_W && h >= LABEL_MIN_H;
          const showFill = showId && h >= FILL_MIN_H;
          const font = Math.max(10, Math.min(13, w / 5.2));
          const onFill = grade && fill >= 60 ? "on-fill" : "";
          return (
            <g key={t.id} className={`tank ${isCargo ? "cargo" : "ballast"} ${selected === t.id ? "selected" : ""}`}
              onClick={() => isCargo && onSelect(t.id)}
              role={isCargo ? "button" : undefined}
              tabIndex={isCargo ? 0 : undefined}
              onKeyDown={(e) => { if (isCargo && (e.key === "Enter" || e.key === " ")) onSelect(t.id); }}
              aria-label={isCargo ? `${t.name}, ${grade ? grade.name : "empty"}, ${fill}% full` : undefined}>
              <title>{`${t.id} · ${t.type}${isCargo ? ` · ${grade ? `${grade.name} ${fill}%` : "empty"}` : ""}`}</title>
              <rect x={x + 1.5} y={y + 1.5} width={Math.max(w - 3, 0)} height={Math.max(h - 3, 0)} rx={isCargo ? 4 : 2} className="tank-shell" />
              {grade && fill > 0 && (() => {
                // Liquid level: the filled part rises from the tank's outboard side.
                const lh = (h - 3) * Math.min(fill, 100) / 100;
                const ly = t.side === "S" ? y + h - 1.5 - lh : y + 1.5;
                return (
                  <g className="tank-liquid">
                    <rect x={x + 1.5} y={y + 1.5} width={Math.max(w - 3, 0)} height={Math.max(h - 3, 0)} rx={4} style={{ fill: grade.color, opacity: 0.18 }} />
                    <rect x={x + 1.5} y={ly} width={Math.max(w - 3, 0)} height={lh} rx={4} style={{ fill: grade.color }} />
                  </g>
                );
              })()}
              {showId && (
                <text x={x + w / 2} y={y + h / 2 + (showFill ? -6 : font / 3)} className={`tank-id ${onFill}`}
                  style={{ fontSize: font }}>{t.id}</text>
              )}
              {showFill && (
                <text x={x + w / 2} y={y + h / 2 + 12} className={`tank-fill ${onFill}`} style={{ fontSize: font - 1 }}>
                  {fill > 0 ? `${fill}%` : "empty"}
                </text>
              )}
            </g>
          );
        })}
      </g>
      <text x={Math.min(tipX, W - PAD_X / 2) - 4} y={PAD_Y - 12} className="axis-note" textAnchor="end">Bow →</text>
      <text x={PAD_X} y={PAD_Y - 12} className="axis-note">Port</text>
      <text x={PAD_X} y={H - 8} className="axis-note">Starboard</text>
    </svg>
  );
}
