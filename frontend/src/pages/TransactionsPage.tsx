import { useAccounts, useCategories, useTransactions } from "@/api/queries";
import { FilterBar } from "@/components/filters/FilterBar";
import { TransactionTable } from "@/components/transactions/TransactionTable";
import { Alert } from "@/components/ui/Alert";
import { useFilters } from "@/hooks/useFilters";
import { Onboarding } from "./Onboarding";
import styles from "./pages.module.css";

export function TransactionsPage(): JSX.Element {
  const { filters, setFilters, reset, isFiltered, transactionParams } = useFilters();

  const accounts = useAccounts();
  const categories = useCategories();
  const transactions = useTransactions(transactionParams);

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
    </div>
  );
}
