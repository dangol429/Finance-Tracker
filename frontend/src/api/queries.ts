/**
 * Every server call the app makes, as TanStack Query hooks.
 *
 * **Query keys are the whole design.** A key is both a cache address and a
 * dependency declaration: `["transactions", filters]` means "this data is a
 * function of those filters", so changing a filter refetches automatically and
 * changing back serves the previous result from cache. It is also what
 * invalidation targets — `invalidateQueries({queryKey: ["transactions"]})`
 * matches every filter combination by prefix, which is exactly what you want
 * after a write, because you cannot know which cached pages the new row belongs
 * on.
 *
 * The keys are built by `keys` below rather than written inline at call sites.
 * A key typo doesn't error — it silently creates a second cache entry, so the
 * list stops updating after a write and nothing anywhere says why.
 *
 * **Why the mutations invalidate summaries too.** Adding a transaction changes
 * the monthly chart, the category donut and the stat cards, none of which the
 * mutation touched. Forgetting that is how a dashboard ends up showing a table
 * that includes a new row above charts that don't.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { request } from "./client";
import type {
  Account,
  AccountCreate,
  AiQuery,
  ApplyCategoriesResult,
  CategorizeParams,
  Category,
  CategoryAssignment,
  CategoryBreakdown,
  CategoryCreate,
  CategorySuggestions,
  ImportSummary,
  IncomeVsExpense,
  MonthlyInsight,
  MonthlySummary,
  SummaryFilters,
  Transaction,
  TransactionCreate,
  TransactionFilters,
  TransactionType,
  TransactionUpdate,
  User,
} from "./types";

export const keys = {
  me: ["me"] as const,
  accounts: ["accounts"] as const,
  categories: (type?: TransactionType) => ["categories", type ?? "all"] as const,
  transactions: (filters: TransactionFilters) => ["transactions", filters] as const,
  summaryMonthly: (filters: SummaryFilters) => ["summary", "monthly", filters] as const,
  summaryByCategory: (filters: SummaryFilters, type: TransactionType) =>
    ["summary", "by-category", type, filters] as const,
  summaryTotals: (filters: SummaryFilters) => ["summary", "totals", filters] as const,
  monthlyInsight: (month: string, accountId?: number) =>
    ["ai", "insight", month, accountId ?? "all"] as const,
};

/** Everything a write can invalidate, in one place so no mutation forgets one. */
function invalidateAfterWrite(client: ReturnType<typeof useQueryClient>): void {
  void client.invalidateQueries({ queryKey: ["transactions"] });
  void client.invalidateQueries({ queryKey: ["summary"] });
}

// --- Reads ------------------------------------------------------------------

