import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/Skeleton";
import { useCountUp } from "@/hooks/useCountUp";
import { formatMoney } from "@/lib/format";
import styles from "./dashboard.module.css";

interface StatCardProps {
  label: string;
  icon: ReactNode;
  /** The figure, as a number. Money strings should be parsed by the caller. */
  value: number;
  /** Rendered instead of a formatted amount — for a category name. */
  displayValue?: string;
  tone?: "neutral" | "income" | "expense";
  /** A line under the value: a comparison, a count, a share. */
  meta?: ReactNode;
  loading?: boolean;
  /** Staggers the mount animation so the row arrives left to right. */
  index?: number;
  currency?: string;
}

/**
 * One headline figure.
 *
 * **The count-up is the point of this component**, and it is more than
 * decoration: because `useCountUp` animates from the *previous* value rather
 * than from zero, changing a filter shows the figure travelling from what it
 * was to what it now is. That movement is information — it says how much the
 * filter changed things — which a number that simply swaps does not convey.
 *
 * The mount stagger is 60ms per card. Enough that the row reads as arriving in
 * sequence; short enough that the fourth card is not still animating after the
 * eye has moved on.
 */
export function StatCard({
  label,
  icon,
  value,
  displayValue,
  tone = "neutral",
  meta,
  loading = false,
  index = 0,
  currency = "USD",
}: StatCardProps): JSX.Element {
  const animated = useCountUp(value);

  const toneClass =
    tone === "income"
      ? styles.statValueIncome
      : tone === "expense"
        ? styles.statValueExpense
        : "";

  return (
    <div className={styles.statCard} style={{ animationDelay: `${index * 60}ms` }}>
      <div className={styles.statHeader}>
        <span className={styles.statIcon}>{icon}</span>
        <span className={styles.statLabel}>{label}</span>
      </div>

      {loading ? (
        <Skeleton width="60%" height="1.75rem" />
      ) : (
        <div className={`${styles.statValue} ${toneClass}`}>
          {displayValue ?? formatMoney(animated, { currency })}
        </div>
      )}

      {/* Reserved height even when empty (see `.statMeta`'s min-height), so a
          card with no comparison line is the same height as one that has it.
          Cards of differing heights in a grid is the sort of thing nobody can
          name but everybody notices. */}
      <div className={styles.statMeta}>{loading ? <Skeleton width="40%" /> : meta}</div>
    </div>
  );
}

/**
 * The change against a previous period, as a coloured delta.
 *
 * `positiveIsGood` exists because the sign's meaning is not universal: income
 * rising is good, spending rising is not. Hard-coding green-for-up would make
 * the spending card congratulate the user for spending more.
 */
export function StatDelta({
  current,
  previous,
  positiveIsGood = true,
  suffix = "vs. previous period",
}: {
  current: number;
  previous: number;
  positiveIsGood?: boolean;
  suffix?: string;
}): JSX.Element | null {
  // No baseline means no comparison. Rendering "+100%" against zero is
  // arithmetically defensible and communicates nothing — the same reasoning the
  // API uses for returning a null savings rate rather than inventing one.
  if (previous === 0) return null;

  const change = ((current - previous) / Math.abs(previous)) * 100;
  if (!Number.isFinite(change)) return null;

  const rose = change > 0;
  const good = rose === positiveIsGood;

  return (
    <>
      <span className={`${styles.statDelta} ${good ? styles.deltaUp : styles.deltaDown}`}>
        {rose ? "↑" : "↓"} {Math.abs(change).toFixed(1)}%
      </span>
      <span>{suffix}</span>
    </>
  );
}
