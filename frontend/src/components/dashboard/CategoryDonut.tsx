import { useMemo } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import type { CategoryBreakdown } from "@/api/types";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { PieIcon } from "@/components/ui/icons";
import { colorForCategory, readPalette } from "@/lib/palette";
import { formatMoney, toNumber } from "@/lib/format";
import { ChartTooltip } from "./ChartTooltip";
import styles from "./dashboard.module.css";

interface CategoryDonutProps {
  data: CategoryBreakdown | undefined;
  loading: boolean;
  /** Dims the chart while a filter change is in flight. */
  fetching?: boolean;
  /** Re-read on theme change so the SVG fills follow the palette. */
  themeKey: string;
}

/**
 * Spending by category, as a donut.
 *
 * **A donut rather than a pie**, because the hole is useful: it holds the total
 * the slices divide, which is the number people look for first and which a pie
 * has nowhere to put. It also removes the hardest part of reading a pie — the
 * centre, where every slice converges and none of them is distinguishable.
 *
 * **Capped at seven slices plus "Other".** Beyond that the slices are thinner
 * than the gaps between them and the legend becomes a scroll. The API returns
 * them already sorted largest-first, so the cut is the tail rather than an
 * arbitrary subset — and "Other" carries the real remaining total, so the parts
 * still sum to the whole.
 */
const MAX_SLICES = 7;

export function CategoryDonut({
  data,
  loading,
  fetching = false,
  themeKey,
}: CategoryDonutProps): JSX.Element {
  const palette = useMemo(() => readPalette(), [themeKey]);

  const slices = useMemo(() => {
    if (!data?.categories.length) return [];

    const visible = data.categories.slice(0, MAX_SLICES);
    const rest = data.categories.slice(MAX_SLICES);

    const mapped = visible.map((slice) => ({
      name: slice.category_name,
      value: toNumber(slice.total),
      share: toNumber(slice.share),
      color: colorForCategory(slice.category_id, palette),
    }));

    if (rest.length) {
      // Summing the tail here is display arithmetic on figures the database
      // already computed, not a total being derived in the client — the
      // difference that matters is that nothing is stored from it.
      const otherTotal = rest.reduce((sum, slice) => sum + toNumber(slice.total), 0);
      const otherShare = rest.reduce((sum, slice) => sum + toNumber(slice.share), 0);
      mapped.push({
        name: `Other (${rest.length})`,
        value: otherTotal,
        share: otherShare,
        color: palette.series[palette.series.length - 1]!,
      });
    }

    return mapped;
  }, [data, palette]);

  return (
    <Card
      title="Where the money went"
      subtitle={data ? `${data.transaction_count} transactions` : undefined}
    >
      {loading ? (
        <div className={styles.donutRelative}>
          <Skeleton height="100%" radius="var(--radius-md)" />
        </div>
      ) : slices.length === 0 ? (
        <EmptyState
          icon={<PieIcon size={20} />}
          title="No spending in this range"
          body="Once there are expenses to break down, they will appear here as a donut with a legend."
        />
      ) : (
        <>
          <div
            className={styles.donutRelative}
            style={{ opacity: fetching ? 0.55 : 1, transition: "opacity 150ms" }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={slices}
                  dataKey="value"
                  nameKey="name"
                  innerRadius="62%"
                  outerRadius="88%"
                  // A small gap between slices reads as separation without a
                  // stroke, which on a dark background would look like a border
                  // rather than a break.
                  paddingAngle={2}
                  strokeWidth={0}
                  // Start at 12 o'clock and run clockwise. Recharts' default
                  // starts at 3 o'clock counter-clockwise, which puts the
                  // largest slice somewhere unexpected.
                  startAngle={90}
                  endAngle={-270}
                  animationDuration={600}
                >
                  {slices.map((slice) => (
                    <Cell key={slice.name} fill={slice.color} />
                  ))}
                </Pie>
                <Tooltip
                  content={<ChartTooltip />}
                  // Recharts draws a grey highlight rectangle behind the
                  // hovered element by default, which on a donut is a full-width
                  // block that looks like a rendering bug.
                  cursor={false}
                />
              </PieChart>
            </ResponsiveContainer>

            <div className={styles.donutCenter}>
              <span className={styles.donutCenterLabel}>Total</span>
              <span className={styles.donutCenterValue}>
                {formatMoney(data?.total ?? 0)}
              </span>
            </div>
          </div>

          {/* A custom legend rather than Recharts' built-in one, because this
              one carries the amount and the share as well as the name — which
              is what makes the chart readable without hovering every slice. */}
          <ul className={styles.legend}>
            {slices.map((slice) => (
              <li className={styles.legendRow} key={slice.name}>
                <span
                  className={styles.legendSwatch}
                  style={{ background: slice.color }}
                  aria-hidden="true"
                />
                <span className={styles.legendName} title={slice.name}>
                  {slice.name}
                </span>
                <span className={styles.legendValue}>{formatMoney(slice.value)}</span>
                <span className={styles.legendShare}>{slice.share.toFixed(1)}%</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
