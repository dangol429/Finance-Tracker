"""Response shapes for the aggregation endpoints.

Only *output* schemas live here. The three summary endpoints take no request
body at all — everything a caller can say is a query parameter, validated in
the router's signature — so there is no `SummaryCreate` to write.

The shapes below are built for a chart, and that pushes two decisions that the
transaction schemas didn't have to make:

**Every endpoint returns an object, not a bare list.** `GET /transactions`
returns a JSON array because a page of rows is all there is to say. An
aggregate is not: a bar chart needs the range that was actually covered, a pie
chart needs the grand total that its slices are shares *of*, and a savings-rate
gauge needs to know that income was zero rather than that the rate was. None of
that fits in a list, and bolting it on later means changing the top-level JSON
type — the one change no client survives silently.

**Zero is a value; missing is not.** A month with no transactions still gets a
bucket, with `0.00` in it. The database has nothing to say about that month —
`GROUP BY` emits no row for a group that has no rows — but a chart that skips
straight from March to May draws a line implying April didn't happen, and one
that renders `null` as zero has quietly invented data. Filling the gap in the
API means every consumer gets the same answer instead of each inventing its
own. See `_fill_month_gaps` in the router for where that happens.

**Money is a `Decimal`, which means it goes out as a JSON string.** Pydantic
serializes `Decimal` as `"1550.00"`, not `1550.0`, and that is the same form
`TransactionRead.amount` already takes — so every endpoint in this API agrees
about what money looks like on the wire.

It is worth knowing that this costs the chart consumer a `Number()` call, and
worth keeping anyway. A JSON number is an IEEE double, and the moment a total
crosses into a value a double can't hold exactly, the API starts reporting a
figure that differs from the one in the database — silently, in the last
decimal, on the numbers a user is most likely to check against their bank. The
string is the exact value the aggregate produced. Parsing it is the client's
one-line problem; reconstructing a lost cent is nobody's.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import TransactionType


class MonthPoint(BaseModel):
    """One month on the x-axis of the monthly-summary chart."""

    # A pre-formatted "2026-03" alongside the real date, because the two get
    # used by different consumers: the string is the axis label, the date is
    # what a client sorts, diffs or does arithmetic on. Formatting once here is
    # cheaper than every caller deriving the label — and it pins the format, so
    # two screens can't disagree about whether it's "2026-03" or "Mar 2026".
    month: str = Field(description='Calendar month as "YYYY-MM"', examples=["2026-03"])
    month_start: date = Field(description="First day of that month")

    # Both are magnitudes, matching how the rows are stored: `expense` is the
    # total money that left, as a positive number. The signed view is `net`.
    income: Decimal
    expense: Decimal
    net: Decimal = Field(description="income - expense; negative in a month that overspent")

    transaction_count: int = Field(description="Rows behind this bucket, across both types")


class MonthlySummaryRead(BaseModel):
    """`GET /summary/monthly` — one row per calendar month, gaps filled."""

    # The range actually covered, which is not the same as the range requested:
    # with no `date_from`, it is resolved from the data. Echoing the resolved
    # bounds means a client can label the chart without a second query, and can
    # tell "you have no history before June" from "you asked for June onward".
    date_from: date | None
    date_to: date | None
    months: list[MonthPoint]


class CategorySlice(BaseModel):
    """One slice of the by-category pie."""

    # Nullable, and the reason the join behind it is a LEFT JOIN: a transaction
    # may legitimately have no category, and dropping those rows would make the
    # slices add up to less than the total they claim to divide.
    category_id: int | None
    category_name: str = Field(description='"Uncategorized" when category_id is null')

    total: Decimal
    transaction_count: int
    average: Decimal = Field(description="Mean transaction size in this category, to the cent")
    share: Decimal = Field(description="Percent of the grand total, to two decimals")


class CategoryBreakdownRead(BaseModel):
    """`GET /summary/by-category` — where the money went, largest first."""

    type: TransactionType = Field(description="Which side was broken down")

    # The denominator every `share` was computed against. Sent explicitly so a
    # client never has to re-derive it by summing the slices — which would be
    # subtly wrong, since each share is rounded independently and they need not
    # add to exactly 100.
    total: Decimal
    transaction_count: int
    categories: list[CategorySlice]


class SideTotals(BaseModel):
    """The four aggregates computed for one side of the ledger."""

    total: Decimal
    transaction_count: int
    # Zero rather than null when there are no transactions. An average over an
    # empty set is genuinely undefined and SQL says so by returning NULL, but
    # "you spent nothing, so your average expense is nothing" is the answer a
    # dashboard wants, and it is not a lie the way a made-up *rate* would be
    # (see `savings_rate` below, where the distinction goes the other way).
    average: Decimal
    largest: Decimal


class IncomeVsExpenseRead(BaseModel):
    """`GET /summary/income-vs-expense` — the two-bar chart, plus the headline."""

    date_from: date | None
    date_to: date | None

    income: SideTotals
    expense: SideTotals
    net: Decimal = Field(description="income.total - expense.total")

    # Null, not zero, when income is zero. Percent-of-income is undefined with
    # no income, and the two candidate lies are both bad: `0` reads as "saved
    # nothing" (false — there was nothing to save) and `-100` reads as a
    # catastrophe. Null makes a gauge show a gap, which is the honest rendering
    # of a question that has no answer.
    savings_rate: Decimal | None = Field(
        description="net as a percent of income, to two decimals; null when income is zero"
    )
