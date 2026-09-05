import { useMemo, useState } from "react";

import { useAccounts, useCategories, useTransactions } from "@/api/queries";
import { CategorizeReview } from "@/components/ai/CategorizeReview";
import { FilterBar } from "@/components/filters/FilterBar";
import { TransactionTable } from "@/components/transactions/TransactionTable";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { SparkleIcon } from "@/components/ui/icons";
import { useFilters } from "@/hooks/useFilters";
import { Onboarding } from "./Onboarding";
import styles from "./pages.module.css";

export function TransactionsPage(): JSX.Element {
  const { filters, setFilters, reset, isFiltered, transactionParams } = useFilters();
  const [reviewOpen, setReviewOpen] = useState(false);

  const accounts = useAccounts();
  const categories = useCategories();
  const transactions = useTransactions(transactionParams);

  // The button is offered only when there is something for it to do. A
  // permanently-visible "Auto-categorise" that reports "nothing to categorise"
  // teaches the user to ignore it; showing the count makes it an answer to a
  // question they already have.
  //
  // Counted from the page currently loaded rather than with a dedicated
  // endpoint. That makes it a lower bound — there may be older uncategorised
  // rows beyond this page — which is why the label says "on this page" rather
  // than claiming a total the number does not support.
  const uncategorisedOnPage = useMemo(
    () => (transactions.data ?? []).filter((row) => row.category_id === null).length,
    [transactions.data],
  );

  if (accounts.isLoading) {
    return <div className={styles.page} />;
  }

  if (accounts.data && accounts.data.length === 0) {
    return <Onboarding />;
  }

  const loadError = accounts.error ?? transactions.error;

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
        // `isFetching` while a search term is settling is what puts the spinner
        // in the search box — the visible answer to "is it doing anything?"
        // during the debounce pause.
        searching={transactions.isFetching}
      />

      {uncategorisedOnPage > 0 && (
        <div className={styles.aiActionRow}>
          <span className={styles.aiActionNote}>
            {uncategorisedOnPage} uncategorised{" "}
            {uncategorisedOnPage === 1 ? "transaction" : "transactions"} on this page
          </span>
          <Button variant="secondary" onClick={() => setReviewOpen(true)}>
            <SparkleIcon size={15} />
            Suggest categories
          </Button>
        </div>
      )}

      <TransactionTable
        transactions={transactions.data ?? []}
        accounts={accounts.data ?? []}
        categories={categories.data ?? []}
        // `isLoading` is true only on the very first fetch for a given filter
        // set with nothing cached; `isFetching` covers every background
        // refetch. Using the second here would flash skeletons on every
        // keystroke of a search.
        loading={transactions.isLoading}
        fetching={transactions.isFetching && !transactions.isLoading}
        isFiltered={isFiltered}
        onClearFilters={reset}
      />

      {/* Suggestions are requested when this opens and nothing is written until
          the user applies them. The account filter is passed through so the
          suggestions match the ledger the user is looking at rather than
          silently reaching across every account. */}
      <CategorizeReview
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        categories={categories.data ?? []}
        accountId={filters.accountId ?? undefined}
      />
    </div>
  );
}
