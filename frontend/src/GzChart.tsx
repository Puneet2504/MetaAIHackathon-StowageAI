interface Props {
  angles: number[];
  gz: number[];
}

const W = 340, H = 180, L = 36, R = 10, T = 12, B = 28;

export default function GzChart({ angles, gz }: Props) {
  const maxA = angles[angles.length - 1];
  const lo = Math.min(0, ...gz), hi = Math.max(0.5, ...gz);
  const x = (a: number) => L + (a / maxA) * (W - L - R);
  const y = (v: number) => T + ((hi - v) / (hi - lo)) * (H - T - B);
  const pts = angles.map((a, i) => `${x(a)},${y(gz[i])}`).join(" ");
  const ticks = angles.filter((a) => a % 10 === 0);
  return (
    <svg className="gz" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Righting lever (GZ) curve">
      <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} className="gz-axis" />
      <line x1={L} x2={L} y1={T} y2={H - B} className="gz-axis" />
      <line x1={x(30)} x2={W - R} y1={y(0.2)} y2={y(0.2)} className="gz-req" />
      <text x={W - R} y={y(0.2) - 4} textAnchor="end" className="gz-label">GZ 0.20 m from 30°</text>
      <polyline points={pts} className="gz-line" />
      {ticks.map((a) => (
        <text key={a} x={x(a)} y={H - 10} textAnchor="middle" className="gz-label">{a}°</text>
      ))}
      <text x={4} y={T + 8} className="gz-label">{hi.toFixed(1)} m</text>
      <text x={4} y={y(0) + 4} className="gz-label">0</text>
      {lo < 0 && <text x={4} y={H - B} className="gz-label">{lo.toFixed(1)} m</text>}
    </svg>
  );
}
