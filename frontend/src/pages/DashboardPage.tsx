import { useMemo } from "react";

import {
  useAccounts,
  useCategories,
  useCategoryBreakdown,
  useMonthlySummary,
  useTotals,
} from "@/api/queries";
import { InsightsCard } from "@/components/ai/InsightsCard";
import { CategoryDonut } from "@/components/dashboard/CategoryDonut";
import { IncomeVsExpenseBars } from "@/components/dashboard/IncomeVsExpenseBars";
import { MonthlyTrend } from "@/components/dashboard/MonthlyTrend";
import { StatCard, StatDelta } from "@/components/dashboard/StatCard";
import { FilterBar } from "@/components/filters/FilterBar";
import { Alert } from "@/components/ui/Alert";
import { PieIcon, TrendDownIcon, TrendUpIcon, WalletIcon } from "@/components/ui/icons";
import { useFilters } from "@/hooks/useFilters";
import { useTheme } from "@/hooks/useTheme";
import { toNumber } from "@/lib/format";
import { Onboarding } from "./Onboarding";
import dashboardStyles from "@/components/dashboard/dashboard.module.css";
import styles from "./pages.module.css";

export function DashboardPage(): JSX.Element {
  const { filters, setFilters, reset, isFiltered, summaryParams } = useFilters();
  const { theme } = useTheme();

  const accounts = useAccounts();
  const categories = useCategories();
  const monthly = useMonthlySummary(summaryParams);
  const breakdown = useCategoryBreakdown(summaryParams, "expense");
  const totals = useTotals(summaryParams);

  // The stat figures, derived once. Note `balance` comes from the aggregation
  // endpoint rather than from `Account.balance`: that column exists but nothing
  // in the API maintains it (see `schemas/account.py`), so trusting it would
  // show a stale zero next to a table full of transactions.
  const stats = useMemo(() => {
    const income = toNumber(totals.data?.income.total);
    const expense = toNumber(totals.data?.expense.total);

    const months = monthly.data?.months ?? [];
    const currentMonth = months.at(-1);
    const previousMonth = months.at(-2);

    const topCategory = breakdown.data?.categories[0];

    return {
      balance: income - expense,
      income,
      expense,
      monthSpend: toNumber(currentMonth?.expense),
      previousMonthSpend: toNumber(previousMonth?.expense),
      topCategoryName: topCategory?.category_name ?? "—",
      topCategoryTotal: toNumber(topCategory?.total),
      topCategoryShare: toNumber(topCategory?.share),
      savingsRate: totals.data?.savings_rate,
    };
  }, [totals.data, monthly.data, breakdown.data]);

  // Which month the AI summary describes: the last one in the filtered range,
  // which is the month the charts above are already ending on. Derived from the
  // data rather than from `filters.dateTo` so the two cannot disagree — a range
  // ending mid-month still summarises that month, and an empty result falls
  // back to the current one rather than rendering a card with no month at all.
  const insightMonth = useMemo(() => {
    const last = monthly.data?.months.at(-1)?.month;
    if (last) return last;
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  }, [monthly.data]);

  // Only the *first* load shows skeletons. A refetch caused by a filter change
  // keeps the previous data on screen and dims it instead (`placeholderData` in
  // queries.ts), because collapsing a populated dashboard to skeletons on every
  // filter tweak is the thing that makes an app feel slow even when it is fast.
  const initialLoading = totals.isLoading || monthly.isLoading;
  const refetching = totals.isFetching || monthly.isFetching || breakdown.isFetching;

  const loadError = accounts.error ?? totals.error ?? monthly.error ?? breakdown.error;

  if (accounts.isLoading) {
    return <div className={styles.page} />;
  }

  // A brand-new user owns no accounts. Showing them an empty dashboard is
  // technically accurate and reads as a broken app.
  if (accounts.data && accounts.data.length === 0) {
    return <Onboarding />;
  }

  return (
    <div className={styles.page}>
      {loadError && <Alert>{loadError.message}</Alert>}

      <FilterBar
        filters={filters}
        setFilters={setFilters}
        reset={reset}
        isFiltered={isFiltered}
        accounts={accounts.data ?? []}
        categories={categories.data ?? []}
        // The dashboard has no table, so the table-only filters are hidden
        // rather than shown doing nothing.
        compact
      />

      <div className={dashboardStyles.statGrid}>
        <StatCard
          index={0}
          label="Net position"
          icon={<WalletIcon size={15} />}
          value={stats.balance}
          tone={stats.balance >= 0 ? "income" : "expense"}
          loading={initialLoading}
          meta={
            stats.savingsRate !== null && stats.savingsRate !== undefined
              ? `Saving ${toNumber(stats.savingsRate).toFixed(1)}% of income`
              : "No income in this period"
          }
        />
        <StatCard
          index={1}
          label="Money in"
          icon={<TrendUpIcon size={15} />}
          value={stats.income}
          tone="income"
          loading={initialLoading}
          meta={`${totals.data?.income.transaction_count ?? 0} transactions`}
        />
        <StatCard
          index={2}
          label="This month's spend"
          icon={<TrendDownIcon size={15} />}
          value={stats.monthSpend}
          loading={initialLoading}
          meta={
            <StatDelta
              current={stats.monthSpend}
              previous={stats.previousMonthSpend}
              // Spending more is not an improvement, so the arrow's colour has
              // to be inverted relative to the income card.
              positiveIsGood={false}
              suffix="vs. last month"
            />
          }
        />
        <StatCard
          index={3}
          label="Top category"
          icon={<PieIcon size={15} />}
          value={stats.topCategoryTotal}
          displayValue={stats.topCategoryName}
          loading={initialLoading}
          meta={
            stats.topCategoryTotal > 0
              ? `${stats.topCategoryShare.toFixed(1)}% of spending`
              : "Nothing categorised yet"
          }
        />
      </div>

      {/* Placed after the stat cards and before the charts: the summary reads
          as a caption for the numbers above it, and a user who wants only the
          figures never has to scroll past a paragraph to reach them. It
          generates nothing until asked — see the note in `InsightsCard`. */}
      <InsightsCard month={insightMonth} accountId={summaryParams.account_id} />

      <MonthlyTrend
        data={monthly.data}
        loading={initialLoading}
        fetching={refetching}
        themeKey={theme}
      />

      <div className={styles.chartRow}>
        <IncomeVsExpenseBars
          data={monthly.data}
          loading={initialLoading}
          fetching={refetching}
          themeKey={theme}
        />
        <CategoryDonut
          data={breakdown.data}
          loading={breakdown.isLoading}
          fetching={refetching}
          themeKey={theme}
        />
      </div>
    </div>
  );
}
