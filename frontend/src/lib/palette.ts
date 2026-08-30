/**
 * Chart colours, read from the CSS custom properties in `tokens.css`.
 *
 * Recharts takes colours as props, not as CSS, so it cannot use `var(--chart-1)`
 * directly on an SVG fill in a way that survives a theme switch. Reading the
 * computed values from the document is what keeps one palette definition
 * serving both the CSS and the charts — the alternative is a second copy of
 * eight hex codes that has to be kept in step by hand, and won't be.
 */

const SERIES_TOKENS = [
  "--chart-1",
  "--chart-2",
  "--chart-3",
  "--chart-4",
  "--chart-5",
  "--chart-6",
  "--chart-7",
  "--chart-8",
] as const;

function readToken(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name);
  return value.trim() || fallback;
}

export interface ChartPalette {
  series: string[];
  income: string;
  expense: string;
  accent: string;
  grid: string;
  axis: string;
  surface: string;
  text: string;
}

export function readPalette(): ChartPalette {
  return {
    series: SERIES_TOKENS.map((token, index) =>
      readToken(token, ["#4f8cff", "#34d399", "#a78bfa", "#fbbf24"][index % 4]!),
    ),
    income: readToken("--income", "#34d399"),
    expense: readToken("--expense", "#f87171"),
    accent: readToken("--accent", "#4f8cff"),
    grid: readToken("--border", "#232833"),
    axis: readToken("--text-muted", "#646e82"),
    surface: readToken("--bg-raised", "#1a1e28"),
    text: readToken("--text-primary", "#e8ecf4"),
  };
}

/**
 * A stable colour for a category, so the same category is the same colour in
 * every chart and across reloads.
 *
 * Keyed on the category id rather than on its position in the array, which is
 * the thing that makes it stable: sorting the donut by size means a category's
 * *index* changes whenever spending changes, and a legend whose colours
 * reshuffle month to month is actively misleading.
 *
 * Uncategorized (`null`) always takes the last, deliberately grey slot — it is
 * an absence rather than a category, and should not look like one.
 */
export function colorForCategory(
  categoryId: number | null,
  palette: ChartPalette,
): string {
  const last = palette.series.length - 1;
  if (categoryId === null) return palette.series[last]!;
  return palette.series[categoryId % last]!;
}
