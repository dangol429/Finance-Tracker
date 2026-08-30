import { useMemo } from "react";

import type {
  Account,
  Category,
  Transaction,
  TransactionCreate,
  TransactionType,
} from "@/api/types";
import { Button } from "@/components/ui/Button";
import { BareInput, BareSelect } from "@/components/ui/Field";
import { CheckIcon, CloseIcon } from "@/components/ui/icons";
import { today } from "@/lib/format";
import styles from "./transactions.module.css";

export interface TransactionDraft {
  occurred_on: string;
  description: string;
  category_id: string;
  account_id: string;
  type: TransactionType;
  amount: string;
}

export function draftFromTransaction(
  transaction: Transaction,
  fallbackAccountId: number,
): TransactionDraft {
  return {
    occurred_on: transaction.occurred_on,
    description: transaction.description ?? "",
    category_id: transaction.category_id ? String(transaction.category_id) : "",
    account_id: String(transaction.account_id || fallbackAccountId),
    type: transaction.type,
    amount: transaction.amount,
  };
}

export function emptyDraft(accountId: number): TransactionDraft {
  return {
    occurred_on: today(),
    description: "",
    category_id: "",
    account_id: String(accountId),
    type: "expense",
    amount: "",
  };
}

/**
 * Validate a draft into a payload, or return why it cannot be sent.
 *
 * Mirrors the backend's rules deliberately and incompletely: `amount > 0`, two
 * decimal places, a date that is not in the future. The server enforces all of
 * these itself — it must, since anything can POST to it — so this exists purely
 * to answer in the same keystroke rather than after a round trip. Where the two
 * disagree the server wins, and its message is what gets shown.
 */
export function validateDraft(
  draft: TransactionDraft,
): { ok: true; payload: TransactionCreate } | { ok: false; error: string } {
  const amount = draft.amount.trim();
  if (!amount) return { ok: false, error: "Amount is required." };

  const parsed = Number(amount);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return { ok: false, error: "Amount must be a positive number." };
  }
  if (!/^\d+(\.\d{1,2})?$/.test(amount)) {
    return { ok: false, error: "Amount can have at most two decimal places." };
  }
  if (!draft.occurred_on) return { ok: false, error: "Date is required." };
  if (draft.occurred_on > today()) {
    return { ok: false, error: "Date cannot be in the future." };
  }
  if (!draft.account_id) return { ok: false, error: "Pick an account." };

  return {
    ok: true,
    payload: {
      account_id: Number(draft.account_id),
      category_id: draft.category_id ? Number(draft.category_id) : null,
      // Sent as a string, matching the API's money contract — `Number` here
      // would reintroduce the float round-trip the string exists to avoid.
      amount,
      type: draft.type,
      occurred_on: draft.occurred_on,
      description: draft.description.trim() || null,
    },
  };
}

interface TransactionFormRowProps {
  draft: TransactionDraft;
  onChange: (draft: TransactionDraft) => void;
  onSubmit: () => void;
  onCancel: () => void;
  accounts: Account[];
  categories: Category[];
  saving?: boolean;
  submitLabel?: string;
}

/**
 * The inline add/edit row.
 *
 * One component for both, because they are the same form over the same fields —
 * a separate "edit" version would be a copy that drifts. What differs is only
 * where the draft came from and which mutation the parent fires.
 *
 * **Category options are filtered by the selected type.** The API rejects an
 * expense filed under an income category with a 422, so offering the choice at
 * all is offering something that cannot work. Changing the type clears a
 * category that has just become invalid, rather than leaving a selection that
 * will fail on save.
 */
