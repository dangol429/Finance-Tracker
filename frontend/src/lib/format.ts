/**
 * Formatting money, dates and percentages.
 *
 * The recurring theme: the API sends money as *strings* (see `api/types.ts`),
 * and this module is the only place that converts them to numbers. Keeping the
 * parse in one place means the rounding, the currency symbol and the sign
 * convention are decided once instead of drifting between the table and the
 * charts.
 */

/**
 * Turn an API money string into a number.
 *
 * Lossy by definition — `Number("0.1")` is not exactly 0.1 — and safe for the
 * two things this app does with it: rendering, and feeding a chart library that
 * only accepts numbers. It is *not* safe for arithmetic whose result is stored,
 * which is why every total in this app is computed by PostgreSQL and merely
 * displayed here.
 */
export function toNumber(amount: string | number | null | undefined): number {
  if (amount === null || amount === undefined) return 0;
  const parsed = typeof amount === "number" ? amount : Number.parseFloat(amount);
  return Number.isFinite(parsed) ? parsed : 0;
}

interface MoneyOptions {
  currency?: string;
  /** Show a leading `+` on positive values. Off by default. */
  signed?: boolean;
  /** Drop the decimals — for axis ticks and large headline figures. */
  compact?: boolean;
}

export function formatMoney(
  amount: string | number | null | undefined,
  { currency = "USD", signed = false, compact = false }: MoneyOptions = {},
): string {
  const value = toNumber(amount);

  if (compact && Math.abs(value) >= 1000) {
    // `notation: "compact"` gives "1.2K"/"3.4M" — right for a chart axis, where
    // the exact figure belongs in the tooltip and the axis just needs scale.
    const formatted = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(Math.abs(value));
    return applySign(formatted, value, signed);
  }

  const formatted = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(value));

  return applySign(formatted, value, signed);
}

/**
 * Attach the sign ourselves rather than letting `Intl` do it.
 *
 * `Intl` renders a negative as `-$45.20`, putting the minus outside the symbol.
 * Finance UIs conventionally render `−$45.20` with a true minus sign (U+2212,
 * which is the width of a digit and aligns in a tabular column, unlike the
 * hyphen). Formatting the absolute value and prefixing by hand is what gets
 * both the glyph and the column alignment right.
 */
function applySign(formatted: string, value: number, signed: boolean): string {
  if (value < 0) return `\u2212${formatted}`;
  if (signed && value > 0) return `+${formatted}`;
  return formatted;
}

/** `"2026-03-04"` → `"Mar 4"`, or `"Mar 4, 2026"` when the year is ambiguous. */
export function formatDate(iso: string, { withYear = false } = {}): string {
  const date = parseIsoDate(iso);
  if (!date) return iso;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    ...(withYear ? { year: "numeric" } : {}),
  }).format(date);
}

/** `"2026-03"` → `"Mar"`, for a chart axis where the year is in the title. */
export function formatMonthShort(month: string): string {
  const date = parseIsoDate(`${month}-01`);
  if (!date) return month;
  return new Intl.DateTimeFormat("en-US", { month: "short" }).format(date);
}

/** `"2026-03"` → `"March 2026"`, for a tooltip where there is room to be clear. */
export function formatMonthLong(month: string): string {
  const date = parseIsoDate(`${month}-01`);
  if (!date) return month;
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(date);
}

/**
 * Parse `YYYY-MM-DD` as a *local* date.
 *
 * `new Date("2026-03-04")` parses as UTC midnight, which in any negative
 * offset renders as March 3rd. That off-by-one is the single most common date
 * bug in a JS frontend and it only shows up for users west of Greenwich, which
 * is why it survives testing. Splitting the parts and using the multi-argument
 * constructor keeps the date in local time, where a calendar date belongs.
 */
export function parseIsoDate(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return null;
  const [, year, month, day] = match;
  return new Date(Number(year), Number(month) - 1, Number(day));
}

/** A `Date` back to `YYYY-MM-DD`, again without a UTC round trip. */
export function toIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function formatPercent(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${toNumber(value).toFixed(1)}%`;
}

/** Today, as the API spells dates. */
export function today(): string {
  return toIsoDate(new Date());
}

/** `n` months before today, clamped to the first of that month. */
export function monthsAgo(months: number): string {
  const date = new Date();
  date.setDate(1);
  date.setMonth(date.getMonth() - months);
  return toIsoDate(date);
}
