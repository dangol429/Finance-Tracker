import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { MonthlySummary } from "@/api/types";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { TrendUpIcon } from "@/components/ui/icons";
import { formatMoney, formatMonthLong, formatMonthShort, toNumber } from "@/lib/format";
import { readPalette } from "@/lib/palette";
import { ChartTooltip } from "./ChartTooltip";
import styles from "./dashboard.module.css";

interface MonthlyTrendProps {
  data: MonthlySummary | undefined;
  loading: boolean;
  fetching?: boolean;
  themeKey: string;
}

/**
 * Income and expenses over time, as two filled areas.
 *
 * **Areas rather than lines**, because the quantity is a total rather than a
 * rate — the filled region carries "how much", which is what a monthly figure
 * means. The fills are gradients fading to transparent so the two can overlap
 * without either becoming unreadable.
 *
 * **The gaps are already filled by the API.** A month with no transactions
 * comes back with `0.00` rather than being absent, which is what stops the line
 * skipping March to May and implying April did not happen. Worth knowing
 * because the naive client-side version of this chart has that bug and it is
 * invisible until a user has a quiet month.
 */
export function MonthlyTrend({
  data,
  loading,
  fetching = false,
  themeKey,
}: MonthlyTrendProps): JSX.Element {
  const palette = useMemo(() => readPalette(), [themeKey]);

  const points = useMemo(
    () =>
      (data?.months ?? []).map((month) => ({
        month: month.month,
        income: toNumber(month.income),
        expense: toNumber(month.expense),
        net: toNumber(month.net),
        count: month.transaction_count,
      })),
    [data],
  );

  const hasData = points.some((point) => point.income > 0 || point.expense > 0);

  return (
    <Card title="Monthly trend" subtitle="Income against spending, month by month">
      {loading ? (
        <div className={styles.chartBodyTall}>
          <Skeleton height="100%" radius="var(--radius-md)" />
        </div>
      ) : !hasData ? (
        <EmptyState
          icon={<TrendUpIcon size={20} />}
          title="Nothing to plot yet"
          body="Add a few transactions, or widen the date range, and the trend will build itself."
        />
      ) : (
        <div
          className={styles.chartBodyTall}
          style={{ opacity: fetching ? 0.55 : 1, transition: "opacity 150ms" }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
              <defs>
                {/* Gradient ids must be unique per document. These are static
                    because the chart appears once; two instances would need
                    generated ids, since a duplicate id makes both charts use
                    whichever gradient the browser found first. */}
                <linearGradient id="fillIncome" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={palette.income} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={palette.income} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="fillExpense" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={palette.expense} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={palette.expense} stopOpacity={0} />
                </linearGradient>
              </defs>

              {/* Horizontal lines only. Vertical gridlines add clutter without
                  helping: the x-axis is categorical here, and the labels
                  already mark each column. */}
              <CartesianGrid
                strokeDasharray="3 3"
                stroke={palette.grid}
                vertical={false}
              />

              <XAxis
                dataKey="month"
                tickFormatter={formatMonthShort}
                stroke={palette.axis}
                fontSize={12}
                tickLine={false}
                axisLine={false}
                // Lets Recharts drop labels when the chart is too narrow to fit
                // them all, rather than overlapping them into mush.
                interval="preserveStartEnd"
                minTickGap={8}
              />
              <YAxis
                stroke={palette.axis}
                fontSize={12}
                tickLine={false}
                axisLine={false}
                width={62}
                // Compact on the axis, exact in the tooltip. The axis needs
                // scale; the precision belongs where someone is asking for a
                // specific figure.
                tickFormatter={(value: number) => formatMoney(value, { compact: true })}
              />

              <Tooltip
                content={
                  <ChartTooltip
                    labelFormatter={formatMonthLong}
                    footer={(payload) => {
                      const point = payload[0]?.payload as { net?: number } | undefined;
                      if (!point) return null;
                      const net = point.net ?? 0;
                      return (
                        <div className={styles.tooltipRow}>
                          <span>Net</span>
                          <span
                            className={styles.tooltipValue}
                            style={{
                              color: net >= 0 ? palette.income : palette.expense,
                            }}
                          >
                            {formatMoney(net, { signed: true })}
                          </span>
                        </div>
                      );
                    }}
                  />
                }
                cursor={{ stroke: palette.grid, strokeWidth: 1 }}
              />

              <Area
                type="monotone"
                dataKey="income"
                name="Income"
                stroke={palette.income}
                strokeWidth={2}
                fill="url(#fillIncome)"
                // `monotone` rather than `natural`: natural splines overshoot
                // between points, so a month between two high ones can be drawn
                // dipping below zero. On a money chart that is not a smoothing
                // artefact, it is a lie.
                animationDuration={700}
              />
              <Area
                type="monotone"
                dataKey="expense"
                name="Expense"
                stroke={palette.expense}
                strokeWidth={2}
                fill="url(#fillExpense)"
                animationDuration={700}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}
