import { useEffect, useState } from "react";

import type { Account, Category } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { BareInput, BareSelect } from "@/components/ui/Field";
import { CloseIcon, SearchIcon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/Spinner";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { Filters } from "@/hooks/useFilters";
import { monthsAgo, today, toIsoDate } from "@/lib/format";
import styles from "./filters.module.css";

interface FilterBarProps {
  filters: Filters;
  setFilters: (changes: Partial<Filters>) => void;
  reset: () => void;
  isFiltered: boolean;
  accounts: Account[];
  categories: Category[];
  /** Shows the search spinner while the debounced query is in flight. */
  searching?: boolean;
  /** Hides the category/type/search controls on the dashboard, which has no table. */
  compact?: boolean;
}

/** Named ranges, because "last 3 months" is how people actually think about
 *  a date range — the two date inputs are the escape hatch, not the default. */
const PRESETS = [
  { label: "30d", months: 0, days: 30 },
  { label: "3m", months: 2, days: 0 },
  { label: "6m", months: 5, days: 0 },
  { label: "12m", months: 11, days: 0 },
] as const;

function presetRange(preset: (typeof PRESETS)[number]): { from: string; to: string } {
  if (preset.days) {
    const date = new Date();
    date.setDate(date.getDate() - preset.days);
    return { from: toIsoDate(date), to: today() };
  }
  return { from: monthsAgo(preset.months), to: today() };
}

export function FilterBar({
  filters,
  setFilters,
  reset,
  isFiltered,
  accounts,
  categories,
  searching = false,
  compact = false,
}: FilterBarProps): JSX.Element {
  // The search box is a *controlled* input on its own local state, and only the
  // debounced copy is written to the URL. Binding the input directly to the URL
  // parameter would make every keystroke a navigation — the cursor jumps, and
  // typing feels like it is fighting back.
  const [searchDraft, setSearchDraft] = useState(filters.search);
  const debouncedSearch = useDebouncedValue(searchDraft, 350);

  useEffect(() => {
    // Guard against writing the value that is already there: without this, the
    // effect fires on mount and on every unrelated filter change, replacing the
    // history entry each time for no reason.
    if (debouncedSearch !== filters.search) {
      setFilters({ search: debouncedSearch });
    }
    // `filters.search` is intentionally excluded — including it makes this
    // effect a loop between the URL and the draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  // Keeps the box in step when the URL changes from elsewhere: a Clear press, a
  // back button, or a pasted link.
  useEffect(() => {
    setSearchDraft(filters.search);
  }, [filters.search]);

  const activePreset = PRESETS.find((preset) => {
    const range = presetRange(preset);
    return range.from === filters.dateFrom && range.to === filters.dateTo;
  });

  return (
    <div className={styles.bar}>
      <div className={styles.group}>
        <span className={styles.groupLabel}>Period</span>
        <div className={styles.presets}>
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className={`${styles.preset} ${
                activePreset?.label === preset.label ? styles.presetActive : ""
              }`}
              aria-pressed={activePreset?.label === preset.label}
              onClick={() => {
                const range = presetRange(preset);
                setFilters({ dateFrom: range.from, dateTo: range.to });
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.group}>
        <span className={styles.groupLabel}>Custom range</span>
        <div className={styles.controls}>
          {/* Native date inputs. A hand-built calendar popover would look more
              designed and would be worse: this one is keyboard-accessible,
              localised, and on a phone it opens the OS date wheel that people
              already know how to use. */}
          <BareInput
            className={styles.dateInput}
            type="date"
            value={filters.dateFrom}
            max={filters.dateTo}
            onChange={(event) => setFilters({ dateFrom: event.target.value })}
            aria-label="From date"
          />
          <span className={styles.dateSeparator}>to</span>
          <BareInput
            className={styles.dateInput}
            type="date"
            value={filters.dateTo}
            min={filters.dateFrom}
            max={today()}
            onChange={(event) => setFilters({ dateTo: event.target.value })}
            aria-label="To date"
          />
        </div>
      </div>

      <div className={styles.group}>
        <span className={styles.groupLabel}>Account</span>
        <BareSelect
          className={styles.selectSm}
          value={filters.accountId ?? ""}
          onChange={(event) =>
            setFilters({ accountId: event.target.value ? Number(event.target.value) : null })
          }
          aria-label="Filter by account"
        >
          <option value="">All accounts</option>
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.name}
            </option>
          ))}
        </BareSelect>
      </div>

      {!compact && (
        <>
          <div className={styles.group}>
            <span className={styles.groupLabel}>Category</span>
            <BareSelect
              className={styles.selectSm}
              value={filters.categoryId ?? ""}
              onChange={(event) =>
                setFilters({
                  categoryId: event.target.value ? Number(event.target.value) : null,
                })
              }
              aria-label="Filter by category"
            >
              <option value="">All categories</option>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
            </BareSelect>
          </div>

          <div className={styles.group}>
            <span className={styles.groupLabel}>Type</span>
            <BareSelect
              className={styles.selectSm}
              value={filters.type ?? ""}
              onChange={(event) =>
                setFilters({
                  type: event.target.value ? (event.target.value as "income" | "expense") : null,
                })
              }
              aria-label="Filter by type"
            >
              <option value="">Income &amp; expense</option>
              <option value="income">Income only</option>
              <option value="expense">Expense only</option>
            </BareSelect>
          </div>

          <div className={`${styles.group} ${styles.searchGroup}`}>
            <span className={styles.groupLabel}>Search</span>
            <div className={styles.searchWrap}>
              <SearchIcon size={15} className={styles.searchIcon} />
              <BareInput
                className={styles.searchInput}
                type="search"
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder="Description contains…"
                aria-label="Search descriptions"
              />
              {searching && searchDraft ? (
                <span className={styles.searchSpinner}>
                  <Spinner />
                </span>
              ) : searchDraft ? (
                <button
                  type="button"
                  className={styles.searchClear}
                  onClick={() => setSearchDraft("")}
                  aria-label="Clear search"
                >
                  <CloseIcon size={13} />
                </button>
              ) : null}
            </div>
          </div>
        </>
      )}

      {isFiltered && (
        <div className={styles.trailing}>
          <Button variant="ghost" size="sm" onClick={reset}>
            Clear filters
          </Button>
        </div>
      )}

      {!compact && (
        <p className={styles.scopeNote}>
          Period and account narrow the charts and the table. Category, type and search
          narrow the table only — the aggregation endpoints take a date range and an
          account, and a category breakdown filtered to one category has nothing to say.
        </p>
      )}
    </div>
  );
}