export function useMe(enabled: boolean): UseQueryResult<User> {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => request<User>("/auth/me"),
    enabled,
    // A 401 here means the stored token is dead. Retrying three times just
    // delays the redirect to the login page by a couple of seconds.
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export function useAccounts(): UseQueryResult<Account[]> {
  return useQuery({
    queryKey: keys.accounts,
    queryFn: () => request<Account[]>("/accounts"),
    // Accounts are a short, hand-maintained list that changes rarely. Five
    // minutes of staleness means the filter dropdown doesn't refetch every
    // time the user navigates back to the dashboard.
    staleTime: 5 * 60 * 1000,
  });
}

export function useCategories(type?: TransactionType): UseQueryResult<Category[]> {
  return useQuery({
    queryKey: keys.categories(type),
    queryFn: () => request<Category[]>("/categories", { params: { type } }),
    staleTime: 5 * 60 * 1000,
  });
}

export function useTransactions(
  filters: TransactionFilters,
): UseQueryResult<Transaction[]> {
  return useQuery({
    queryKey: keys.transactions(filters),
    queryFn: () => request<Transaction[]>("/transactions", { params: { ...filters } }),
    // Keeps the previous page rendered while the next one loads, so changing a
    // filter dims the table instead of collapsing it to a skeleton and back.
    // This is the single setting that makes filtering feel instant rather than
    // like a page load — see `isFetching` handling in the table.
    placeholderData: (previous) => previous,
  });
}

export function useMonthlySummary(filters: SummaryFilters): UseQueryResult<MonthlySummary> {
  return useQuery({
    queryKey: keys.summaryMonthly(filters),
    queryFn: () => request<MonthlySummary>("/summary/monthly", { params: { ...filters } }),
    placeholderData: (previous) => previous,
  });
}

export function useCategoryBreakdown(
  filters: SummaryFilters,
  type: TransactionType,
): UseQueryResult<CategoryBreakdown> {
  return useQuery({
    queryKey: keys.summaryByCategory(filters, type),
    queryFn: () =>
      request<CategoryBreakdown>("/summary/by-category", { params: { ...filters, type } }),
    placeholderData: (previous) => previous,
  });
}

export function useTotals(filters: SummaryFilters): UseQueryResult<IncomeVsExpense> {
  return useQuery({
    queryKey: keys.summaryTotals(filters),
    queryFn: () =>
      request<IncomeVsExpense>("/summary/income-vs-expense", { params: { ...filters } }),
    placeholderData: (previous) => previous,
  });
}

// --- Writes -----------------------------------------------------------------

/**
 * Context carried from `onMutate` to `onError` so a failed optimistic update
 * can be rolled back to exactly what was on screen before.
 */
interface RollbackContext {
  snapshots: Array<[readonly unknown[], Transaction[] | undefined]>;
}

/**
 * Cancel in-flight transaction queries and snapshot every cached list.
 *
 * The `cancelQueries` call is the part that is easy to omit and painful to
 * debug: a refetch that was already in flight when the user hit save will land
 * *after* the optimistic update and overwrite it with server data that predates
 * the change. The row then flickers back to its old value for a moment, which
 * looks exactly like the save having failed.
 */
async function beginOptimistic(
  client: ReturnType<typeof useQueryClient>,
): Promise<RollbackContext> {
  await client.cancelQueries({ queryKey: ["transactions"] });
  const snapshots = client.getQueriesData<Transaction[]>({ queryKey: ["transactions"] });
  return { snapshots: snapshots.map(([key, data]) => [key, data]) };
}

function rollback(
  client: ReturnType<typeof useQueryClient>,
  context: RollbackContext | undefined,
): void {
  context?.snapshots.forEach(([key, data]) => {
    client.setQueryData(key, data);
  });
}

/**
 * The id given to a row that exists only on screen so far.
 *
 * Negative, because the server's ids are positive integers and this guarantees
 * no collision. That matters: React keys the row by id, and a temporary id that
 * happened to match a real one would make the reconciler reuse the wrong DOM
 * node when the real row arrives.
 */
let temporaryId = -1;
const nextTemporaryId = (): number => temporaryId--;

export function useCreateTransaction(): UseMutationResult<
  Transaction,
  Error,
  TransactionCreate,
  RollbackContext
> {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (payload: TransactionCreate) =>
      request<Transaction>("/transactions", { method: "POST", json: payload }),

    onMutate: async (payload) => {
      const context = await beginOptimistic(client);

      // The optimistic row. `signed_amount` is computed here because the table
      // renders it and the server would otherwise be the only source — leaving
      // it blank for one frame is a visible flash of an empty cell.
      const optimistic: Transaction = {
        id: nextTemporaryId(),
        account_id: payload.account_id,
        category_id: payload.category_id ?? null,
        amount: payload.amount,
        type: payload.type,
        occurred_on: payload.occurred_on,
        description: payload.description ?? null,
        signed_amount: payload.type === "expense" ? `-${payload.amount}` : payload.amount,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      client.setQueriesData<Transaction[]>({ queryKey: ["transactions"] }, (old) => {
        if (!old) return old;
        // Newest first, matching the server's sort. Inserting at the top rather
        // than appending is what makes the new row appear where the user is
        // already looking.
        return [optimistic, ...old];
      });

      return context;
    },

    onError: (_error, _payload, context) => rollback(client, context),
    // Always refetch, success or failure: on success the optimistic row still
    // has a fake id and no server timestamps, and on failure the rollback needs
    // confirming against reality.
    onSettled: () => invalidateAfterWrite(client),
  });
}

export function useUpdateTransaction(): UseMutationResult<
  Transaction,
  Error,
  { id: number; changes: TransactionUpdate },
  RollbackContext
> {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ id, changes }) =>
      request<Transaction>(`/transactions/${id}`, { method: "PATCH", json: changes }),

    onMutate: async ({ id, changes }) => {
      const context = await beginOptimistic(client);

      client.setQueriesData<Transaction[]>({ queryKey: ["transactions"] }, (old) => {
        if (!old) return old;
        return old.map((row) => {
          if (row.id !== id) return row;
          const merged = { ...row, ...changes };
          // Recompute the derived field rather than merging it: a patch that
          // changes `amount` or `type` invalidates the stored `signed_amount`,
          // and showing a stale one is worse than showing none.
          const amount = merged.amount;
          return {
            ...merged,
            signed_amount: merged.type === "expense" ? `-${amount}` : amount,
          };
        });
      });

      return context;
    },

    onError: (_error, _variables, context) => rollback(client, context),
    onSettled: () => invalidateAfterWrite(client),
  });
}

export function useDeleteTransaction(): UseMutationResult<
  void,
  Error,
  number,
  RollbackContext
> {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => request<void>(`/transactions/${id}`, { method: "DELETE" }),

    onMutate: async (id) => {
      const context = await beginOptimistic(client);
      client.setQueriesData<Transaction[]>({ queryKey: ["transactions"] }, (old) =>
        old ? old.filter((row) => row.id !== id) : old,
      );
      return context;
    },

    onError: (_error, _id, context) => rollback(client, context),
    onSettled: () => invalidateAfterWrite(client),
  });
}

