import { useEffect, useState } from "react";

// Light or dark. The choice is saved per browser; until one is made, the OS setting is followed.
// index.html applies the same rule before the first paint, so the page never flashes the wrong theme.
export type Theme = "light" | "dark";

const KEY = "tanker-planner-theme";
const media = () => window.matchMedia?.("(prefers-color-scheme: dark)");

function saved(): Theme | null {
  try { const t = localStorage.getItem(KEY); return t === "light" || t === "dark" ? t : null; } catch { return null; }
}

const system = (): Theme => (media()?.matches ? "dark" : "light");

function apply(t: Theme) {
  document.documentElement.dataset.theme = t;
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => saved() ?? system());

  useEffect(() => { apply(theme); }, [theme]);

  // Follow OS changes until the person picks a theme themselves.
  useEffect(() => {
    const m = media();
    if (!m) return;
    const onChange = () => { if (!saved()) setTheme(system()); };
    m.addEventListener("change", onChange);
    return () => m.removeEventListener("change", onChange);
  }, []);

  const toggle = () => setTheme((t) => {
    const next: Theme = t === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch { /* private mode: still switches for this visit */ }
    return next;
  });
  return [theme, toggle];
}
