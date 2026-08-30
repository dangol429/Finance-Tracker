import { useEffect, useState } from "react";

/**
 * A value that lags behind its input until the input stops changing.
 *
 * Used for the search box. Without it, every keystroke is a request: typing
 * "groceries" fires nine, eight of which are obsolete before they land, and the
 * results flicker through prefixes as they race each other back.
 *
 * **Debounce, not throttle.** Throttling would send a request every 300ms
 * *during* typing, which is the wrong shape — nobody wants results for "groc".
 * Debouncing waits for a pause, which is the actual signal that the user has
 * finished expressing what they want.
 *
 * The input stays immediately responsive because the *input element* is
 * controlled by the raw value; only the query is delayed. Debouncing the input
 * itself is the classic mistake — it makes typing feel broken.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    // Clearing on every change is what makes this a debounce rather than a
    // queue of timers that each eventually fire.
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
