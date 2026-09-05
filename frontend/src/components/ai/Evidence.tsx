import { useState } from "react";

import type { EvidenceStep } from "@/api/types";
import { ReceiptIcon } from "@/components/ui/icons";
import { formatDate } from "@/lib/format";
import styles from "./ai.module.css";

/**
 * The receipt behind an AI answer: every query that ran, and what it returned.
 *
 * **This component is the reason the Ask page can be trusted.** A sentence like
 * "you spent 412.30 on food in June" reads identically whether the figure came
 * from a `SUM` or from a model's imagination, and a user has no way to tell
 * them apart. Rendering the tool calls underneath turns the claim into a
 * citation — the date window, the categories, the row count and the total, as
 * the database produced them.
 *
 * **Collapsed by default, and that is a real trade-off.** The evidence is the
 * point, but showing four tables above a one-sentence answer buries the answer.
 * The compromise is a disclosure that names how many queries ran, so the user
 * knows the receipt exists without having to look at it every time.
 *
 * **The shapes are narrowed at runtime, not asserted.** `result` is typed
 * `unknown` because each tool returns something different, and the honest way
 * to render it is to check what actually arrived rather than to cast. An
 * unrecognised shape falls through to formatted JSON — which is ugly, and is
 * still strictly better than a blank panel or a crash when a tool's output
 * changes on the server.
 */

interface EvidenceProps {
  steps: EvidenceStep[];
}

/** One row of a grouped summary, once we have checked it looks like one. */
interface GroupRow {
  label: string;
  total: string;
  count: number;
}

/** One transaction row from `list_transactions`. */
interface TransactionRow {
  id: number;
  date: string;
  description: string | null;
  amount: string;
  category: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Read the `groups` array out of a `summarize_transactions` result.
 *
 * Returns null rather than throwing when the shape is not what we expect, so
 * the caller can fall back. The key is either `category` or `month` depending
 * on how the model grouped, which is why both are checked here rather than the
 * component branching on `grouped_by` — one place that knows the shape.
 */
function readGroups(result: unknown): GroupRow[] | null {
  if (!isRecord(result) || !Array.isArray(result.groups)) return null;

  const rows: GroupRow[] = [];
  for (const group of result.groups) {
    if (!isRecord(group)) return null;
    const label = group.category ?? group.month;
    if (typeof label !== "string" || typeof group.total !== "string") return null;
    rows.push({
      label,
      total: group.total,
      count: typeof group.transaction_count === "number" ? group.transaction_count : 0,
    });
  }
  return rows;
}

function readTransactions(result: unknown): TransactionRow[] | null {
  if (!isRecord(result) || !Array.isArray(result.transactions)) return null;

  const rows: TransactionRow[] = [];
  for (const row of result.transactions) {
    if (!isRecord(row)) return null;
    if (typeof row.id !== "number" || typeof row.amount !== "string") return null;
    rows.push({
      id: row.id,
      date: typeof row.date === "string" ? row.date : "",
      description: typeof row.description === "string" ? row.description : null,
      amount: row.amount,
      category: typeof row.category === "string" ? row.category : "—",
    });
  }
  return rows;
}

/**
 * Render the tool's arguments as the query they are.
 *
 * Shown verbatim, in monospace, rather than prettified into a sentence. The
 * value of this line is that it is the *exact* structured query — a rendering
 * that paraphrased it would be one more thing between the user and the fact
 * they are trying to check.
 */
function ArgumentSummary({ step }: { step: EvidenceStep }): JSX.Element {
  const parts = Object.entries(step.arguments)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? `[${value.join(",")}]` : String(value)}`);

  return (
    <div className={styles.evidenceHeader}>
      {step.tool}({parts.join(", ")})
    </div>
  );
}

function StepBody({ step }: { step: EvidenceStep }): JSX.Element {
  const groups = readGroups(step.result);
  if (groups) {
    return (
      <div className={styles.evidenceTableWrap}>
        <table className={styles.evidenceTable}>
          <thead>
            <tr>
              <th scope="col">Group</th>
              <th scope="col" className={styles.numeric}>
                Total
              </th>
              <th scope="col" className={styles.numeric}>
                Count
              </th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={group.label}>
                <td>{group.label}</td>
                <td className={styles.numeric}>{group.total}</td>
                <td className={styles.numeric}>{group.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const transactions = readTransactions(step.result);
  if (transactions) {
    return (
      <div className={styles.evidenceTableWrap}>
        <table className={styles.evidenceTable}>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Description</th>
              <th scope="col">Category</th>
              <th scope="col" className={styles.numeric}>
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((row) => (
              <tr key={row.id}>
                <td>{row.date ? formatDate(row.date) : "—"}</td>
                <td>{row.description ?? "—"}</td>
                <td>{row.category}</td>
                <td className={styles.numeric}>{row.amount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  // An ungrouped total: the single most common result, and the one where a
  // table of one row would be sillier than a sentence.
  if (
    isRecord(step.result) &&
    typeof step.result.total === "string" &&
    typeof step.result.transaction_count === "number"
  ) {
    return (
      <p className={styles.evidenceTotal}>
        Total <strong>{step.result.total}</strong> across{" "}
        {step.result.transaction_count.toLocaleString()}{" "}
        {step.result.transaction_count === 1 ? "transaction" : "transactions"}
      </p>
    );
  }

  return (
    <div className={styles.evidenceTableWrap}>
      <pre className={styles.evidenceTotal}>{JSON.stringify(step.result, null, 2)}</pre>
    </div>
  );
}

export function Evidence({ steps }: EvidenceProps): JSX.Element | null {
  const [open, setOpen] = useState(false);

  // No queries ran, so there is nothing to show and no disclosure to offer.
  // This is the normal case for a question the ledger cannot answer, and the
  // page says so separately rather than showing an empty "0 queries" control.
  if (steps.length === 0) return null;

  return (
    <div className={styles.evidence}>
      <button
        type="button"
        className={styles.evidenceToggle}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        aria-expanded={open}
      >
        <ReceiptIcon size={14} />
        {open ? "Hide" : "Show"} the {steps.length}{" "}
        {steps.length === 1 ? "query" : "queries"} behind this answer
      </button>

      {open && (
        <div className={styles.evidenceBody}>
          {steps.map((step, index) => (
            // Index as key is safe and correct here: this list is ordered,
            // append-only within one response, and never reordered or filtered.
            <div key={index} className={styles.evidenceStep}>
              <ArgumentSummary step={step} />
              <StepBody step={step} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
