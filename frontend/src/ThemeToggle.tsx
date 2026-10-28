import Icon from "./Icons";
import type { Theme } from "./theme";

interface Props {
  theme: Theme;
  onToggle: () => void;
  className?: string;
}

// One button: shows the theme you'd switch to.
export default function ThemeToggle({ theme, onToggle, className }: Props) {
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button type="button" className={`theme-toggle ${className ?? ""}`} onClick={onToggle}
      aria-label={`Switch to ${next} mode`} title={`Switch to ${next} mode`}>
      <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
    </button>
  );
}
