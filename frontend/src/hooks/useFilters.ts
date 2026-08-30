/**
 * The dashboard's filter state, kept in the URL.
 *
 * **Why the URL and not `useState`.** A filtered view is a thing people want to
 * share, bookmark and reload. Keeping the state in a component means a refresh
 * silently resets to the last 6 months, the back button leaves the page
 * entirely instead of undoing a filter, and "look at this" requires a
 * screenshot. Keeping it in the query string makes all three work for free, and
 * it makes the app's state inspectable — the URL says what you are looking at.
 *
 * It also removes a whole category of bug. With one source of truth, the table
 * and the charts cannot disagree about what is being shown, because they read
 * the same parameters rather than each holding their own copy that has to be
 * kept in step.
 *
 * **What is shared and what is not.** The date range and account narrow
 * *everything* — table and charts alike, because the aggregation endpoints take
 * exactly those parameters. Category and search narrow only the ledger table:
 * the API's `/summary/by-category` has no `category_id` (filtering a category
 * breakdown to a single category is degenerate) and no `q`. That split is real
 * rather than an omission, and the UI labels it, because a filter that silently
 * applies to half the screen is worse than one that says which half.
 */

import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { monthsAgo, today } from "@/lib/format";
import type { SummaryFilters, TransactionFilters, TransactionType } from "@/api/types";

export interface Filters {
  dateFrom: string;
  dateTo: string;
  accountId: number | null;
  categoryId: number | null;
  type: TransactionType | null;
  search: string;
}

/** Six months back, to the first of that month, through today. */
function defaultRange(): { dateFrom: string; dateTo: string } {
  return { dateFrom: monthsAgo(5), dateTo: today() };
}

function parseNumber(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function parseType(value: string | null): TransactionType | null {
  return value === "income" || value === "expense" ? value : null;
}

export interface UseFiltersResult {
  filters: Filters;
  /** Merge a partial change. Pass `null` to clear a single filter. */
  setFilters: (changes: Partial<Filters>) => void;
  reset: () => void;
  /** True when anything differs from the defaults — drives the "Clear" button. */
  isFiltered: boolean;
  /** Shaped for `GET /transactions`. */
  transactionParams: TransactionFilters;
  /** Shaped for `/summary/*` — the subset those endpoints accept. */
  summaryParams: SummaryFilters;
}

export function useFilters(): UseFiltersResult {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters = useMemo<Filters>(() => {
    const fallback = defaultRange();
    return {
      dateFrom: searchParams.get("from") ?? fallback.dateFrom,
      dateTo: searchParams.get("to") ?? fallback.dateTo,
      accountId: parseNumber(searchParams.get("account")),
      categoryId: parseNumber(searchParams.get("category")),
      type: parseType(searchParams.get("type")),
      search: searchParams.get("q") ?? "",
    };
  }, [searchParams]);

  const setFilters = useCallback(
    (changes: Partial<Filters>) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          const apply = (key: string, value: string | number | null | undefined) => {
            // An absent parameter and an empty one should not be two ways of
            // saying the same thing — the URL stays short and readable, and
            // `parseNumber` never has to consider `""`.
            if (value === null || value === undefined || value === "") next.delete(key);
            else next.set(key, String(value));
          };

          if ("dateFrom" in changes) apply("from", changes.dateFrom);
          if ("dateTo" in changes) apply("to", changes.dateTo);
          if ("accountId" in changes) apply("account", changes.accountId);
          if ("categoryId" in changes) apply("category", changes.categoryId);
          if ("type" in changes) apply("type", changes.type);
          if ("search" in changes) apply("q", changes.search);

          return next;
        },
        // `replace` so typing in the search box does not push twenty history
        // entries that the back button then has to be pressed twenty times to
        // escape. The trade-off is that a filter change is not individually
        // undoable, which is the right call: the URL is still shareable, and a
        // back button that appears broken is worse than one that skips a step.
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const reset = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true });
  }, [setSearchParams]);

  const isFiltered = useMemo(() => {
    const fallback = defaultRange();
    return (
      filters.dateFrom !== fallback.dateFrom ||
      filters.dateTo !== fallback.dateTo ||
      filters.accountId !== null ||
      filters.categoryId !== null ||
      filters.type !== null ||
      filters.search !== ""
    );
  }, [filters]);

  // Built with `undefined` rather than `null` for absent values, because that
  // is what the client's param serializer skips — and because these objects are
  // query keys, where `{a: undefined}` and `{}` must not be two different cache
  // entries for the same request.
  const transactionParams = useMemo<TransactionFilters>(
    () => ({
      date_from: filters.dateFrom,
      date_to: filters.dateTo,
      account_id: filters.accountId ?? undefined,
      category_id: filters.categoryId ?? undefined,
      type: filters.type ?? undefined,
      q: filters.search || undefined,
      limit: 100,
    }),
    [filters],
  );

  const summaryParams = useMemo<SummaryFilters>(
    () => ({
      date_from: filters.dateFrom,
      date_to: filters.dateTo,
      account_id: filters.accountId ?? undefined,
    }),
    [filters],
  );

  return { filters, setFilters, reset, isFiltered, transactionParams, summaryParams };
}
