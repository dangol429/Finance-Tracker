import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { MonthlySummary } from "@/api/types";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { TrendDownIcon } from "@/components/ui/icons";
import { formatMoney, formatMonthLong, formatMonthShort, toNumber } from "@/lib/format";
import { readPalette } from "@/lib/palette";
import { ChartTooltip } from "./ChartTooltip";
import styles from "./dashboard.module.css";

interface IncomeVsExpenseBarsProps {
  data: MonthlySummary | undefined;
  loading: boolean;
  fetching?: boolean;
  themeKey: string;
}

/**
 * Net position per month, as bars above and below zero.
 *
 * **Why net rather than a grouped income/expense pair.** The monthly trend
 * chart already shows both series against each other; repeating them as bars
 * says the same thing twice. What that chart *cannot* show is the sign — whether
 * a month came out ahead — because two overlapping areas leave the reader to
 * subtract by eye. Bars crossing a zero line answer it immediately, and the
 * colour reinforces rather than carries the message (the position relative to
 * the axis is the primary signal, which is what makes it readable without
 * colour vision).
 */
export function IncomeVsExpenseBars({
  data,
  loading,
  fetching = false,
  themeKey,
}: IncomeVsExpenseBarsProps): JSX.Element {
  const palette = useMemo(() => readPalette(), [themeKey]);

  const points = useMemo(
    () =>
      (data?.months ?? []).map((month) => ({
        month: month.month,
        net: toNumber(month.net),
        income: toNumber(month.income),
        expense: toNumber(month.expense),
      })),
    [data],
  );

  const hasData = points.some((point) => point.income > 0 || point.expense > 0);

  return (
    <Card title="Net by month" subtitle="Above the line is a month you saved">
      {loading ? (
        <div className={styles.chartBody}>
          <Skeleton height="100%" radius="var(--radius-md)" />
        </div>
      ) : !hasData ? (
        <EmptyState
          icon={<TrendDownIcon size={20} />}
          title="No months to compare"
          body="This chart compares what came in against what went out, once there is something in both columns."
        />
      ) : (
        <div
          className={styles.chartBody}
          style={{ opacity: fetching ? 0.55 : 1, transition: "opacity 150ms" }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={palette.grid} vertical={false} />

              <XAxis
                dataKey="month"
                tickFormatter={formatMonthShort}
                stroke={palette.axis}
                fontSize={12}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
                minTickGap={8}
              />
              <YAxis
                stroke={palette.axis}
                fontSize={12}
                tickLine={false}
                axisLine={false}
                width={62}
                tickFormatter={(value: number) => formatMoney(value, { compact: true })}
              />

              <Tooltip
                content={
                  <ChartTooltip
                    labelFormatter={formatMonthLong}
                    footer={(payload) => {
                      const point = payload[0]?.payload as
                        | { income?: number; expense?: number }
                        | undefined;
                      if (!point) return null;
                      return (
                        <>
                          <div className={styles.tooltipRow}>
                            <span>In</span>
                            <span className={styles.tooltipValue}>
                              {formatMoney(point.income ?? 0)}
                            </span>
                          </div>
                          <div className={styles.tooltipRow}>
                            <span>Out</span>
                            <span className={styles.tooltipValue}>
                              {formatMoney(point.expense ?? 0)}
                            </span>
                          </div>
                        </>
                      );
                    }}
                  />
                }
                // A translucent wash over the hovered column, in the accent
                // rather than Recharts' default grey — which on a dark theme
                // reads as a stuck highlight.
                cursor={{ fill: "var(--bg-hover)" }}
              />

              <Bar dataKey="net" name="Net" radius={[4, 4, 4, 4]} animationDuration={600}>
                {/* Per-bar colour, which a single `fill` cannot express: the
                    colour depends on each datum's sign, not on the series. */}
                {points.map((point) => (
                  <Cell
                    key={point.month}
                    fill={point.net >= 0 ? palette.income : palette.expense}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}
