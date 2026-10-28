// Decorative SVG artwork: the brand mark, the sign-in hero and the ship-card thumbnails.

export function Logo({ size = 34 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <circle cx="20" cy="20" r="19" className="logo-ring" />
      <path d="M7 23h26l-3.5 6.5h-19z" className="logo-hull" />
      <path d="M10 23v-7h6v7 M12 16v-3h2" className="logo-house" />
      <path d="M18 21h13 M21 19v2 M25 19v2 M29 19v2" className="logo-deck" />
      <path d="M5 32c3 0 3-1.6 6-1.6s3 1.6 6 1.6 3-1.6 6-1.6 3 1.6 6 1.6 3-1.6 6-1.6" className="logo-wave" />
    </svg>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className="brand">
      <Logo size={compact ? 30 : 38} />
      <span className="brand-text">
        <span className="brand-name">Stowage AI</span>
        {!compact && <span className="brand-sub">Product tanker planner</span>}
      </span>
    </span>
  );
}

// Side profile of a product tanker riding on animated swell.
export function HeroTanker() {
  const hull = "M40 132 L62 132 L62 150 L540 150 L548 132 L600 128 Q592 176 560 205 L84 205 Q54 200 45 170 Z";
  const domes = [150, 200, 250, 300, 380, 430, 480];
  const wave = (y: number, amp: number) => {
    let d = `M0 ${y}`;
    for (let x = 0; x < 1280; x += 80) d += ` q 20 ${-amp} 40 0 t 40 0`;
    return `${d} L1280 320 L0 320 Z`;
  };
  return (
    <svg className="hero-art" viewBox="0 0 640 300" role="img" aria-label="Illustration of a product tanker at sea">
      <defs>
        <clipPath id="hero-hull-clip"><path d={hull} /></clipPath>
      </defs>
      {/* no sky: the sign-in panel's gradient shows through */}
      <circle cx="520" cy="70" r="34" className="hero-sun" />
      <g className="hero-wave hero-wave-back"><path d={wave(196, 6)} /></g>
      <g className="hero-ship">
        {/* mast, crane and deck piping */}
        <path d="M566 128 V96 M556 104 h20" className="hero-line" />
        <path d="M318 146 V112 L352 124" className="hero-line" />
        <path d="M130 144 H534 M130 140 H534" className="hero-pipe" />
        <path d="M326 150 V138 M334 150 V138 M342 150 V138" className="hero-line" />
        {domes.map((x) => <rect key={x} x={x} y={141} width={14} height={7} rx={2} className="hero-dome" />)}
        {/* accommodation, bridge and funnel */}
        <rect x="64" y="90" width="58" height="42" rx="2" className="hero-house" />
        <rect x="56" y="88" width="74" height="7" rx="1.5" className="hero-house" />
        {[0, 1, 2, 3, 4, 5].map((i) => <rect key={i} x={70 + i * 8.5} y={99} width={5} height={4} className="hero-window" />)}
        {[0, 1, 2, 3, 4, 5].map((i) => <rect key={i} x={70 + i * 8.5} y={111} width={5} height={4} className="hero-window" />)}
        <path d="M76 88 L80 58 H98 L96 88 Z" className="hero-funnel" />
        <path d="M78.6 68 H97.4 L97 74 H77.8 Z" className="hero-funnel-band" />
        {/* hull: topsides, red bottom below the boot-top */}
        <g clipPath="url(#hero-hull-clip)">
          <rect x="0" y="120" width="640" height="66" className="hero-topsides" />
          <rect x="0" y="186" width="640" height="30" className="hero-bottom" />
          <rect x="0" y="183" width="640" height="3" className="hero-boottop" />
        </g>
        <path d={hull} className="hero-hull-line" />
        <text x="470" y="170" className="hero-name">MT DEMO FJORD</text>
      </g>
      <g className="hero-wave hero-wave-mid"><path d={wave(204, 7)} /></g>
      <g className="hero-wave hero-wave-front"><path d={wave(214, 9)} /></g>
    </svg>
  );
}

const PALETTE = ["var(--g-ulsd)", "var(--g-jet)", "var(--g-gasoline)", "var(--g-naphtha)"];

// Plan-view sketch for a ship card: cargo tank pairs drawn as coloured cells.
export function ShipThumb({ cargoTanks, owned }: { cargoTanks: number; owned: boolean }) {
  const pairs = Math.max(1, Math.ceil(cargoTanks / 2));
  const x0 = 46, x1 = 172, w = (x1 - x0) / pairs;
  return (
    <svg className="ship-thumb" viewBox="0 0 220 70" aria-hidden="true">
      <rect width="220" height="70" rx="8" className="thumb-sea" />
      <path d="M18 18 H176 Q206 20 210 35 Q206 50 176 52 H18 Z" className="thumb-hull" />
      <rect x="22" y="22" width="18" height="26" rx="2" className="thumb-house" />
      {Array.from({ length: pairs }, (_, i) => (
        [0, 1].map((row) => (
          <rect key={`${i}-${row}`} x={x0 + i * w + 1} y={row ? 36 : 21} width={w - 2} height={13} rx={2}
            style={{ fill: PALETTE[(i + (owned ? 1 : 0)) % PALETTE.length], opacity: 0.85 - row * 0.15 }} />
        ))
      ))}
    </svg>
  );
}
