import HullPlan, { type BallastWater, type TankState } from "./HullPlan";
import type { BallastPreview as Preview } from "./types/api";
import type { Tank, VesselModel } from "./types/vessel-model";

// The ship seen from the side and from above, with the ballast water the preview takes in.
// Side profile: each ballast tank (port and starboard as one) filled to its water level, from the sounding table,
// and the waterline before and after ballasting. Heights are drawn larger than lengths so the levels are readable.
interface Props {
  vessel: VesselModel;
  preview: Preview;
  cargo?: Record<string, TankState>;  // the condition's cargo, for the top-down plan
}

const W = 800, H = 260, PAD_X = 24, PAD_TOP = 28, PAD_BOTTOM = 20, HOUSE = 9;
const LABEL_MIN_W = 44;

// Height of the liquid above the tank bottom for a volume, from the ullage in the sounding table.
function level(t: Tank, m3: number): { bottom: number; height: number; liquid: number } {
  const rows = [...t.soundingTable].sort((a, b) => a.volume - b.volume);
  const bottom = rows[0].vcg, height = rows[0].ullage;
  let ullage = rows[rows.length - 1].ullage;
  for (let i = 1; i < rows.length; i++) {
    if (m3 <= rows[i].volume) {
      const a = rows[i - 1], b = rows[i];
      ullage = a.ullage + ((m3 - a.volume) / (b.volume - a.volume || 1)) * (b.ullage - a.ullage);
      break;
    }
  }
  return { bottom, height, liquid: Math.max(0, Math.min(height, height - ullage)) };
}

const pair = (t: Tank) => (t.side === "C" ? t.id : t.id.replace(/[PS]$/, ""));
const trim = (v: number) => `${Math.abs(v).toFixed(2)} m by the ${v >= 0 ? "stern" : "head"}`;

