import { useMemo, useState } from "react";

import {
  useCreateTransaction,
  useDeleteTransaction,
  useUpdateTransaction,
} from "@/api/queries";
import type { Account, Category, Transaction } from "@/api/types";
import { Alert, Badge } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmDialog } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Skeleton";
import { EditIcon, LedgerIcon, PlusIcon, TrashIcon } from "@/components/ui/icons";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { formatDate, formatMoney } from "@/lib/format";
import {
  TransactionFormPanel,
  TransactionFormRow,
  draftFromTransaction,
  emptyDraft,
  validateDraft,
  type TransactionDraft,
} from "./TransactionForm";
import styles from "./transactions.module.css";

interface TransactionTableProps {
  transactions: Transaction[];
  accounts: Account[];
  categories: Category[];
  loading: boolean;
  /** A background refetch — dims the body without unmounting it. */
  fetching: boolean;
  /** True when filters are narrowing the list, so "empty" means something else. */
  isFiltered: boolean;
  onClearFilters: () => void;
}

export function TransactionTable({
  transactions,
  accounts,
  categories,
  loading,
  fetching,
  isFiltered,
  onClearFilters,
}: TransactionTableProps): JSX.Element {
  const isNarrow = useMediaQuery("(max-width: 720px)");

  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<TransactionDraft | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Transaction | null>(null);

  const create = useCreateTransaction();
  const update = useUpdateTransaction();
  const remove = useDeleteTransaction();

  // Lookups rather than `.find()` inside the render loop: with 100 rows and a
  // handful of accounts that difference is immaterial, but the map also makes
  // the "deleted category" case explicit instead of an undefined that has to be
  // guarded at each use.
  const accountsById = useMemo(
    () => new Map(accounts.map((account) => [account.id, account])),
    [accounts],
  );
  const categoriesById = useMemo(
    () => new Map(categories.map((category) => [category.id, category])),
    [categories],
  );

  const defaultAccountId = accounts[0]?.id ?? 0;

  function startAdd(): void {
    setEditingId(null);
    setFormError(null);
    setDraft(emptyDraft(defaultAccountId));
    setAdding(true);
  }

  function startEdit(transaction: Transaction): void {
    setAdding(false);
    setFormError(null);
    setDraft(draftFromTransaction(transaction, defaultAccountId));
    setEditingId(transaction.id);
  }

  function cancelForm(): void {
    setAdding(false);
    setEditingId(null);
    setDraft(null);
    setFormError(null);
  }

  function submitForm(): void {
    if (!draft) return;

    const result = validateDraft(draft);
    if (!result.ok) {
      setFormError(result.error);
      return;
    }

    setFormError(null);

    if (adding) {
      // The form closes immediately rather than waiting for the server. That is
      // the whole point of the optimistic update: the row is already in the
      // table, so keeping the editor open would show the same transaction
      // twice. A failure rolls the row back and surfaces the error below.
      create.mutate(result.payload, {
        onError: (error) => setFormError(error.message),
      });
      cancelForm();
      return;
    }

    if (editingId !== null) {
      update.mutate(
        { id: editingId, changes: result.payload },
        { onError: (error) => setFormError(error.message) },
      );
      cancelForm();
    }
  }

  function confirmDelete(): void {
    if (!pendingDelete) return;
    remove.mutate(pendingDelete.id);
    setPendingDelete(null);
  }

  const mutationError = create.error ?? update.error ?? remove.error;

  // --- Empty and loading states ---------------------------------------------

  if (loading) {
    return (
      <Card flush>
        <div style={{ padding: "var(--space-4)" }}>
          {Array.from({ length: 6 }, (_, index) => (
            <div
              key={index}
              style={{
                display: "flex",
                gap: "var(--space-4)",
                padding: "var(--space-3) 0",
              }}
            >
              <Skeleton width="90px" />
              <Skeleton width="40%" />
              <Skeleton width="110px" />
              <Skeleton width="90px" />
            </div>
          ))}
        </div>
      </Card>
    );
  }

  if (accounts.length === 0) {
    return (
      <Card>
        <EmptyState
          icon={<LedgerIcon size={20} />}
          title="Create an account first"
          body="A transaction has to belong to an account — a current account, a card, cash. Add one and this table becomes usable."
        />
      </Card>
    );
  }

  const isEmpty = transactions.length === 0 && !adding;

  return (
    <>
      <Card flush>
        <div className={styles.addBar}>
          <span className={styles.count}>
            {transactions.length} transaction{transactions.length === 1 ? "" : "s"}
            {fetching && " · updating…"}
          </span>
          <Button variant="primary" size="sm" onClick={startAdd} disabled={adding}>
            <PlusIcon size={15} />
            Add transaction
          </Button>
        </div>

        {mutationError && (
          <div style={{ padding: "var(--space-3) var(--space-4)" }}>
            <Alert>{mutationError.message}</Alert>
          </div>
        )}
        {formError && <div className={styles.formError}>{formError}</div>}

        {/* On a phone the editor is a stacked panel above the list rather than
            a row inside a table that does not exist at this width. */}
        {isNarrow && draft && (adding || editingId !== null) && (
          <TransactionFormPanel
            draft={draft}
            onChange={setDraft}
            onSubmit={submitForm}
            onCancel={cancelForm}
            accounts={accounts}
            categories={categories}
            submitLabel={adding ? "Add transaction" : "Save changes"}
          />
        )}

        {isEmpty && !(isNarrow && draft) ? (
          isFiltered ? (
            <EmptyState
              icon={<LedgerIcon size={20} />}
              title="Nothing matches these filters"
              body="There are transactions here, just not in this slice. Widen the date range or clear the filters to see them."
              action={
                <Button variant="secondary" size="sm" onClick={onClearFilters}>
                  Clear filters
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={<LedgerIcon size={20} />}
              title="No transactions yet"
              body="Add one by hand to get started, or import a bank statement CSV and fill in months of history at once."
              action={
                <Button variant="primary" size="sm" onClick={startAdd}>
                  <PlusIcon size={15} />
                  Add your first transaction
                </Button>
              }
            />
          )
        ) : isNarrow ? (
          <CardList
            // The row being edited is hidden from the list: its panel is open
            // directly above, and showing both is two copies of one row.
            transactions={transactions.filter((row) => row.id !== editingId)}
            accountsById={accountsById}
            categoriesById={categoriesById}
            fetching={fetching}
            onEdit={startEdit}
            onDelete={setPendingDelete}
          />
        ) : (
          <div className={styles.wrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.colDate}>Date</th>
                  <th className={styles.colDescription}>Description</th>
                  <th className={styles.colCategory}>Category</th>
                  <th className={styles.colAccount}>Account</th>
                  <th className={`${styles.colAmount} ${styles.amountCell}`}>Amount</th>
                  <th className={styles.colActions}>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className={fetching ? styles.stale : undefined}>
                {adding && draft && (
                  <TransactionFormRow
                    draft={draft}
                    onChange={setDraft}
                    onSubmit={submitForm}
                    onCancel={cancelForm}
                    accounts={accounts}
                    categories={categories}
                    submitLabel="Add"
                  />
                )}

                {transactions.map((transaction) =>
                  editingId === transaction.id && draft ? (
                    <TransactionFormRow
                      key={transaction.id}
                      draft={draft}
                      onChange={setDraft}
                      onSubmit={submitForm}
                      onCancel={cancelForm}
                      accounts={accounts}
                      categories={categories}
                    />
                  ) : (
                    <Row
                      key={transaction.id}
                      transaction={transaction}
                      account={accountsById.get(transaction.account_id)}
                      category={
                        transaction.category_id
                          ? categoriesById.get(transaction.category_id)
                          : undefined
                      }
                      onEdit={() => startEdit(transaction)}
                      onDelete={() => setPendingDelete(transaction)}
                    />
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this transaction?"
        body={
          pendingDelete ? (
            <>
              {formatMoney(pendingDelete.signed_amount, { signed: true })} on{" "}
              {formatDate(pendingDelete.occurred_on, { withYear: true })}
              {pendingDelete.description ? ` — ${pendingDelete.description}` : ""}. This
              cannot be undone.
            </>
          ) : null
        }
        confirmLabel="Delete"
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </>
  );
}

// --- Rows -------------------------------------------------------------------

interface RowProps {
  transaction: Transaction;
  account: Account | undefined;
  category: Category | undefined;
  onEdit: () => void;
  onDelete: () => void;
}

function Row({ transaction, account, category, onEdit, onDelete }: RowProps): JSX.Element {
  // A negative id is an optimistic row that the server has not confirmed yet
  // (see `nextTemporaryId` in queries.ts). Editing or deleting one would send a
  // request for an id that does not exist, so the actions are withheld for the
  // fraction of a second it takes to be replaced by the real row.
  const isPending = transaction.id < 0;
  const isIncome = transaction.type === "income";

  return (
    <tr className={`${styles.row} ${isPending ? styles.rowPending : ""}`}>
      <td className={styles.dateCell}>{formatDate(transaction.occurred_on)}</td>
      <td className={styles.descriptionCell} title={transaction.description ?? undefined}>
        {transaction.description || <span className={styles.muted}>No description</span>}
      </td>
      <td>
        {category ? (
          <Badge tone={category.type === "income" ? "income" : "neutral"}>
            {category.name}
          </Badge>
        ) : (
          <span className={styles.muted}>Uncategorized</span>
        )}
      </td>
      <td className={styles.descriptionCell}>{account?.name ?? "—"}</td>
      <td
        className={`${styles.amountCell} ${
          isIncome ? styles.amountIncome : styles.amountExpense
        }`}
      >
        {formatMoney(transaction.signed_amount, { signed: true })}
      </td>
      <td>
        <div className={styles.actions}>
          <Button
            variant="ghost"
            size="sm"
            iconOnly
            onClick={onEdit}
            disabled={isPending}
            aria-label="Edit transaction"
          >
            <EditIcon size={15} />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            iconOnly
            onClick={onDelete}
            disabled={isPending}
            aria-label="Delete transaction"
          >
            <TrashIcon size={15} />
          </Button>
        </div>
      </td>
    </tr>
  );
}

interface CardListProps {
  transactions: Transaction[];
  accountsById: Map<number, Account>;
  categoriesById: Map<number, Category>;
  fetching: boolean;
  onEdit: (transaction: Transaction) => void;
  onDelete: (transaction: Transaction) => void;
}

/** The phone layout: one card per transaction instead of a squeezed table. */
function CardList({
  transactions,
  accountsById,
  categoriesById,
  fetching,
  onEdit,
  onDelete,
}: CardListProps): JSX.Element {
  return (
    <div className={`${styles.cards} ${fetching ? styles.stale : ""}`}>
      {transactions.map((transaction) => {
        const category = transaction.category_id
          ? categoriesById.get(transaction.category_id)
          : undefined;
        const isIncome = transaction.type === "income";

        return (
          <div
            className={`${styles.card} ${transaction.id < 0 ? styles.rowPending : ""}`}
            key={transaction.id}
          >
            <div className={styles.cardTop}>
              <span className={styles.cardDescription}>
                {transaction.description || (
                  <span className={styles.muted}>No description</span>
                )}
              </span>
            </div>

            <span
              className={`${styles.cardAmount} ${
                isIncome ? styles.amountIncome : styles.amountExpense
              }`}
            >
              {formatMoney(transaction.signed_amount, { signed: true })}
            </span>

            <div className={styles.cardMeta}>
              <span>{formatDate(transaction.occurred_on)}</span>
              <span>·</span>
              <span>{category?.name ?? "Uncategorized"}</span>
              <span>·</span>
              <span>{accountsById.get(transaction.account_id)?.name ?? "—"}</span>
            </div>

            <div className={styles.cardActions}>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => onEdit(transaction)}
                disabled={transaction.id < 0}
              >
                <EditIcon size={14} />
                Edit
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onDelete(transaction)}
                disabled={transaction.id < 0}
              >
                <TrashIcon size={14} />
                Delete
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