export function TransactionFormRow({
  draft,
  onChange,
  onSubmit,
  onCancel,
  accounts,
  categories,
  saving = false,
  submitLabel = "Save",
}: TransactionFormRowProps): JSX.Element {
  const validCategories = useMemo(
    () => categories.filter((category) => category.type === draft.type),
    [categories, draft.type],
  );

  function update(changes: Partial<TransactionDraft>): void {
    const next = { ...draft, ...changes };

    if (changes.type && changes.type !== draft.type) {
      const stillValid = categories.some(
        (category) =>
          String(category.id) === next.category_id && category.type === changes.type,
      );
      if (!stillValid) next.category_id = "";
    }

    onChange(next);
  }

  // Enter saves, Escape cancels — the two keys anyone expects from an inline
  // editor. Without them the row can only be dismissed with the mouse, which
  // makes rapid entry (the reason for an inline form) impossible.
  function handleKeyDown(event: React.KeyboardEvent): void {
    if (event.key === "Enter") {
      event.preventDefault();
      onSubmit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
    }
  }

  return (
    <tr className={styles.formRow} onKeyDown={handleKeyDown}>
      <td>
        <BareInput
          type="date"
          value={draft.occurred_on}
          max={today()}
          onChange={(event) => update({ occurred_on: event.target.value })}
          aria-label="Date"
          autoFocus
        />
      </td>
      <td>
        <BareInput
          type="text"
          value={draft.description}
          onChange={(event) => update({ description: event.target.value })}
          placeholder="Description"
          aria-label="Description"
          maxLength={255}
        />
      </td>
      <td>
        <BareSelect
          value={draft.category_id}
          onChange={(event) => update({ category_id: event.target.value })}
          aria-label="Category"
        >
          <option value="">Uncategorized</option>
          {validCategories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </BareSelect>
      </td>
      <td>
        <BareSelect
          value={draft.account_id}
          onChange={(event) => update({ account_id: event.target.value })}
          aria-label="Account"
        >
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.name}
            </option>
          ))}
        </BareSelect>
      </td>
      <td>
        <div style={{ display: "flex", gap: "var(--space-1)" }}>
          <BareSelect
            value={draft.type}
            onChange={(event) => update({ type: event.target.value as TransactionType })}
            aria-label="Type"
            style={{ width: 92 }}
          >
            <option value="expense">Out</option>
            <option value="income">In</option>
          </BareSelect>
          <BareInput
            // `inputMode="decimal"` opens the numeric keypad on a phone without
            // the spinner arrows and locale quirks `type="number"` brings —
            // notably that a `type="number"` field silently reports "" for
            // input the browser considers invalid, so you cannot show the user
            // what they typed.
            type="text"
            inputMode="decimal"
            value={draft.amount}
            onChange={(event) => update({ amount: event.target.value })}
            placeholder="0.00"
            aria-label="Amount"
            style={{ textAlign: "right" }}
          />
        </div>
      </td>
      <td>
        <div className={styles.formActions}>
          <Button
            variant="primary"
            size="sm"
            iconOnly
            onClick={onSubmit}
            loading={saving}
            aria-label={submitLabel}
            title={`${submitLabel} (Enter)`}
          >
            {!saving && <CheckIcon size={15} />}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            iconOnly
            onClick={onCancel}
            disabled={saving}
            aria-label="Cancel"
            title="Cancel (Esc)"
          >
            <CloseIcon size={15} />
          </Button>
        </div>
      </td>
    </tr>
  );
}

interface TransactionFormPanelProps extends Omit<TransactionFormRowProps, "submitLabel"> {
  submitLabel?: string;
}

/**
 * The same form, stacked, for narrow screens.
 *
 * Shares the draft type, the change handler and `validateDraft` with the row
 * version — only the layout differs. Duplicating the *validation* here is what
 * would make this a maintenance problem; duplicating the markup is unavoidable,
 * because a `<tr>` cannot be re-flowed into a column and cannot legally exist
 * outside a table.
 */
export function TransactionFormPanel({
  draft,
  onChange,
  onSubmit,
  onCancel,
  accounts,
  categories,
  saving = false,
  submitLabel = "Save",
}: TransactionFormPanelProps): JSX.Element {
  const validCategories = categories.filter((category) => category.type === draft.type);

  function update(changes: Partial<TransactionDraft>): void {
    const next = { ...draft, ...changes };
    if (changes.type && changes.type !== draft.type) {
      const stillValid = categories.some(
        (category) =>
          String(category.id) === next.category_id && category.type === changes.type,
      );
      if (!stillValid) next.category_id = "";
    }
    onChange(next);
  }

  return (
    <div
      className={styles.panel}
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel();
      }}
    >
      <BareInput
        type="text"
        value={draft.description}
        onChange={(event) => update({ description: event.target.value })}
        placeholder="Description"
        aria-label="Description"
        maxLength={255}
        autoFocus
      />

      <div className={styles.panelRow}>
        <BareSelect
          value={draft.type}
          onChange={(event) => update({ type: event.target.value as TransactionType })}
          aria-label="Type"
        >
          <option value="expense">Money out</option>
          <option value="income">Money in</option>
        </BareSelect>
        <BareInput
          type="text"
          inputMode="decimal"
          value={draft.amount}
          onChange={(event) => update({ amount: event.target.value })}
          placeholder="0.00"
          aria-label="Amount"
          style={{ textAlign: "right" }}
        />
      </div>

      <div className={styles.panelRow}>
        <BareInput
          type="date"
          value={draft.occurred_on}
          max={today()}
          onChange={(event) => update({ occurred_on: event.target.value })}
          aria-label="Date"
        />
        <BareSelect
          value={draft.category_id}
          onChange={(event) => update({ category_id: event.target.value })}
          aria-label="Category"
        >
          <option value="">Uncategorized</option>
          {validCategories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </BareSelect>
      </div>

      <BareSelect
        value={draft.account_id}
        onChange={(event) => update({ account_id: event.target.value })}
        aria-label="Account"
      >
        {accounts.map((account) => (
          <option key={account.id} value={account.id}>
            {account.name}
          </option>
        ))}
      </BareSelect>

      <div className={styles.panelActions}>
        <Button variant="primary" onClick={onSubmit} loading={saving} fullWidth>
          {submitLabel}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