export default function BallastLayout({ vessel, preview, cargo }: Props) {
  const { lbp, depth } = vessel.particulars;
  const loa = vessel.layout?.loa;
  const water = Object.fromEntries(preview.tanks.map((r) => [r.tank, r]));

  // Ballast tanks merged per port/starboard pair, with the water level of the pair.
  const groups = new Map<string, Tank[]>();
  vessel.tanks.filter((t) => t.type === "ballast").forEach((t) => groups.set(pair(t), [...(groups.get(pair(t)) ?? []), t]));
  const ballast = [...groups.entries()].map(([id, ts]) => {
    const lv = ts.map((t) => level(t, water[t.id]?.m3 ?? 0));
    const t = ts.reduce((s, x) => s + (water[x.id]?.t ?? 0), 0);
    const cap = ts.reduce((s, x) => s + x.capacity100, 0);
    const m3 = ts.reduce((s, x) => s + (water[x.id]?.m3 ?? 0), 0);
    return { id, ts, xAft: ts[0].xAft, xFwd: ts[0].xFwd, bottom: lv[0].bottom, height: lv[0].height,
      liquid: lv.reduce((s, l) => s + l.liquid, 0) / lv.length, t, pct: Math.round((100 * m3) / cap) };
  });
  const cargoTanks = vessel.tanks.filter((t) => (t.type === "cargo" || t.type === "slop") && t.side !== "S");

  // Length span as on the top-down plan: stern to bow tip, with the overhangs.
  const shaped = vessel.tanks.filter((t) => ["cargo", "slop", "ballast"].includes(t.type));
  const overhang = loa && loa > lbp ? (loa - lbp) / 2 : lbp * 0.03;
  const xs = Math.min(0, ...shaped.map((t) => t.xAft)) - overhang;
  const xe = Math.max(lbp, ...shaped.map((t) => t.xFwd)) + overhang;
  const top = depth + HOUSE, low = -1;
  const sx = (x: number) => PAD_X + ((x - xs) / (xe - xs)) * (W - 2 * PAD_X);
  const sy = (z: number) => PAD_TOP + ((top - z) / (top - low)) * (H - PAD_TOP - PAD_BOTTOM);
  const span = xe - xs;

  // Hull: flat keel, a rounded stern rising to the transom, a raked bow with a little sheer.
  const sheer = depth * 0.08;
  const keelAft = xs + span * 0.07, keelFwd = xe - span * 0.06;
  const hull = [
    `M ${sx(xs)} ${sy(depth)}`,
    `L ${sx(xe)} ${sy(depth + sheer)}`,
    `L ${sx(xe - span * 0.025)} ${sy(depth * 0.35)}`,
    `Q ${sx(xe - span * 0.01)} ${sy(depth * 0.05)} ${sx(keelFwd)} ${sy(0)}`,
    `L ${sx(keelAft)} ${sy(0)}`,
    `Q ${sx(xs + span * 0.01)} ${sy(0)} ${sx(xs + span * 0.01)} ${sy(depth * 0.45)}`,
    `L ${sx(xs)} ${sy(depth * 0.5)}`,
    "Z",
  ].join(" ");
  const firstTank = Math.min(...shaped.map((t) => t.xAft));
  const houseAft = xs + span * 0.02, houseFwd = Math.max(houseAft + span * 0.04, firstTank - span * 0.01);

  // Waterlines: straight between the draughts at the aft and forward perpendiculars, carried to the ends of the drawing.
  const wl = (aft: number, fwd: number) => (x: number) => aft + ((fwd - aft) * x) / lbp;
  const after = preview.draught_aft_m != null && preview.draught_fwd_m != null ? wl(preview.draught_aft_m, preview.draught_fwd_m) : null;
  const before = preview.draught_aft_before != null && preview.draught_fwd_before != null
    ? wl(preview.draught_aft_before, preview.draught_fwd_before) : null;
  const x0 = xs - (PAD_X / (W - 2 * PAD_X)) * span, x1 = xe + (PAD_X / (W - 2 * PAD_X)) * span;

  const plan: Record<string, BallastWater> = Object.fromEntries(preview.tanks.map((r) => [r.tank, { pct: r.pct, t: r.t }]));

  const draughts = (fwd?: number, aft?: number) => (fwd != null && aft != null ? ` (${fwd.toFixed(2)} m fwd / ${aft.toFixed(2)} m aft)` : "");

  return (
    <div className="ballast-layout">
      <div className="profile-wrap">
        <svg className="profile" viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`Side view of ${vessel.particulars.name} with the ballast water to take in, bow to the right`}>
          <rect width={W} height={H} rx={10} className="profile-sky" />
          {after && (
            <path className="profile-sea" d={`M 0 ${sy(after(x0))} L ${W} ${sy(after(x1))} L ${W} ${H} L 0 ${H} Z`} />
          )}
          <path d={hull} className="profile-hull" />
          <rect x={sx(houseAft)} y={sy(depth + HOUSE)} width={sx(houseFwd) - sx(houseAft)} height={sy(depth) - sy(depth + HOUSE)}
            rx={3} className="hull-house" />
          {cargoTanks.map((t) => {
            const lv = level(t, 0);
            return <rect key={t.id} x={sx(t.xAft)} y={sy(lv.bottom + lv.height)} width={sx(t.xFwd) - sx(t.xAft)}
              height={sy(lv.bottom) - sy(lv.bottom + lv.height)} className="profile-cargo"><title>{pair(t)} · cargo</title></rect>;
          })}
          {ballast.map((b) => {
            const x = sx(b.xAft), w = sx(b.xFwd) - sx(b.xAft);
            const yTop = sy(b.bottom + b.height), yBot = sy(b.bottom);
            const yWater = sy(b.bottom + b.liquid);
            const used = b.t > 0;
            const onWater = (dy: number) => (yWater <= yTop + dy - 12 ? "on-water" : "");
            return (
              <g key={b.id} className={`profile-ballast ${used ? "takes-water" : ""}`}>
                <title>{`${b.id}${b.ts.length > 1 ? " (port + starboard)" : ""} · ${used ? `${b.t.toLocaleString()} t · ${b.pct}% full` : "no water"}`}</title>
                <rect x={x + 1} y={yTop} width={Math.max(w - 2, 0)} height={yBot - yTop} rx={2} className="profile-tank" />
                {used && <rect x={x + 1} y={yWater} width={Math.max(w - 2, 0)} height={yBot - yWater} rx={2} className="tank-water" />}
                {used && w >= LABEL_MIN_W && (
                  <>
                    <text x={x + w / 2} y={yTop + 18} className={`profile-label strong ${onWater(18)}`}>{b.id}</text>
                    <text x={x + w / 2} y={yTop + 35} className={`profile-label ${onWater(35)}`}>{`${b.t.toLocaleString()} t`}</text>
                    <text x={x + w / 2} y={yTop + 51} className={`profile-label ${onWater(51)}`}>{`${b.pct}%`}</text>
                  </>
                )}
              </g>
            );
          })}
          {before && (
            <line x1={0} y1={sy(before(x0))} x2={W} y2={sy(before(x1))} className="waterline before" />
          )}
          {after && <line x1={0} y1={sy(after(x0))} x2={W} y2={sy(after(x1))} className="waterline after" />}
          <text x={W - PAD_X / 2} y={PAD_TOP - 12} className="axis-note" textAnchor="end">Bow →</text>
        </svg>
      </div>
      <ul className="legend small">
        <li><span className="key-line before" />Waterline now: {trim(preview.trim_before)}{draughts(preview.draught_fwd_before, preview.draught_aft_before)}</li>
        <li><span className="key-line after" />After ballasting: {trim(preview.trim_after)}{draughts(preview.draught_fwd_m, preview.draught_aft_m)}</li>
        <li><span className="swatch water" />Ballast water to take in</li>
      </ul>
      <div className="plan">
        <HullPlan vessel={vessel} state={cargo ?? {}} selected={null} onSelect={() => {}} ballast={plan} />
      </div>
    </div>
  );
}
