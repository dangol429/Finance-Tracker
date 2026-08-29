"""Aggregation routes — the endpoints a dashboard is actually built on.

    GET /summary/monthly            income / expense / net, one row per month
    GET /summary/by-category        where the money went, largest slice first
    GET /summary/income-vs-expense  the headline pair, plus a savings rate

**The one idea this module exists to demonstrate.** Every number below is
computed by PostgreSQL, in one round trip, with `GROUP BY` and real aggregate
functions. None of these handlers fetches rows and adds them up in Python. That
is not a micro-optimization, it is a difference in kind:

  - A year of transactions might be 5,000 rows. Summing them in Python means
    serializing 5,000 objects across the socket, building 5,000 ORM instances,
    and discarding all of them to produce twelve numbers. The database can
    return those twelve numbers directly, having never left the page cache.
  - It stays correct as the data grows. The Python version's cost scales with
    the user's *history*; the SQL version's cost scales with the size of the
    answer, which is bounded by the number of months (or categories) on screen.
  - The pagination problem disappears. `GET /transactions` needs `limit` and a
    ceiling because an unbounded list is a memory hazard. These endpoints need
    no such thing — the aggregate *is* the bound. Arbitrarily many transactions
    collapse into one row per group, and the group count is bounded by the
    calendar or by the user's category list.

**What SQL computes and what Python computes.** The split is deliberate and
holds in all three handlers:

    SQL     — anything that touches a transaction row: SUM, COUNT, AVG, MAX,
              the grouping, the filtering, the ordering.
    Python  — presentation over the handful of rows that come back: rounding
              to the cent, percent shares, the "YYYY-MM" label, filling in
              months the database correctly had nothing to say about.

Rounding in particular belongs on this side. `AVG` over `NUMERIC` returns a
value with a long tail of decimals, and the moment you round it in SQL you have
baked a display decision into the query — so the export, the chart and the API
each need their own copy of it. Quantizing here keeps the aggregate exact right
up to the edge.

**Ownership works exactly as it does in `routers/transactions.py`**: every
statement starts from `Transaction.user_id == current_user.id` in the `WHERE`
clause, applied before any client-supplied filter can influence the statement.
An aggregate makes that rule *more* load-bearing, not less — a missing scope on
a list endpoint leaks rows the caller can at least be seen fetching, while a
missing scope here silently folds other people's money into a total that looks
entirely plausible. There is no id to notice is wrong; there is just a number
that is too big.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import TIMESTAMP, ColumnElement, Date, Numeric, Row, cast, func, select

from app.core.deps import CurrentUser, DbSession
from app.models.category import Category
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.summary import (
    CategoryBreakdownRead,
    CategorySlice,
    IncomeVsExpenseRead,
    MonthlySummaryRead,
    MonthPoint,
    SideTotals,
)

router = APIRouter(prefix="/summary", tags=["summary"])


# --- Constants -------------------------------------------------------------

ZERO = Decimal("0.00")

# Two decimal places, half-up — the rounding a person does on paper, and the one
# an accountant expects. Python's *default* is ROUND_HALF_EVEN ("banker's
# rounding"), which is better across repeated sums but surprises everyone who
# checks a single figure by hand.
CENTS = Decimal("0.01")

# The upper bound on how many month buckets one response may contain. This
# exists because the range is gap-filled: without `date_from`, the span is
# whatever the data says, and a single fat-fingered `1823-04-01` would turn a
# twelve-point chart into a two-thousand-point one. 600 is fifty years — far
# past any real ledger, close enough to catch a garbage date.
MAX_MONTHS = 600

# The label for transactions with no category. A constant rather than a string
# literal in the handler because it is a value clients will match on, and two
# spellings of it ("Uncategorized" here, "(none)" in a later export) is the kind
# of drift nobody notices until a dashboard shows two empty slices.
UNCATEGORIZED_LABEL = "Uncategorized"


# --- The month expression --------------------------------------------------
#
# `date_trunc('month', x)` is the canonical way to bucket by month, but the call
# needs one non-obvious cast to be safe.
#
# PostgreSQL has `date_trunc(text, timestamp)`, `date_trunc(text, timestamptz)`
# and `date_trunc(text, interval)`. A `date` column implicitly converts to the
# first two, so the call is ambiguous — and PostgreSQL breaks that tie by
# preferring `timestamptz`, which quietly drags the session's `TimeZone` setting
# into a calculation that has nothing to do with clocks. Casting to a plain
# `TIMESTAMP` first matches one overload exactly and keeps the arithmetic in
# calendar space, which is where `occurred_on` already lives (see the note on
# that column: it is a `Date`, not a `DateTime`, precisely so a purchase can't
# land in the wrong month).
#
# Casting the result back to `DATE` is what makes the driver hand Python a
# `datetime.date` of the first of the month, rather than a midnight timestamp
# the handler would then have to strip a time off.
#
# Defined once at module scope because it appears in both the SELECT list and
# the GROUP BY of the same statement, and the two must be the *same expression*
# — SQL evaluates GROUP BY before the SELECT list exists, so the expression is
# repeated in the emitted SQL rather than referred to by its output name.
MONTH_START = cast(func.date_trunc("month", cast(Transaction.occurred_on, TIMESTAMP)), Date)


# --- Shared query pieces ---------------------------------------------------


def _require_sane_range(date_from: date | None, date_to: date | None) -> None:
    """Reject a range that runs backwards.

    Pydantic validates each query parameter alone and cannot see a relationship
    *between* two of them, so this has to be a handler check. Left unchecked it
    isn't an error at all — just a `WHERE` that matches nothing, which reads to
    a user as "my dashboard went blank".

    The same rule is enforced in `routers/transactions.py`. Two copies of a
    four-line check is the cheaper mistake for now; the third caller is when it
    earns a home in a shared module rather than a third copy.
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_from must not be after date_to",
        )


