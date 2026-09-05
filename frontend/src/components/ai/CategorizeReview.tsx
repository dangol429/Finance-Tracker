import { useEffect, useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import { useApplyCategories, useSuggestCategories } from "@/api/queries";
import type { Category, CategorySuggestion } from "@/api/types";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { BareSelect } from "@/components/ui/Field";
import { SparkleIcon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/Spinner";
import { formatDate, formatMoney } from "@/lib/format";
import styles from "./ai.module.css";
import uiStyles from "@/components/ui/ui.module.css";

/**
 * Review AI category suggestions, then apply the ones you accept.
 *
 * **The whole design goal is that correcting is as easy as accepting.** A
 * review screen where "accept" is one click and "fix" is five is a rubber
 * stamp, and a rubber stamp on a model's output is how a year of history
 * quietly acquires wrong categories. So every row carries a full category
 * dropdown pre-set to the suggestion: changing it is one interaction, exactly
 * like leaving it. The server does not know or care which rows were corrected —
 * `/ai/categorize/apply` takes plain `(transaction, category)` pairs.
 *
 * **Only confident suggestions are ticked by default.** `recommended` is
 * computed server-side against the confidence threshold, so the client and the
 * server cannot drift about what "confident" means. The rest are shown
 * unticked rather than hidden — a user scanning for a wrong guess needs to see
 * the uncertain ones, and hiding them would mean the only visible suggestions
 * are the ones least in need of review.
 *
 * **Nothing is written until "Apply" is pressed.** The suggestion request is
 * read-only on the server, and this component holds every edit in local state
 * until the single explicit write.
 */

interface CategorizeReviewProps {
  open: boolean;
  onClose: () => void;
  categories: Category[];
  /** Restricts the suggestion request to one account, matching the page filter. */
  accountId?: number;
}

export function CategorizeReview({
  open,
  onClose,
  categories,
  accountId,
}: CategorizeReviewProps): JSX.Element | null {
  const suggest = useSuggestCategories();
  const apply = useApplyCategories();

  // The user's decisions, keyed by transaction id:
  //   `checked`  — whether this row will be written
  //   `chosen`   — which category, which starts as the suggestion and may be
  //                changed to any category of the matching type
  const [checked, setChecked] = useState<Record<number, boolean>>({});
  const [chosen, setChosen] = useState<Record<number, number>>({});

  const suggestions = suggest.data?.suggestions;

  // Seed the decisions when a fresh set of suggestions arrives. Keyed on the
  // `suggestions` array identity rather than on `open`, so re-running the
  // suggestion request inside an already-open panel re-seeds correctly instead
  // of leaving the previous run's ticks behind on rows that no longer exist.
  useEffect(() => {
    if (!suggestions) return;
    setChecked(Object.fromEntries(suggestions.map((s) => [s.transaction_id, s.recommended])));
    setChosen(Object.fromEntries(suggestions.map((s) => [s.transaction_id, s.category_id])));
  }, [suggestions]);

  // Request suggestions when the panel opens, once. `suggest.reset()` on close
  // is what makes "once" true: without it, reopening would show the previous
  // run's results with no request and no way to tell they were stale.
  useEffect(() => {
    if (!open) return;
    suggest.mutate({ account_id: accountId });
    // `suggest` is a stable mutation object from TanStack Query; including it
    // would re-fire the request on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, accountId]);

  useEffect(() => {
    if (open) return;
    suggest.reset();
    apply.reset();
    setChecked({});
    setChosen({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Escape closes, matching `ConfirmDialog`. A panel with no keyboard exit is
  // the single most common accessibility failure in a hand-rolled modal.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  const acceptedCount = useMemo(
    () => Object.values(checked).filter(Boolean).length,
    [checked],
  );

  if (!open) return null;

  const notConfigured = suggest.error instanceof ApiError && suggest.error.status === 503;

  function submit(): void {
    if (!suggestions) return;
    const assignments = suggestions
      .filter((s) => checked[s.transaction_id])
      .map((s) => ({
        transaction_id: s.transaction_id,
        category_id: chosen[s.transaction_id] ?? s.category_id,
      }));
    if (assignments.length === 0) return;
    apply.mutate(assignments, { onSuccess: onClose });
  }

  return (
    <div
      className={uiStyles.modalBackdrop}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={styles.reviewPanel}
        role="dialog"
        aria-modal="true"
        aria-label="Review category suggestions"
      >
        <p className={styles.reviewPanelTitle}>
          <SparkleIcon size={15} /> Suggested categories
        </p>
        <p className={styles.reviewIntro}>
          Nothing has been changed yet. Untick anything you disagree with, or pick a
          different category — then apply.
        </p>

        {suggest.isPending && (
          <EmptyState
            icon={<Spinner large />}
            title="Reading your uncategorised transactions…"
            body="This takes a few seconds."
          />
        )}

        {notConfigured && (
          <Alert variant="info">
            This server has no AI provider configured. Set <code>ANTHROPIC_API_KEY</code> on
            the API to enable suggestions.
          </Alert>
        )}
        {suggest.error && !notConfigured && <Alert>{suggest.error.message}</Alert>}
        {apply.error && <Alert>{apply.error.message}</Alert>}

        {suggest.data && suggestions && suggestions.length === 0 && (
          <EmptyState
            title="No suggestions"
            body={
              suggest.data.considered === 0
                ? "Every transaction already has a category."
                : "The model was not confident enough about any of them to suggest a category."
            }
          />
        )}

        {suggestions && suggestions.length > 0 && (
          <>
            <div className={styles.reviewList}>
              {suggestions.map((suggestion) => (
                <SuggestionRow
                  key={suggestion.transaction_id}
                  suggestion={suggestion}
                  categories={categories}
                  checked={checked[suggestion.transaction_id] ?? false}
                  chosenId={chosen[suggestion.transaction_id] ?? suggestion.category_id}
                  onToggle={(value) =>
                    setChecked((prev) => ({ ...prev, [suggestion.transaction_id]: value }))
                  }
                  onChoose={(categoryId) =>
                    setChosen((prev) => ({ ...prev, [suggestion.transaction_id]: categoryId }))
                  }
                />
              ))}
            </div>

            {suggest.data && suggest.data.skipped.length > 0 && (
              <div className={styles.skippedBlock}>
                <p className={styles.skippedTitle}>
                  Not suggested ({suggest.data.skipped.length})
                </p>
                {/* Shown rather than dropped: a user who asked about fifteen
                    transactions and sees twelve rows needs to know which three
                    are missing and why. */}
                {suggest.data.skipped.map((item) => (
                  <div key={item.transaction_id} className={styles.skippedItem}>
                    {item.description ?? "(no description)"} — {item.reason}
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        <div className={styles.reviewActions}>
          <Button variant="ghost" onClick={onClose} disabled={apply.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={submit}
            loading={apply.isPending}
            disabled={acceptedCount === 0}
          >
            Apply {acceptedCount > 0 ? acceptedCount : ""}{" "}
            {acceptedCount === 1 ? "category" : "categories"}
          </Button>
        </div>
      </div>
    </div>
  );
}

interface SuggestionRowProps {
  suggestion: CategorySuggestion;
  categories: Category[];
  checked: boolean;
  chosenId: number;
  onToggle: (value: boolean) => void;
  onChoose: (categoryId: number) => void;
}

function SuggestionRow({
  suggestion,
  categories,
  checked,
  chosenId,
  onToggle,
  onChoose,
}: SuggestionRowProps): JSX.Element {
  // Only categories of the matching side are offered. The server rejects a
  // mismatch with a 422 anyway; filtering here means the user never builds one
  // to be rejected — a constraint enforced in the UI *and* the API, which is
  // the right number of places for a rule this load-bearing.
  const options = categories.filter((category) => category.type === suggestion.type);

  const percent = Math.round(suggestion.confidence * 100);

  return (
    <div className={`${styles.reviewRow} ${checked ? styles.reviewRowChecked : ""}`}>
      <input
        type="checkbox"
        className={styles.reviewCheckbox}
        checked={checked}
        onChange={(event) => onToggle(event.target.checked)}
        aria-label={`Apply ${suggestion.category_name} to ${
          suggestion.description ?? "this transaction"
        }`}
      />

      <div className={styles.reviewMain}>
        <span
          className={styles.reviewDescription}
          // The full description in a tooltip, because the visible text is
          // truncated and bank descriptions carry the detail at the end.
          title={suggestion.description ?? undefined}
        >
          {suggestion.description ?? "(no description)"}
        </span>
        <span className={styles.reviewMeta}>
          {formatDate(suggestion.occurred_on)} ·{" "}
          {formatMoney(suggestion.amount, { signed: false })}
        </span>
        {suggestion.reasoning && (
          <span className={styles.reviewReasoning}>{suggestion.reasoning}</span>
        )}
      </div>

      <div className={styles.reviewChoice}>
        <span className={styles.confidence}>
          <span className={styles.confidenceBar}>
            <span
              className={`${styles.confidenceFill} ${
                suggestion.recommended ? styles.confidenceHigh : styles.confidenceLow
              }`}
              style={{ width: `${percent}%` }}
            />
          </span>
          {percent}%
        </span>

        <BareSelect
          value={chosenId}
          onChange={(event) => onChoose(Number(event.target.value))}
          aria-label="Category"
        >
          {options.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </BareSelect>
      </div>
    </div>
  );
}
