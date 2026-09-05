import { useState } from "react";

import { ApiError } from "@/api/client";
import { useMonthlyInsight } from "@/api/queries";
import type { MonthlyInsightFacts } from "@/api/types";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { SparkleIcon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatMonthLong, formatMoney, toNumber } from "@/lib/format";
import styles from "./ai.module.css";

/**
 * A short AI write-up of one month, with the figures it was written from.
 *
 * **The facts panel is not a detail — it is the feature.** A paragraph about
 * spending reads identically whether its numbers are real or invented, so the
 * server returns the aggregates it handed the model, and this card renders them
 * directly beneath the prose. A user who doubts "up 34% on last month" can see
 * both totals without leaving the page, and a claim that does not match its own
 * evidence becomes visible instead of persuasive.
 *
 * **Generated on request, never on mount.** The dashboard mounts on every visit
 * and this call costs money, so a card that generated itself would bill the
 * user for scrolling past it. The button is the consent.
 *
 * **A month that had no transactions is answered by the server without a model
 * at all** — `model` comes back null, and the badge says "No activity" rather
 * than "AI". Labelling a fixed sentence as AI-written is a small lie that
 * cheapens the badge everywhere it is true.
 */

interface InsightsCardProps {
  /** The month to summarise, `"YYYY-MM"`. Derived from the dashboard filters. */
  month: string;
  accountId?: number;
}

export function InsightsCard({ month, accountId }: InsightsCardProps): JSX.Element {
  // `enabled` is flipped by the button rather than by a URL or a filter, so the
  // paid call happens exactly when someone asks for it. Reset when the month
  // changes: an insight for June must not stay on screen labelled July.
  const [requested, setRequested] = useState(false);
  const [requestedFor, setRequestedFor] = useState(month);

  if (requestedFor !== month) {
    // Derived-state reset during render, which React supports and prefers over
    // an effect: the alternative renders the stale insight for one frame under
    // the new month's heading before the effect clears it.
    //
    // The condition deliberately does *not* also test `requested`. Gating on it
    // leaves `requestedFor` stale whenever the month changes while no summary
    // has been generated — and the next click then sets `requested` true, which
    // makes this branch fire and immediately unset it. The button would appear
    // to do nothing. Resetting on every month change keeps the two in step.
    setRequestedFor(month);
    setRequested(false);
  }

  const insight = useMonthlyInsight(month, accountId, requested);

  const notConfigured = insight.error instanceof ApiError && insight.error.status === 503;

  return (
    <Card
      title={`${formatMonthLong(month)} in review`}
      subtitle="Written from your own totals — the figures behind it are shown below."
      action={
        insight.data ? (
          <span
            className={`${styles.aiMark} ${
              insight.data.model ? "" : styles.aiMarkStatic
            }`}
          >
            <SparkleIcon size={12} />
            {insight.data.model ? "AI" : "No activity"}
          </span>
        ) : undefined
      }
    >
      {!requested && (
        <>
          <p className={styles.insightSummary}>
            Generate a short summary of {formatMonthLong(month)} — what changed, and which
            categories moved.
          </p>
          <div className={styles.reviewActions}>
            <Button variant="primary" onClick={() => setRequested(true)}>
              <SparkleIcon size={15} />
              Write the summary
            </Button>
          </div>
        </>
      )}

      {insight.isLoading && requested && (
        <>
          <Skeleton height="1.4em" width="70%" />
          <Skeleton height="1em" width="96%" />
          <Skeleton height="1em" width="88%" />
        </>
      )}

      {notConfigured && (
        <Alert variant="info">
          This server has no AI provider configured. Set <code>ANTHROPIC_API_KEY</code> on the
          API to enable monthly summaries.
        </Alert>
      )}
      {insight.error && !notConfigured && <Alert>{insight.error.message}</Alert>}

      {insight.data && (
        <>
          <p className={styles.insightHeadline}>{insight.data.headline}</p>
          <p className={styles.insightSummary}>{insight.data.summary}</p>

          {insight.data.highlights.length > 0 && (
            <ul className={styles.highlights}>
              {insight.data.highlights.map((highlight) => (
                <li key={highlight} className={styles.highlight}>
                  {highlight}
                </li>
              ))}
            </ul>
          )}

          <FactsPanel facts={insight.data.facts} />
        </>
      )}
    </Card>
  );
}

/**
 * The aggregates the summary was written from.
 *
 * Every figure here came out of the same SQL the dashboard charts run — the
 * server returns the identical `IncomeVsExpense` and `CategoryBreakdown`
 * objects `/summary/*` would. So this panel and the donut above it cannot
 * disagree, which is the property that makes the prose checkable rather than
 * merely confident.
 */
function FactsPanel({ facts }: { facts: MonthlyInsightFacts }): JSX.Element {
  const spend = toNumber(facts.totals.expense.total);
  const previousSpend = toNumber(facts.previous_totals.expense.total);
  const income = toNumber(facts.totals.income.total);
  const topCategory = facts.categories.categories[0];

  return (
    <div className={styles.factsGrid}>
      <Fact
        label="Spent"
        value={formatMoney(spend)}
        delta={describeChange(spend, previousSpend)}
      />
      <Fact label="Earned" value={formatMoney(income)} />
      <Fact
        label="Net"
        value={formatMoney(facts.totals.net)}
        delta={
          facts.totals.savings_rate === null
            ? "No income this month"
            : `${toNumber(facts.totals.savings_rate).toFixed(1)}% saved`
        }
      />
      <Fact
        label="Largest category"
        value={topCategory?.category_name ?? "—"}
        delta={topCategory ? formatMoney(topCategory.total) : "Nothing categorised"}
      />
    </div>
  );
}

function Fact({
  label,
  value,
  delta,
}: {
  label: string;
  value: string;
  delta?: string;
}): JSX.Element {
  return (
    <div className={styles.fact}>
      <span className={styles.factLabel}>{label}</span>
      <span className={styles.factValue}>{value}</span>
      {delta && <span className={styles.factDelta}>{delta}</span>}
    </div>
  );
}

/**
 * Describe a month-on-month change in words.
 *
 * Returns a sentence rather than a percentage when the previous month was zero.
 * A percent change from nothing is undefined, and the tempting renderings are
 * both wrong: "+100%" understates it and "∞%" is not a figure. Same reasoning
 * the API applies to `savings_rate`, where an undefined rate stays null rather
 * than being flattened to a convenient zero.
 */
function describeChange(current: number, previous: number): string {
  if (previous === 0) {
    return current === 0 ? "Same as last month" : "Nothing spent last month";
  }
  const change = ((current - previous) / previous) * 100;
  if (Math.abs(change) < 0.5) return "Level with last month";
  return `${change > 0 ? "+" : ""}${change.toFixed(0)}% vs. last month`;
}