def _scope(
    user_id: int,
    account_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    """Build the `WHERE` criteria every aggregate in this module starts from.

    Returned as a list of criteria rather than as a half-built `select()` on
    purpose: the three statements below differ in what they select and group by,
    and share only what they filter on. Handing back the filters lets each be
    assembled in its own handler, where its shape is readable, instead of hiding
    the interesting part inside a builder that returns a statement already
    half-committed to a shape.

    Note the first element is neither optional nor conditional. Everything after
    it is a filter the caller asked for; that one is the scope those filters
    narrow, and it is first so no `if` can be slipped in above it later.

    An `account_id` belonging to somebody else needs no check of its own: the
    user scope has already made the condition unsatisfiable, so the answer is an
    empty summary — the same reasoning (and the same non-answer) as filtering the
    ledger by a foreign account id.
    """
    criteria: list[ColumnElement[bool]] = [Transaction.user_id == user_id]
    if account_id is not None:
        criteria.append(Transaction.account_id == account_id)
    if date_from is not None:
        criteria.append(Transaction.occurred_on >= date_from)
    if date_to is not None:
        criteria.append(Transaction.occurred_on <= date_to)
    return criteria


def _sum_where(condition: ColumnElement[bool]) -> ColumnElement[Decimal]:
    """`SUM(amount)` over only the rows matching `condition`, never NULL.

    Two things are happening here, and both are the point of the exercise.

    **`FILTER (WHERE ...)` is conditional aggregation.** It lets one pass over
    the table produce several differently-filtered totals — income and expense
    in the same `GROUP BY`, from the same scan. The alternative is two queries
    (two scans, plus a merge in Python to line the months up) or the older
    `SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END)`, which does the same
    job in every SQL dialect but reads worse and, unlike `FILTER`, quietly turns
    "no matching rows" into a real zero for `AVG` and `COUNT` too.

    **`COALESCE` is not decoration.** An aggregate over zero rows is NULL, not
    zero — so a month where the user earned nothing comes back with
    `income = None`. That is SQL being precise about the difference between
    "nothing was added up" and "the total was zero", and it is a distinction the
    API deliberately does not pass on: a chart needs a number, and here the two
    genuinely mean the same thing. Note this reasoning does *not* extend to
    `savings_rate` in `income_vs_expense` below, where an undefined value stays
    null — the test is whether zero is the honest answer, not whether it is the
    convenient one.
    """
    return func.coalesce(func.sum(Transaction.amount).filter(condition), ZERO)


def _avg_amount() -> ColumnElement[Decimal]:
    """`AVG(amount)`, with its result type spelled out.

    The explicit `type_` is the whole reason this is a function rather than a
    bare `func.avg(...)` at the two call sites.

    SQLAlchemy knows the return type of some aggregates and not others.
    `func.sum()` and `func.max()` are "return type from args" functions, so they
    inherit `Numeric(12, 2)` from the column and SQLAlchemy converts the result
    accordingly. `func.avg()` is not one of them: it compiles to `avg(...)` with
    a `NullType`, meaning SQLAlchemy applies *no* result processing and hands
    back whatever the driver produced.

    On PostgreSQL that happens to be right — `avg(numeric)` is `numeric` and
    psycopg returns a `Decimal`. But "happens to be right" is the load-bearing
    phrase: the code would be depending on a driver's type mapping rather than
    on anything it stated, and the first backend that returns a float for this
    (SQLite does) turns `_to_cents` into an `AttributeError` at runtime. Naming
    the type makes the contract the query's, not the driver's.

    Unbounded `Numeric()` rather than `Numeric(12, 2)`: a mean genuinely has
    more decimals than the amounts it averages, and truncating them here would
    move a rounding decision back into the query — the thing this module keeps
    on the Python side on purpose.
    """
    return func.avg(Transaction.amount, type_=Numeric())


# --- Presentation helpers --------------------------------------------------


def _to_cents(value: Decimal | None) -> Decimal:
    """Round an aggregate to two decimal places, treating NULL as zero.

    `AVG` over `NUMERIC` returns far more precision than money has — the mean of
    10.00 and 5.01 is 7.5050000000000000 — and something has to decide where to
    cut it. Doing it here rather than in SQL keeps the query returning the exact
    aggregate, so a future caller that wants more precision (a tax export, a
    variance calculation) isn't fighting a rounding decision baked into the
    statement.
    """
    if value is None:
        return ZERO
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _percent(part: Decimal, whole: Decimal) -> Decimal | None:
    """`part` as a percent of `whole`, or None when the question is undefined.

    Guarding the zero denominator is the whole reason this is a function.
    `whole` is itself an aggregate, so "no matching rows" and "the amounts
    cancelled out" both arrive here as `0` — and in neither case is there a
    percentage to report. Returning `0` would be a fabricated answer that a
    chart renders as a confident empty bar.
    """
    if whole == 0:
        return None
    return (part / whole * 100).quantize(CENTS, rounding=ROUND_HALF_UP)


def _month_label(month_start: date) -> str:
    """Format a month as "YYYY-MM".

    Built from the parts rather than with `strftime("%Y-%m")` because
    `strftime`'s output for years before 1000 is platform-dependent — glibc,
    musl and the Windows CRT disagree about zero-padding. An f-string with an
    explicit width is the same amount of work and the same answer everywhere.
    """
    return f"{month_start.year:04d}-{month_start.month:02d}"


def _first_of_month(value: date) -> date:
    """The first day of the month `value` falls in."""
    return value.replace(day=1)


def _months_between(first: date, last: date) -> list[date]:
    """Every month start from `first` to `last`, inclusive.

    Counting in absolute month indices (`year * 12 + month`) rather than adding
    days in a loop. Months have four different lengths, so the `timedelta`
    version needs a correction step and a special case for December; this one is
    exact and has neither.
    """
    first_index = first.year * 12 + (first.month - 1)
    last_index = last.year * 12 + (last.month - 1)
    return [date(index // 12, index % 12 + 1, 1) for index in range(first_index, last_index + 1)]


def _fill_month_gaps(
    rows: list[Row[Any]],
    date_from: date | None,
    date_to: date | None,
) -> list[MonthPoint]:
    """Turn the grouped rows into a continuous monthly series.

    **The problem.** `GROUP BY` emits a row per group that *has* rows. A month in
    which nothing happened produces no group and therefore no row, so a user who
    took January off gets a series that jumps from December to February. Every
    charting library then draws a straight line between the two, which reads as
    "January was somewhere in between" — a value the data never contained. The
    database is not wrong here; it answered exactly what it was asked. Turning
    "no rows" into "a row of zeros" is a presentation decision, and this is the
    layer that makes it.

    **Why here and not in SQL.** PostgreSQL can do this with
    `generate_series(:from, :to, '1 month')` LEFT JOINed to the aggregate, and on
    a much larger result that would be the right call. It needs both bounds as
    real values though — and when the caller supplied neither, those bounds are
    *derived from the rows the query just returned*. Doing it in SQL would mean
    either a second query for MIN/MAX first, or a CTE that computes them and
    joins back. Both are more machinery than a loop over at most a few hundred
    rows that are already in memory.

    **Why the requested bounds win when present.** Asking for January to December
    and getting eight months back is a worse answer than getting twelve with four
    zeros in them: the caller picked that window, and the empty months inside it
    are part of what they asked about. With no bounds given the span is the
    data's own, so no leading or trailing padding is invented.
    """
    if not rows and (date_from is None or date_to is None):
        # Nothing to plot and no window to plot it over. Padding a range with one
        # open end would mean inventing the other end — "to today" is a guess, and
        # a wrong one for a caller browsing last year.
        return []

    first = _first_of_month(date_from) if date_from else rows[0].month_start
    last = _first_of_month(date_to) if date_to else rows[-1].month_start

    span = _months_between(first, last)
    if len(span) > MAX_MONTHS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"That range covers {len(span)} months (limit {MAX_MONTHS}). "
                "Narrow it with date_from / date_to."
            ),
        )

    # Index the rows the database *did* return, so the fill is a dict lookup per
    # month rather than a scan of the rows per month. At twelve months either
    # would be fine; at fifty years the quadratic version is the kind of thing
    # that only shows up in production.
    by_month = {row.month_start: row for row in rows}

    points: list[MonthPoint] = []
    for month_start in span:
        row = by_month.get(month_start)
        income = _to_cents(row.income) if row is not None else ZERO
        expense = _to_cents(row.expense) if row is not None else ZERO
        points.append(
            MonthPoint(
                month=_month_label(month_start),
                month_start=month_start,
                income=income,
                expense=expense,
                # Computed from the rounded pair, not rounded after the
                # subtraction, so `income - expense == net` holds exactly in the
                # JSON a client receives. Rounding last would let a chart's own
                # arithmetic disagree with the number printed beside it by a cent
                # — the sort of discrepancy that gets reported as a bug in the
                # ledger rather than as a rounding artefact.
                net=income - expense,
                transaction_count=row.transaction_count if row is not None else 0,
            )
        )
    return points


def _side(row: Row[Any] | None) -> SideTotals:
    """One side of the ledger, or a zeroed set when the group had no rows."""
    if row is None:
        return SideTotals(total=ZERO, transaction_count=0, average=ZERO, largest=ZERO)
    return SideTotals(
        total=_to_cents(row.total),
        transaction_count=row.transaction_count,
        average=_to_cents(row.average),
        largest=_to_cents(row.largest),
    )


# --- Endpoints -------------------------------------------------------------


@router.get(
    "/monthly",
    response_model=MonthlySummaryRead,
    summary="Income, expense and net for each calendar month",
)
def monthly_summary(
    current_user: CurrentUser,
    db: DbSession,
    account_id: Annotated[int | None, Query(gt=0, description="Only this account")] = None,
    date_from: Annotated[date | None, Query(description="Inclusive lower bound")] = None,
    date_to: Annotated[date | None, Query(description="Inclusive upper bound")] = None,
) -> MonthlySummaryRead:
    """The bar chart on the dashboard: one point per month, oldest first.

    The statement this builds:

    ```sql
    SELECT CAST(date_trunc('month', CAST(occurred_on AS TIMESTAMP)) AS DATE) AS month_start,
           COALESCE(SUM(amount) FILTER (WHERE type = 'income'),  0) AS income,
           COALESCE(SUM(amount) FILTER (WHERE type = 'expense'), 0) AS expense,
           COUNT(*)                                                 AS transaction_count
      FROM transactions
     WHERE user_id = :me                     -- the scope, before any filter
       AND occurred_on >= :date_from         -- ...the filters
     GROUP BY 1
     ORDER BY 1;
    ```

    **Why `income` and `expense` are two columns and not two rows.** Grouping by
    `(month, type)` is the naive shape and it returns *up to* two rows per month,
    which every client then has to pivot — and pivot carefully, since a month
    with only expenses yields one row, not two. `FILTER` does the pivot in the
    database, so each month arrives as exactly one row with both figures always
    present. One scan, one shape, no client-side reassembly.

    **Ascending, not descending.** The ledger sorts newest-first because that is
    how a human reads a list; a time axis runs left to right, and handing a chart
    library a reversed series is how you get a graph that runs backwards. No
    tiebreaker is needed here — unlike the paged ledger, `month_start` is unique
    across the result by construction, since it *is* the group key.
    """
    _require_sane_range(date_from, date_to)

    stmt = (
        select(
            MONTH_START.label("month_start"),
            _sum_where(Transaction.type == TransactionType.INCOME).label("income"),
            _sum_where(Transaction.type == TransactionType.EXPENSE).label("expense"),
            # `count()` with no argument renders `COUNT(*)`: every row in the
            # group, which is what "how many transactions in March" means.
            # `COUNT(column)` would skip NULLs in that column — the right tool
            # for a different question (see `category_breakdown`, where it is
            # deliberately not used for exactly this reason).
            func.count().label("transaction_count"),
        )
        .where(*_scope(current_user.id, account_id, date_from, date_to))
        # The same expression object as in the SELECT list, deliberately. GROUP
        # BY is evaluated before the output columns exist, so the expression is
        # repeated in the emitted SQL rather than referenced by its label.
        .group_by(MONTH_START)
        .order_by(MONTH_START)
    )

    # `.execute(...).all()` rather than `.scalars(...)`: this statement selects
    # four separate columns, not whole entities, so each result is a row-like
    # tuple with the labels above as attributes. `scalars()` would take only the
    # first column and silently throw the aggregates away.
    rows = db.execute(stmt).all()

    return MonthlySummaryRead(
        # The *resolved* bounds, not the requested ones — with no `date_from`,
        # the chart's left edge is wherever the user's history starts.
        date_from=date_from or (rows[0].month_start if rows else None),
        date_to=date_to or (rows[-1].month_start if rows else None),
        months=_fill_month_gaps(rows, date_from, date_to),
    )


@router.get(
    "/by-category",
    response_model=CategoryBreakdownRead,
    summary="Totals grouped by category, largest first",
)
def category_breakdown(
    current_user: CurrentUser,
    db: DbSession,
    type: Annotated[
        TransactionType,
        Query(description="Which side to break down; defaults to spending"),
    ] = TransactionType.EXPENSE,
    account_id: Annotated[int | None, Query(gt=0, description="Only this account")] = None,
    date_from: Annotated[date | None, Query(description="Inclusive lower bound")] = None,
    date_to: Annotated[date | None, Query(description="Inclusive upper bound")] = None,
) -> CategoryBreakdownRead:
    """The pie chart: how one side of the ledger divides across categories.

    ```sql
    SELECT t.category_id, c.name,
           SUM(t.amount) AS total,
           COUNT(*)      AS transaction_count,
           AVG(t.amount) AS average
      FROM transactions t
      LEFT JOIN categories c
             ON c.id = t.category_id
            AND c.user_id = :me        -- in the ON clause, not the WHERE
     WHERE t.user_id = :me
       AND t.type = :type
     GROUP BY t.category_id, c.name
     ORDER BY total DESC, c.name;
    ```

    **Why the join is a LEFT JOIN.** `category_id` is nullable — an imported or
    just-entered transaction is legitimately uncategorized. An inner join drops
    those rows, and the failure is the worst kind: the response still looks
    complete, the slices still render, and the pie is simply missing money. The
    outer join keeps them as a group with a NULL key, surfaced below as
    "Uncategorized", so the slices always add up to the total they claim to
    divide.

    **Why the ownership check on `categories` is in the ON clause.** Moving
    `c.user_id = :me` into the `WHERE` looks equivalent and silently undoes the
    outer join: uncategorized rows have `c.user_id IS NULL` after the join,
    `NULL = :me` is not true, and every one of them is filtered out. This is the
    classic way a LEFT JOIN degrades into an inner join. In the `ON` clause the
    condition constrains what may be *matched*, and unmatched rows still survive
    with NULLs — which is the behaviour that was wanted.

    (That check is belt-and-braces: `_require_owned_category` in the transactions
    router already refuses to file a transaction under someone else's category,
    so no such row should exist. It costs nothing here, and it means this query
    cannot leak a category name even if that invariant is ever broken by a bulk
    import or a migration.)

    **Why `type` is a filter and not a second grouping level.** Income and
    expense categories are disjoint by construction — `Category.type` exists
    precisely so "spending by category" cannot accidentally sum in a paycheck —
    so grouping by both would interleave two unrelated charts into one list, each
    slice holding a meaningless share of a combined total. One side per request
    keeps the denominator honest.

    **No `limit` here, unlike `GET /transactions`.** The row count is bounded by
    how many categories the user has created, not by how much they have spent —
    that is exactly what the aggregate bought. A top-N-plus-"Other" collapse is a
    real feature for a pie with forty slices, and it belongs in a `top=`
    parameter that also folds the remainder into a bucket; silently truncating
    here would produce shares that no longer sum to the total beside them.
    """
    _require_sane_range(date_from, date_to)

    total_expr = func.sum(Transaction.amount).label("total")

    stmt = (
        select(
            Transaction.category_id,
            Category.name.label("category_name"),
            total_expr,
            # `COUNT(*)`, not `COUNT(c.id)`. The latter counts non-NULL values of
            # that column, so the uncategorized group — whose `c.id` is NULL for
            # every row — would report a count of 0 beside a real total.
            func.count().label("transaction_count"),
            _avg_amount().label("average"),
        )
        .join(
            Category,
            # Explicit ON condition rather than letting SQLAlchemy infer it from
            # the foreign key, because the second half — the ownership check — is
            # not something it could infer. See the docstring for why that half
            # belongs here and not in the WHERE clause.
            (Category.id == Transaction.category_id) & (Category.user_id == current_user.id),
            isouter=True,
        )
        .where(*_scope(current_user.id, account_id, date_from, date_to))
        .where(Transaction.type == type)
        # `name` is grouped alongside the id rather than left out: PostgreSQL
        # only lets a selected column be omitted from GROUP BY when it is
        # functionally dependent on a grouped *primary key*, and the grouping key
        # here is `transactions.category_id`, not `categories.id`.
        .group_by(Transaction.category_id, Category.name)
        # `name` also serves as the sort tiebreaker: two categories with equal
        # totals would otherwise come back in whatever order the plan happened to
        # produce, so a chart's legend could reshuffle between two identical
        # requests. Same reasoning as `id DESC` on the paged ledger.
        .order_by(total_expr.desc(), Category.name)
    )

    rows = db.execute(stmt).all()

    # The denominator, summed from the group totals rather than fetched with a
    # second query. Every transaction in scope belongs to exactly one group — the
    # outer join guarantees the uncategorized ones are in the NULL group rather
    # than dropped — so the group totals are a partition of the whole, and adding
    # them up is exact.
    grand_total = sum((row.total for row in rows), start=Decimal(0))

    return CategoryBreakdownRead(
        type=type,
        total=_to_cents(grand_total),
        transaction_count=sum(row.transaction_count for row in rows),
        categories=[
            CategorySlice(
                category_id=row.category_id,
                # NULL here means the transaction had no category. Naming it
                # rather than passing the null through keeps every slice
                # labelled, so a legend never has a blank entry.
                category_name=row.category_name or UNCATEGORIZED_LABEL,
                total=_to_cents(row.total),
                transaction_count=row.transaction_count,
                average=_to_cents(row.average),
                # Percent of the grand total. Note these need not add to exactly
                # 100.00: each is rounded independently, so three equal thirds
                # come back as 33.33 three times. Sending `total` alongside is
                # what lets a client that cares reconcile against the real figure
                # instead of against the rounded shares.
                #
                # `or ZERO` covers only the case where the grand total is zero,
                # which means there were no rows at all — so the list this runs
                # over is empty and the branch never actually fires. It is here
                # so the field's type stays non-optional for clients.
                share=_percent(row.total, grand_total) or ZERO,
            )
            for row in rows
        ],
    )


@router.get(
    "/income-vs-expense",
    response_model=IncomeVsExpenseRead,
    summary="Totals for each side of the ledger, plus the savings rate",
)
def income_vs_expense(
    current_user: CurrentUser,
    db: DbSession,
    account_id: Annotated[int | None, Query(gt=0, description="Only this account")] = None,
    date_from: Annotated[date | None, Query(description="Inclusive lower bound")] = None,
    date_to: Annotated[date | None, Query(description="Inclusive upper bound")] = None,
) -> IncomeVsExpenseRead:
    """The headline numbers: money in, money out, and what stayed.

    ```sql
    SELECT type,
           SUM(amount) AS total,
           COUNT(*)    AS transaction_count,
           AVG(amount) AS average,
           MAX(amount) AS largest
      FROM transactions
     WHERE user_id = :me
     GROUP BY type;
    ```

    **The deliberate contrast with `/summary/monthly`.** That endpoint pivots
    income and expense into columns with `FILTER`, because it needs one row per
    month with both figures side by side. This one groups by `type` and gets
    *rows* — at most two — then pivots them into a single object in Python. Both
    shapes are correct; which is right depends on what the caller wants back:

      - `GROUP BY month` + `FILTER` — many groups, each needing both figures.
        Pivoting client-side would mean reassembling a series while coping with
        months that produced one row instead of two.
      - `GROUP BY type` — one group per figure, and the pivot is a two-key
        lookup. Writing four `FILTER` clauses to avoid it would trade a clear
        query for a wide one and save nothing.

    The trap in the second shape is what happens when a group is absent. A user
    with no income at all produces exactly one row, and code that reads `rows[0]`
    as income reports their expenses as earnings. That is why the pivot below
    goes through a dict with an explicit default rather than indexing — the empty
    case is the normal case for a new account, not an edge.

    **Why `net` is not just another aggregate.** It is `income - expense` over
    magnitudes, computed once both are known. `SUM(signed_amount)` isn't
    available: `signed_amount` is a Python property on the model, usable on a
    loaded object and invisible to the database (the model says so where it is
    defined). The SQL equivalent would be a `CASE` expression, and it would be a
    third figure that can disagree with the two beside it.
    """
    _require_sane_range(date_from, date_to)

    stmt = (
        select(
            Transaction.type,
            func.sum(Transaction.amount).label("total"),
            func.count().label("transaction_count"),
            _avg_amount().label("average"),
            func.max(Transaction.amount).label("largest"),
        )
        .where(*_scope(current_user.id, account_id, date_from, date_to))
        .group_by(Transaction.type)
    )

    # At most two rows, keyed by the group column. The `.get` with a `None`
    # default feeding `_side` is what makes a missing group read as zeros instead
    # of an IndexError or, worse, the other side's numbers.
    by_type = {row.type: row for row in db.execute(stmt).all()}

    income = _side(by_type.get(TransactionType.INCOME))
    expense = _side(by_type.get(TransactionType.EXPENSE))

    return IncomeVsExpenseRead(
        date_from=date_from,
        date_to=date_to,
        income=income,
        expense=expense,
        net=income.total - expense.total,
        # Percent of income kept. `_percent` returns None when income is zero,
        # and that null travels all the way out to the client — see the note on
        # the field in `schemas/summary.py` for why it isn't flattened to 0.
        savings_rate=_percent(income.total - expense.total, income.total),
    )
