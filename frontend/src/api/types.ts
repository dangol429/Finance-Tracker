/**
 * TypeScript mirrors of the backend's Pydantic schemas.
 *
 * **Money is `string`, everywhere, and that is not a mistake.** The API sends
 * `"1500.00"`, not `1500.0`, because a JSON number is an IEEE double and a
 * total a double cannot hold exactly would arrive differing from the database
 * in its last decimal — on precisely the figures a user checks against their
 * bank. Typing these as `string` makes that contract visible: you cannot
 * accidentally add two amounts with `+` and get `"45.20100.00"` past the
 * compiler without noticing. Parse explicitly at the point of arithmetic
 * (`lib/format.ts` has the helpers), and never round-trip a value you are only
 * displaying.
 *
 * **These are hand-written rather than generated from the OpenAPI spec.** A
 * generator is the right answer once the surface is large or the team is more
 * than one person; at this size, hand-written types stay readable, carry the
 * comments that explain *why* a field is shaped the way it is, and cost one
 * edit per endpoint change. The risk they carry — drifting from the server — is
 * real, and the mitigation is that every one of these is exercised by a page
 * that would break visibly.
 */

export type TransactionType = "income" | "expense";

export type AccountType =
  | "checking"
  | "savings"
  | "credit_card"
  | "cash"
  | "investment";

// --- Auth -------------------------------------------------------------------

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string;
}

export interface Token {
  access_token: string;
  token_type: string;
}

export interface RegisterPayload {
  email: string;
  password: string;
  full_name?: string | null;
}

// --- Accounts and categories ------------------------------------------------

export interface Account {
  id: number;
  name: string;
  type: AccountType;
  currency: string;
  /** A real column the API does not maintain — see `schemas/account.py`. The
   *  dashboard derives balance from the aggregation endpoints instead. */
  balance: string;
  created_at: string;
  updated_at: string;
}

export interface AccountCreate {
  name: string;
  type: AccountType;
  currency?: string;
}

export interface Category {
  id: number;
  name: string;
  type: TransactionType;
  created_at: string;
  updated_at: string;
}

export interface CategoryCreate {
  name: string;
  type: TransactionType;
}

// --- Transactions -----------------------------------------------------------

export interface Transaction {
  id: number;
  account_id: number;
  category_id: number | null;
  amount: string;
  type: TransactionType;
  /** ISO date, `YYYY-MM-DD`. Not a timestamp: this is the day the money moved. */
  occurred_on: string;
  description: string | null;
  /** Amount with direction applied — negative for expenses. Derived server-side. */
  signed_amount: string;
  created_at: string;
  updated_at: string;
}

export interface TransactionCreate {
  account_id: number;
  category_id?: number | null;
  amount: string;
  type: TransactionType;
  occurred_on: string;
  description?: string | null;
}

/**
 * Every field optional, because the endpoint is PATCH.
 *
 * The subtlety the backend documents and this type cannot express: `null` and
 * *absent* mean different things. `{description: null}` clears the description;
 * `{}` leaves it alone. Only `category_id` and `description` may be explicitly
 * nulled — sending `null` for anything else is a 422. Build these objects by
 * adding only the keys that changed rather than by spreading a whole row.
 */
export type TransactionUpdate = Partial<TransactionCreate>;

export interface TransactionFilters {
  account_id?: number;
  category_id?: number;
  type?: TransactionType;
  date_from?: string;
  date_to?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

// --- Aggregations -----------------------------------------------------------

export interface MonthPoint {
  /** Pre-formatted `"2026-03"`, for an axis label. */
  month: string;
  month_start: string;
  income: string;
  expense: string;
  net: string;
  transaction_count: number;
}

export interface MonthlySummary {
  date_from: string | null;
  date_to: string | null;
  months: MonthPoint[];
}

export interface CategorySlice {
  category_id: number | null;
  /** `"Uncategorized"` when `category_id` is null. */
  category_name: string;
  total: string;
  transaction_count: number;
  average: string;
  share: string;
}

export interface CategoryBreakdown {
  type: TransactionType;
  total: string;
  transaction_count: number;
  categories: CategorySlice[];
}

export interface SideTotals {
  total: string;
  transaction_count: number;
  average: string;
  largest: string;
}

export interface IncomeVsExpense {
  date_from: string | null;
  date_to: string | null;
  income: SideTotals;
  expense: SideTotals;
  net: string;
  /** Null, not zero, when income is zero — the rate is undefined, not 0%. */
  savings_rate: string | null;
}

/** Filters the aggregation endpoints accept. Notably *not* `category_id` or
 *  `q`: narrowing a category breakdown to one category is degenerate, and the
 *  search box is a property of the ledger view. See `useFilters`. */
export interface SummaryFilters {
  account_id?: number;
  date_from?: string;
  date_to?: string;
}

// --- CSV import -------------------------------------------------------------

export interface RowError {
  row: number;
  field: string | null;
  value: string | null;
  reason: string;
}

export interface ImportSummary {
  filename: string | null;
  account_id: number;
  dry_run: boolean;
  total_rows: number;
  imported: number;
  failed: number;
  errors: RowError[];
  errors_truncated: boolean;
}
