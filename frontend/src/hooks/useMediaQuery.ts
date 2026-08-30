import { useEffect, useState } from "react";

/**
 * Subscribe to a CSS media query from JS.
 *
 * Used sparingly. Layout belongs in CSS, where it costs nothing and works
 * before hydration — this is for the cases where the *component tree* differs
 * rather than its appearance: the transaction table becomes a list of cards on
 * a phone, which is different markup, not different styling. Rendering both and
 * hiding one with CSS would mean every row exists twice in the DOM.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia?.(query).matches ?? false);

  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    // Re-read on subscribe: the query may have changed between the initial
    // state and the effect running.
    setMatches(list.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