export function useCreateAccount(): UseMutationResult<Account, Error, AccountCreate> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload) => request<Account>("/accounts", { method: "POST", json: payload }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.accounts });
    },
  });
}

export function useCreateCategory(): UseMutationResult<Category, Error, CategoryCreate> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload) =>
      request<Category>("/categories", { method: "POST", json: payload }),
    onSuccess: () => {
      // Invalidate by prefix: the categories cache is keyed by `type`, so a new
      // expense category has to clear the "all" entry and the "expense" one.
      void client.invalidateQueries({ queryKey: ["categories"] });
    },
  });
}

// --- AI ---------------------------------------------------------------------
//
// **Every call here costs money**, which changes the caching rules that apply
// to the rest of this file. A summary refetching in the background when a
// window regains focus is free and desirable; an AI insight doing the same is a
// paid regeneration of a paragraph the user is already looking at. So the read
// below opts out of every automatic refetch TanStack Query does by default, and
// the two writes are mutations — not because they change server state (one of
// them does not) but because a mutation only ever fires when something calls
// it.

/**
 * `POST /ai/query` — a question, an answer, and the evidence behind it.
 *
 * A mutation despite being a read, and the reason is the same one that makes
 * the endpoint a POST: the call is neither idempotent-in-cost nor cacheable. A
 * `useQuery` keyed on the question text would re-run on remount and on window
 * focus, quietly billing for an answer already on screen. `useMutation` fires
 * exactly when `mutate` is called and never on its own.
 */
export function useAskAi(): UseMutationResult<AiQuery, Error, string> {
  return useMutation({
    mutationFn: (question: string) =>
      request<AiQuery>("/ai/query", { method: "POST", json: { question } }),
  });
}

/**
 * `POST /ai/categorize` — suggestions only. Writes nothing, so invalidates
 * nothing.
 *
 * The absent `onSuccess` is the point: this mutation deliberately leaves every
 * cache alone, because the ledger has not changed. Invalidating here would
 * refetch the transaction list to discover that it is identical — and would
 * suggest to the next person reading this file that something was written.
 */
export function useSuggestCategories(): UseMutationResult<
  CategorySuggestions,
  Error,
  CategorizeParams
> {
  return useMutation({
    mutationFn: (params: CategorizeParams) =>
      request<CategorySuggestions>("/ai/categorize", { method: "POST", json: params }),
  });
}

/** `POST /ai/categorize/apply` — the explicit write, after the user has reviewed. */
export function useApplyCategories(): UseMutationResult<
  ApplyCategoriesResult,
  Error,
  CategoryAssignment[]
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (assignments: CategoryAssignment[]) =>
      request<ApplyCategoriesResult>("/ai/categorize/apply", {
        method: "POST",
        json: { assignments },
      }),
    // This one *does* write, and it changes categories — so the donut, the
    // category breakdown and the ledger are all now stale. Same invalidation
    // every other write in this file performs, for the same reason.
    onSuccess: () => invalidateAfterWrite(client),
  });
}

/**
 * `GET /ai/insights/monthly` — the write-up for one month.
 *
 * `enabled` gates it behind an explicit request rather than firing on mount.
 * The dashboard mounts on every visit and this is a paid call, so generating an
 * insight nobody asked to see would bill the user for scrolling past a card.
 *
 * The staleness settings are unusually aggressive for this file, and
 * deliberately: a month that has ended cannot produce a different answer, so
 * `Infinity` is not a heuristic here, it is a fact about the data. The refetch
 * opt-outs cover the ways TanStack Query would otherwise regenerate it for
 * free-feeling reasons — a tab switch, a reconnect, a remount when the user
 * navigates back to the dashboard.
 */
export function useMonthlyInsight(
  month: string,
  accountId: number | undefined,
  enabled: boolean,
): UseQueryResult<MonthlyInsight> {
  return useQuery({
    queryKey: keys.monthlyInsight(month, accountId),
    queryFn: () =>
      request<MonthlyInsight>("/ai/insights/monthly", {
        params: { month, account_id: accountId },
      }),
    enabled,
    staleTime: Infinity,
    gcTime: 30 * 60 * 1000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    // A 503 means the server has no API key configured — retrying will not
    // conjure one, and three attempts just delay the message explaining it.
    retry: false,
  });
}

export function useImportCsv(): UseMutationResult<
  ImportSummary,
  Error,
  { file: File; accountId: number; dryRun: boolean }
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ file, accountId, dryRun }) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("account_id", String(accountId));
      return request<ImportSummary>("/transactions/import", {
        method: "POST",
        formData,
        params: { dry_run: dryRun },
      });
    },
    onSuccess: (result) => {
      // A dry run wrote nothing, so invalidating would throw away good cache
      // and refetch identical data for no reason.
      if (!result.dry_run) invalidateAfterWrite(client);
    },
  });
}
