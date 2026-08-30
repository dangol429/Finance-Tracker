import { formatMoney } from "@/lib/format";
import styles from "./dashboard.module.css";

/**
 * One tooltip shared by all three charts.
 *
 * Recharts' default tooltip is a white box with unformatted numbers — it
 * ignores the theme entirely and renders `1500` where the rest of the app says
 * `$1,500.00`. Replacing it is not polish for its own sake: a chart whose
 * tooltip disagrees with the table beneath it about how money looks is a chart
 * people stop trusting.
 *
 * Shared rather than one per chart so the three cannot drift apart, which is
 * the failure that happens by default when each chart gets its own inline
 * formatter.
 */

interface TooltipEntry {
  name?: string;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  /** Formats the heading — a month key needs expanding, a category name does not. */
  labelFormatter?: (label: string) => string;
  /** Appended under a divider, e.g. the net figure for a month. */
  footer?: (payload: TooltipEntry[]) => React.ReactNode;
}

export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter,
  footer,
}: ChartTooltipProps): JSX.Element | null {
  // Recharts renders this component even when nothing is hovered, passing
  // `active: false`. Returning null is what stops an empty box appearing in the
  // corner of every chart.
  if (!active || !payload?.length) return null;

  const heading =
    labelFormatter && typeof label === "string" ? labelFormatter(label) : String(label ?? "");

  return (
    <div className={styles.tooltip}>
      {heading && <div className={styles.tooltipTitle}>{heading}</div>}

      {payload.map((entry, index) => (
        <div className={styles.tooltipRow} key={`${entry.dataKey}-${index}`}>
          <span className={styles.tooltipKey}>
            <span
              className={styles.legendSwatch}
              style={{ background: entry.color }}
              aria-hidden="true"
            />
            {entry.name}
          </span>
          <span className={styles.tooltipValue}>{formatMoney(entry.value ?? 0)}</span>
        </div>
      ))}

      {footer && (
        <>
          <div className={styles.tooltipDivider} />
          {footer(payload)}
        </>
      )}
    </div>
  );
}
