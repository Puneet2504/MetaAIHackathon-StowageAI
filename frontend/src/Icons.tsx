// Small line icons (24 x 24, stroke = currentColor). Decorative unless a label is given.
const PATHS: Record<string, string> = {
  order: "M7 3h7l5 5v13H7z M14 3v5h5 M10 12h6 M10 16h6",
  allocate: "M4 5h7v6H4z M13 5h7v6h-7z M4 13h7v6H4z M13 13h7v6h-7z",
  gauge: "M4 17a8 8 0 1 1 16 0 M12 17l4-6 M8.5 17h7",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z",
  approve: "M5 12.5l4.5 4.5L19 7.5",
  cross: "M6 6l12 12 M18 6L6 18",
  upload: "M12 16V4 M7 9l5-5 5 5 M4 16v4h16v-4",
  plus: "M12 5v14 M5 12h14",
  logout: "M15 4h4v16h-4 M10 8l-4 4 4 4 M6 12h10",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6",
  ship: "M3 15h18l-2.5 4.5h-13z M6 15V9h5v6 M13 15v-3h5v3 M8 9V6h2",
  anchor: "M12 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M12 7v14 M5 13a7 7 0 0 0 14 0 M8 10h8",
  displacement: "M4 20h16 M7 20V10h10v10 M12 4v6 M9 7l3-3 3 3",
  draught: "M12 3v14 M8 13l4 4 4-4 M3 20c2 0 2-1.5 4.5-1.5S9.5 20 12 20s2-1.5 4.5-1.5S18.5 20 21 20",
  trim: "M3 14l18-4 M6 18h12 M12 10v8",
  list: "M12 4v16 M6 8l6-4 6 4 M5 18c2-2 12-2 14 0",
  stability: "M5 19c4-10 10-12 14-14 M5 19h14 M5 19V5",
  free: "M3 12c3-3 6 3 9 0s6 3 9 0 M3 17c3-3 6 3 9 0s6 3 9 0",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  memory:"M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V4.5A3 3 0 0 0 9 4z M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M8 17h5a3 3 0 0 0 0-6h-2a3 3 0 0 1 0-6h5",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M20 20l-4-4",
  trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13",
  edit: "M4 20h4L19 9l-4-4L4 16z M14 6l4 4",
  calendar: "M4 6h16v14H4z M4 10h16 M8 3v4 M16 3v4",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  eyeOff: "M3 3l18 18 M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4 M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a10 10 0 0 0 5.4-1.6 M9.9 9.9a3 3 0 0 0 4.2 4.2",
};

interface Props {
  name: keyof typeof PATHS | string;
  size?: number;
  label?: string;
  className?: string;
}

export default function Icon({ name, size = 20, label, className }: Props) {
  return (
    <svg className={`icon ${className ?? ""}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"
      role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}
