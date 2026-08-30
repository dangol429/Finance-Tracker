import { useEffect, useRef, useState } from "react";

/**
 * Animate a number from its previous value to a new one.
 *
 * The micro-interaction behind the stat cards. Three things make it feel like a
 * product rather than a gimmick:
 *
 * **It eases out.** A linear count is mechanical; easing out means the number
 * moves fast enough to register as motion and settles gently on the value the
 * eye needs to read.
 *
 * **It animates from the previous value, not from zero.** When a filter changes
 * and the monthly total goes from $2,400 to $2,650, counting up from zero
 * implies the data was replaced. Counting from the old figure shows the
 * *change*, which is the information the animation exists to convey. Only the
 * first mount starts at zero.
 *
 * **It respects `prefers-reduced-motion`.** A CSS media query cannot stop a
 * `requestAnimationFrame` loop, so the check has to happen here — the hook
 * jumps straight to the final value. For someone with a vestibular disorder,
 * numbers spinning on every filter change is not decoration, it is a symptom
 * trigger.
 */
export function useCountUp(target: number, durationMs = 700): number {
  const [displayed, setDisplayed] = useState(0);
  const fromRef = useRef(0);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    if (prefersReducedMotion || !Number.isFinite(target)) {
      fromRef.current = target;
      setDisplayed(target);
      return;
    }

    const from = fromRef.current;
    const delta = target - from;

    if (delta === 0) {
      setDisplayed(target);
      return;
    }

    const start = performance.now();

    const step = (now: number) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / durationMs, 1);
      // easeOutCubic — the same curve family as --ease-out in tokens.css, so
      // the numbers and the card that holds them decelerate together.
      const eased = 1 - Math.pow(1 - progress, 3);

      setDisplayed(from + delta * eased);

      if (progress < 1) {
        frameRef.current = requestAnimationFrame(step);
      } else {
        // Snap to the exact target: the eased value approaches it but floating
        // point will not land on it, and a balance that reads $2,649.997 is a
        // bug report.
        fromRef.current = target;
        setDisplayed(target);
      }
    };

    frameRef.current = requestAnimationFrame(step);

    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      // Record where the interrupted animation actually got to, so a filter
      // changed mid-count continues from the visible number rather than
      // jumping back to where the last one started.
      fromRef.current = displayed;
    };
    // `displayed` is deliberately not a dependency: including it would restart
    // the animation on every frame it sets. It is read only in cleanup, where
    // the latest value is what is wanted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, durationMs]);

  return displayed;
}
