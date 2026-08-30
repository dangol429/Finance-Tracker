import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "finance_tracker_theme";

/**
 * Read the initial theme without waiting for React.
 *
 * Exported and called from `main.tsx` *before* the first render, because a
 * theme applied in an effect arrives one paint too late: the page renders dark,
 * then flips to light. That flash is the reason this is not simply a piece of
 * component state.
 */
export function resolveInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    /* storage unavailable — fall through to the OS preference */
  }
  // No explicit choice means "whatever the OS says", which for this app's
  // audience is usually dark. Defaulting to dark regardless would override a
  // deliberate system setting, which is exactly the thing `prefers-color-scheme`
  // exists to stop apps doing.
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(() => {
    const current = document.documentElement.dataset.theme;
    return current === "light" ? "light" : "dark";
  });

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}
