"""CSV import — turn a bank statement into transactions, skipping the rows that
can't be turned into anything.

    POST /transactions/import       multipart upload    200 (a report, not rows)

**The rule this module exists to enforce: one bad row is not a bad file.** A
year of statement history arrives with a blank line at the end, a refund printed
in parentheses, and a memo containing a comma someone quoted wrong. Refusing the
whole upload over any of those means the user fixes one line, re-uploads, and
finds the next one — and does that eleven times. So every row is parsed
independently, a failure is recorded against its line number, and the survivors
are inserted. The response is a tally plus a defect list (`schemas/csv_import.py`
has the reasoning for that shape).

What that rule does *not* license is guessing. There is a real difference
between a row this module can read unambiguously and one it can only read by
picking an interpretation, and the second kind is rejected rather than imported
wrong. `04/03/2026` is the canonical case: it is the 4th of March in most of the
world and the 3rd of April in the United States, and no amount of context in a
CSV settles it. Importing it under a guess produces a ledger that is *quietly*
wrong — the rows are all there, the totals all look plausible, and the error
only surfaces months later in a monthly chart nobody can reconcile. A rejected
row, by contrast, announces itself immediately and costs one column edit. The
same reasoning governs `45,20` (see `_parse_amount`) and an unknown category
name (see `_CategoryIndex.resolve`).

**Three things about the request itself are worth reading the code for.**

1. *The handler is `def`, not `async def`* — see the note on `_read_capped`.
2. *The upload is read with a byte cap*, because nothing upstream imposes one.
3. *`account_id` is a form field, not a CSV column.* One upload is one bank
   statement, which is one account, so asking per row would invite a file where
   half the rows go somewhere the caller doesn't own. Asking once means the
   ownership check is one SELECT, performed before a single row is parsed.

**Ownership works as it does everywhere else in this app**: the account is
proven to belong to `current_user` up front, every category name is resolved
against *that user's* categories only, and the `user_id` written on every row
comes from the access token. A CSV cannot name a `user_id`, an `account_id` it
doesn't own, or a category belonging to someone else — the first because no
column maps to it, the second and third because the lookups are scoped.

**What this milestone deliberately does not do: detect duplicates.** Re-uploading
an overlapping statement will import the overlap a second time. That is a real
problem and it is deliberately not solved here, because solving it properly is a
schema change and not a check — it needs a stable per-transaction fingerprint
(the bank's own reference id where the export has one, otherwise a hash of
account + date + amount + description) stored in a unique index, so the guarantee
comes from PostgreSQL rather than from a SELECT that races. Doing it halfway —
"skip rows that look like existing ones" — is worse than not doing it: it drops
the second identical coffee someone genuinely bought on the same day.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.category import Category
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.csv_import import ImportSummaryRead, RowError
from app.schemas.transaction import TransactionCreate

router = APIRouter(prefix="/transactions", tags=["import"])


# --- Limits ----------------------------------------------------------------
#
# Module constants rather than settings, following `MAX_MONTHS` in
# `routers/summary.py`: these are guardrails against a pathological input, not
# knobs an operator tunes per deployment. The day one of them needs to differ
# between staging and production is the day it moves to `core/config.py`.

# 5 MB is a decade of dense statement history and about two orders of magnitude
# more than a year of it. The number matters less than the fact that there is
# one: without a cap, "upload a file" is an endpoint that allocates however much
# memory the client feels like sending.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Read in chunks rather than in one call, so the cap can be enforced *during*
# the read. Checking `len(file.read())` afterwards means the oversized file is
# already in memory, which is the thing the check was supposed to prevent.
CHUNK_SIZE = 64 * 1024

# The second cap, and not redundant with the first: 5 MB of pathologically
# short rows is a great many transactions, and every one of them becomes an ORM
# object held in memory until the commit.
MAX_ROWS = 10_000

# How many individual failures the response will describe. See the note in
# `schemas/csv_import.py` on why the *count* is exact but the list is a sample.
MAX_REPORTED_ERRORS = 100


# --- The file format -------------------------------------------------------

# Header aliases, because "bank statement CSV" is not a format — it is a dozen
# formats that agree on very little. Normalizing here (lowercased, spaces to
# underscores) means `Transaction Date`, `transaction_date` and `DATE` all
# arrive at the same place, and adding support for another bank is a line in
# this dict rather than a branch in the parser.
#
# Only `date` and `amount` are required; the rest of the file may be absent
# entirely. Note there is no alias for `account` or for anything resembling a
# user or an id: a column that isn't in this dict cannot influence the import,
# which is what makes "a CSV cannot name someone else's account" a property of
# the mapping rather than a check somebody has to remember.
COLUMN_ALIASES: dict[str, str] = {
    "date": "date",
    "occurred_on": "date",
    "transaction_date": "date",
    "posted_date": "date",
    "posting_date": "date",
    "value_date": "date",
    "amount": "amount",
    "value": "amount",
    "type": "type",
    "direction": "type",
    "transaction_type": "type",
    "description": "description",
    "memo": "description",
    "narrative": "description",
    "details": "description",
    "payee": "description",
    "category": "category",
}

# The direction column, in the spellings banks actually use. A statement that
# has this column has it *instead* of signed amounts, which is why both
# conventions have to be supported — see `_resolve_direction`.
TYPE_SYNONYMS: dict[str, TransactionType] = {
    "income": TransactionType.INCOME,
    "credit": TransactionType.INCOME,
    "cr": TransactionType.INCOME,
    "deposit": TransactionType.INCOME,
    "in": TransactionType.INCOME,
    "expense": TransactionType.EXPENSE,
    "debit": TransactionType.EXPENSE,
    "dr": TransactionType.EXPENSE,
    "withdrawal": TransactionType.EXPENSE,
    "out": TransactionType.EXPENSE,
    "payment": TransactionType.EXPENSE,
    "purchase": TransactionType.EXPENSE,
}

# Where `csv.DictReader` puts the values of a row that has *more* fields than
# the header. Paired with `restval=None` for rows that have fewer, the two
# together are how a ragged line is detected at all — see `_ragged_reason`.
EXTRA_FIELDS_KEY = "__extra_fields__"

# Rejected up front, with a message naming the fix. `DD/MM/YYYY` and `MM/DD/YYYY`
# are indistinguishable for the first twelve days of every month, and a parser
# that picks one is wrong about roughly a third of a real statement's rows while
# reporting complete success.
AMBIGUOUS_SLASHED_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")

# Currency symbols to strip before parsing an amount. Written as escapes rather
# than as literal glyphs so the source stays pure ASCII: dollar, euro, pound,
# yen, rupee.
CURRENCY_SYMBOLS = re.compile("[$€£¥₹]")

# What is left must look exactly like this: an optional sign, then either a run
# of digits or a properly thousands-grouped number, then an optional decimal
# part. The grouping alternative is the important half — it accepts `1,234.56`
# while rejecting `45,20`, which is the European spelling of forty-five euros
# twenty and would otherwise be read as four thousand five hundred and twenty.
DECIMAL_AMOUNT = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$")


class _RowRejected(Exception):
    """A single row could not be turned into a transaction.

    An exception rather than a returned `None` or an `(ok, error)` tuple so the
    per-row parsing code reads as a straight line — parse the date, parse the
    amount, resolve the category — with one handler at the bottom of the loop.
    The alternative threads a "did that work?" check between every step, and the
    step where somebody forgets it is the step that imports a bad row.

    Scoped to this module (leading underscore, never raised past the loop): it
    means "skip this row", which is only meaningful inside the loop that has
    another row to move on to.
    """

    def __init__(self, reason: str, field: str | None = None, value: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.field = field
        self.value = value


# --- Cell parsers ----------------------------------------------------------
#
# Each takes the raw string as it appeared in the file and returns a real Python
# value, or raises `_RowRejected` naming the cell. None of them touches the
# database, and none of them enforces a business rule — `amount > 0`, the
# future-date ceiling and the description length are all `TransactionCreate`'s
# job (see `_build_payload`). The split is the same one `schemas/transaction.py`
# describes: shape and type here, meaning there.


def _parse_date(raw: str) -> date:
    """Read `occurred_on` from a cell, accepting only unambiguous spellings.

    ISO 8601 and nothing else, for the reason in the module docstring: every
    other common statement format is either identical to ISO or ambiguous with
    another format, and there is no third category. `2026/03/04` is accepted
    because slashes are the only thing separating it from ISO; `04/03/2026` is
    rejected by name, with a message saying what to do about it, because a
    parser that answers it at all is answering a question the file did not ask.

    A trailing time component is dropped rather than rejected: exports that
    stamp `2026-03-04 00:00:00` are common, and `occurred_on` is a `Date` on
    purpose (see the model), so there is nothing to lose in discarding it.
    """
    text = raw.strip()
    if not text:
        raise _RowRejected("date is required", field="date", value=raw)

    # `split(sep, 1)[0]` on both separators handles "2026-03-04T00:00:00" and
    # "2026-03-04 00:00:00" without caring which one arrived.
    head = text.split("T", 1)[0].split(" ", 1)[0]

    if AMBIGUOUS_SLASHED_DATE.match(head):
        raise _RowRejected(
            f"{head!r} is ambiguous (DD/MM/YYYY and MM/DD/YYYY are indistinguishable); "
            "re-export the file with ISO dates, e.g. 2026-03-04",
            field="date",
            value=raw,
        )

    try:
        return date.fromisoformat(head.replace("/", "-"))
    except ValueError as exc:
        raise _RowRejected(
            f"{head!r} is not a valid ISO date (expected YYYY-MM-DD)",
            field="date",
            value=raw,
        ) from exc


def _parse_amount(raw: str) -> Decimal:
    """Read a possibly-signed money value from a cell.

    Returns the value *with its sign*, which `_resolve_direction` then converts
    into the magnitude-plus-`type` pair the model stores. Keeping those two
    steps apart is what lets one function deal with the file's notation and the
    other with the ledger's convention.

    Three notations are accommodated because statements really do use them:
    a leading currency symbol, thousands separators, and `(45.20)` for a
    negative — the accounting convention, which predates the minus sign and
    still ships out of every spreadsheet with a currency format applied.

    And one is refused: a comma in any position other than a thousands group.
    `45,20` is forty-five twenty in most of Europe, and stripping commas
    indiscriminately — the obvious implementation — turns it into 4520. That is
    a hundred-fold error, on a number, in a financial ledger, with no symptom
    at all until someone reads their spending report. `Decimal` is used
    throughout for the same family of reasons `Account.balance` is `Numeric`:
    binary floats cannot hold most cent values exactly.
    """
    text = raw.strip()
    if not text:
        raise _RowRejected("amount is required", field="amount", value=raw)

    negative_by_parentheses = text.startswith("(") and text.endswith(")")
    if negative_by_parentheses:
        text = text[1:-1].strip()

    text = CURRENCY_SYMBOLS.sub("", text)
    # `str.split()` with no argument splits on *any* unicode whitespace, which
    # includes the non-breaking space some locales use as a thousands separator.
    text = "".join(text.split())

    if not DECIMAL_AMOUNT.match(text):
        if "," in text:
            raise _RowRejected(
                f"{raw.strip()!r} uses a comma in a position that is not a thousands "
                "separator; write the decimal point as '.' (e.g. 45.20)",
                field="amount",
                value=raw,
            )
        raise _RowRejected(f"{raw.strip()!r} is not a number", field="amount", value=raw)

    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - the regex already rejects these
        raise _RowRejected(f"{raw.strip()!r} is not a number", field="amount", value=raw) from exc

    return -value if negative_by_parentheses else value


def _parse_type(raw: str | None) -> TransactionType | None:
    """Read the direction column, if the file has one.

    `None` means "the file did not say", which is a legitimate answer and not an
    error: a statement with signed amounts carries the direction in the sign.
    Distinguishing "absent" from "unreadable" is the whole job here — the first
    hands the decision to `_resolve_direction`, the second is a bad row.
    """
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None

    declared = TYPE_SYNONYMS.get(text)
    if declared is None:
        raise _RowRejected(
            f"{raw.strip()!r} is not a recognised direction "
            f"(try one of: {', '.join(sorted(TYPE_SYNONYMS))})",
            field="type",
            value=raw,
        )
    return declared


def _resolve_direction(
    amount: Decimal,
    declared: TransactionType | None,
) -> tuple[Decimal, TransactionType]:
    """Reconcile the two ways a statement can express direction.

    The model stores amounts as positive magnitudes with `type` carrying the
    sign (see `Transaction.amount` and the CHECK constraint behind it). A CSV
    may express the same fact either way:

        -45.20            no type column     ->  the sign is the direction
        45.20, "debit"    type column        ->  the column is the direction
        -45.20, "debit"   both, agreeing     ->  fine, the magnitude is taken

    The fourth combination is the one worth handling explicitly:

        -45.20, "credit"  both, disagreeing  ->  rejected

    A negative amount labelled as income is not a value this function can
    resolve — it is a file where two columns contradict each other, which
    usually means the wrong column got mapped or the export was assembled by
    hand. Both readings are defensible, so picking one would be a coin flip
    recorded as a fact. The row is skipped and the contradiction is reported.

    Zero has no direction either, and is rejected here rather than left to
    `TransactionCreate`'s `gt=0` so the message can say *why* zero is a problem
    in a file whose sign convention is load-bearing.
    """
    if amount == 0:
        raise _RowRejected(
            "amount is zero, so the row has no direction and nothing to record",
            field="amount",
            value=str(amount),
        )

    if declared is None:
        # The near-universal statement convention: money leaving the account is
        # negative. This is the branch that runs for exports with no direction
        # column, which is most of them.
        return abs(amount), TransactionType.EXPENSE if amount < 0 else TransactionType.INCOME

    if amount < 0 and declared is TransactionType.INCOME:
        raise _RowRejected(
            f"amount {amount} is negative but the row is labelled 'income'; "
            "the sign and the type column disagree",
            field="type",
            value=declared.value,
        )

    return abs(amount), declared


# --- Category resolution ---------------------------------------------------


class _CategoryIndex:
    """Every category the caller owns, keyed for lookup by name.

    **Loaded once per upload, not once per row.** This is the N+1 problem in its
    most avoidable form: a `SELECT ... WHERE name = ?` inside the row loop turns
    a 400-row import into 401 round trips, and the file almost certainly names
    only four or five distinct categories. One query up front, a dict lookup per
    row. The user's category list is bounded by what a person will maintain by
    hand, so holding all of it in memory costs nothing worth measuring.

    The matching is case-insensitive because a human typed the CSV's category
    column and will not have matched the app's capitalisation. That opens one
    edge worth handling honestly: the database's uniqueness constraint is
    `(user_id, name)` and PostgreSQL compares those case-*sensitively*, so a
    user may legitimately own both "Food" and "food". A case-insensitive lookup
    for "FOOD" then has two right answers, and this class says so (see
    `resolve`) rather than silently taking whichever the query returned first.
    """

    def __init__(self, categories: Iterable[Category]) -> None:
        self._by_exact: dict[str, Category] = {}
        # `None` as a value means "two or more categories fold to this key" —
        # the marker for the ambiguous case described above.
        self._by_folded: dict[str, Category | None] = {}

        for category in categories:
            self._by_exact[category.name] = category
            # `casefold`, not `lower`: it is the aggressive form intended for
            # caseless *matching* rather than display, and it handles the cases
            # `lower` gets wrong in non-English text.
            key = category.name.casefold()
            self._by_folded[key] = None if key in self._by_folded else category

    def resolve(self, raw: str, transaction_type: TransactionType) -> int:
        """Map a category name from the file to one of this user's category ids.

        Raises `_RowRejected` for a name that does not resolve — the row is
        skipped rather than imported uncategorized. That is the stricter of two
        defensible choices, and it is the one that tells the truth: importing
        the row with `category_id = NULL` produces a "success" whose result is
        wrong in a way nobody will look for, and the whole point of the error
        report is that the file's problems arrive with the file. `dry_run=true`
        exists so the fix is one cycle — preview, see which names are missing,
        create them, import.

        The alternative, creating categories on the fly, is rejected for a
        narrower reason: it makes every typo permanent. One misspelt
        "Grocerys" and the user's category list has a new member forever, and
        the spending report has a slice nobody can account for.
        """
        name = raw.strip()

        # Exact first, and it can never be ambiguous — the database's unique
        # constraint guarantees one row per (user, exact name).
        category = self._by_exact.get(name)
        if category is None:
            key = name.casefold()
            if key not in self._by_folded:
                raise _RowRejected(
                    f"no category named {name!r} (create it first, or clear the column "
                    "to import the row uncategorized)",
                    field="category",
                    value=raw,
                )
            category = self._by_folded[key]
            if category is None:
                raise _RowRejected(
                    f"{name!r} matches more than one of your categories case-insensitively; "
                    "write it exactly as it is stored",
                    field="category",
                    value=raw,
                )

        # The same check `_require_owned_category` performs in
        # `routers/transactions.py`, and for the same reason: `Category.type`
        # exists so "spending by category" is a join that cannot accidentally
        # sum in a paycheck, and a single expense filed under an income category
        # is exactly the row that breaks that promise. A bulk import is where
        # that would happen at scale and unnoticed.
        if category.type is not transaction_type:
            raise _RowRejected(
                f"category {category.name!r} is an {category.type.value} category and "
                f"cannot be used on an {transaction_type.value} transaction",
                field="category",
                value=raw,
            )

        return category.id


# --- Database helpers ------------------------------------------------------
#
# `_require_owned_account` and `_commit_or_conflict` are near-copies of the
# helpers in `routers/transactions.py`. That is the second copy, which
# `routers/summary.py` already named as the acceptable one: "two copies of a
# small check is the cheaper mistake for now; the third caller is when it earns
# a home in a shared module". Importing the originals is the alternative, and it
# would make one router depend on another — `main.py` imports the routers and
# nothing imports back, and keeping that arrow pointing one way is worth more
# than the twelve lines below.


def _require_owned_account(db: Session, user_id: int, account_id: int) -> Account:
    """Resolve the form's `account_id` against this user's accounts, or 404.

    Performed once, before a byte of the file is parsed. That ordering is the
    point: a caller probing for other people's account ids gets the same 404
    whether they attach a valid CSV or an empty one, and a legitimate caller who
    picked the wrong account does not wait for 10,000 rows to be parsed first.

    404 rather than 403 for an account that exists but belongs to someone else,
    on the reasoning `routers/transactions.py` sets out at length: "not yours"
    and "not there" have to be indistinguishable from outside.
    """
    account = db.scalar(
        select(Account).where(Account.id == account_id, Account.user_id == user_id)
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found",
        )
    return account


def _commit_or_conflict(db: Session) -> None:
    """Commit the batch, turning a foreign-key violation into a 409, not a 500.

    The window is wider here than on a single POST: this transaction opened when
    the account was checked and stays open across the whole parse, so a
    `DELETE /accounts/{id}` from the user's other tab has the duration of the
    import to land in. PostgreSQL's foreign key is what actually guarantees the
    reference; the check up front exists to produce a good error in the common
    case, not to make the constraint redundant.

    Note what a failure here costs: the entire batch. That is the intended
    behaviour — see the commit call in the handler.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The referenced account or category no longer exists",
        ) from exc


# --- Reading the upload ----------------------------------------------------


def _read_capped(upload: UploadFile) -> bytes:
    """Read the whole upload into memory, refusing to exceed `MAX_UPLOAD_BYTES`.

    **Why `upload.file.read(...)` and not `await upload.read(...)`.** The async
    API would force this handler to be `async def`, and every database call in
    it is blocking psycopg2. A blocking call inside an `async def` handler runs
    *on the event loop*, so one slow import would stall every other request the
    process is serving — including ones that have nothing to do with this route.
    A plain `def` handler is dispatched to a threadpool instead, where blocking
    is exactly what is expected, and `UploadFile.file` is the synchronous file
    object underneath. Every other route in this app is `def` for the same
    reason; this is the one where the temptation to differ shows up.

    **Why a cap at all.** Neither Starlette nor FastAPI limits request body size
    by default, and `.read()` with no argument on an unbounded upload is a
    memory bomb with a one-line trigger. A reverse proxy in front of this will
    usually have its own limit, which is a good reason to configure one and no
    reason to omit this: the app should not depend on deployment topology to
    avoid falling over.

    Reading in chunks is what makes the check meaningful — the size is tested
    while the bytes arrive, so the oversized file is abandoned partway rather
    than measured after it is already resident.

    (Starlette has already spooled the body to a temporary file on disk if it
    was large, so "in memory" starts here, not at the socket. This still ends
    with the whole file in one `bytes` object, which is a deliberate simplicity:
    a streaming parser would remove the cap's reason to exist, at the price of
    a row loop that cannot be read top to bottom. 5 MB is the trade.)
    """
    chunks: list[bytes] = []
    total = 0

    while chunk := upload.file.read(CHUNK_SIZE):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
            )
        chunks.append(chunk)

    return b"".join(chunks)


def _decode(raw: bytes) -> str:
    """Decode the upload as UTF-8, tolerating a byte-order mark.

    `utf-8-sig` rather than `utf-8` is not a nicety. Excel — which is what
    produces a large share of the CSVs a personal finance app will ever see —
    writes a BOM at the start of the file, and under plain `utf-8` those three
    bytes decode to an invisible character that becomes part of the *first
    header name*. `date` silently becomes `﻿date`, the required-column
    check fails, and the error says the file has no date column while the user
    is looking straight at one. The `-sig` suffix strips it if present and
    changes nothing if it is not.

    Anything that is not valid UTF-8 is refused rather than guessed at. The
    tempting fallback is cp1252 (Excel's default on Windows), which would
    rescue a file containing a pound sign — and would also decode genuinely
    broken bytes into plausible-looking garbage, since cp1252 has a mapping for
    almost every byte. Guessing an encoding is the same class of mistake as
    guessing a date format: it turns a loud failure into a quiet corruption,
    and here it would land in the description column, which is the one field
    nothing downstream validates.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "File is not valid UTF-8. Re-save it as CSV UTF-8 "
                "(in Excel: File > Save As > 'CSV UTF-8')."
            ),
        ) from exc


def _map_header(fieldnames: list[str] | None) -> dict[str, str]:
    """Match the file's header row against `COLUMN_ALIASES`, or 422.

    Returns canonical name -> the header exactly as it appears in the file,
    because that original string is what keys every row dict `DictReader`
    produces. Doing this once here rather than normalizing each row's keys is
    the difference between one pass over five strings and one pass over five
    strings per row.

    Checked before any row is read, so the wrong file — last year's PDF export
    saved as `.csv`, a spreadsheet of something else entirely — fails in one
    message instead of producing ten thousand identical row errors. Failing on
    the header is the cheapest failure this endpoint has.

    Duplicate aliases resolve first-one-wins by header order: a file with both
    `payee` and `memo` maps `description` to `payee` and ignores `memo`. Erroring
    would be defensible and is worse in practice, because that pair of columns is
    common and neither choice loses a row.
    """
    if not fieldnames:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File is empty or has no header row",
        )

    mapping: dict[str, str] = {}
    for original in fieldnames:
        if original is None:
            continue
        key = original.strip().lower().replace(" ", "_")
        canonical = COLUMN_ALIASES.get(key)
        if canonical is not None and canonical not in mapping:
            mapping[canonical] = original

    missing = [name for name in ("date", "amount") if name not in mapping]
    if missing:
        seen = ", ".join(repr(name) for name in fieldnames if name is not None)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Missing required column(s): {', '.join(missing)}. "
                f"The file's header row is: {seen or '(empty)'}"
            ),
        )

    return mapping


# --- Row handling ----------------------------------------------------------


def _cell(row: Mapping[str, str | None], header: dict[str, str], canonical: str) -> str | None:
    """Read one canonical column out of a raw row, or `None` if absent."""
    original = header.get(canonical)
    if original is None:
        return None
    return row.get(original)


def _is_blank(row: Mapping[str, str | None]) -> bool:
    """True for a row that carries no data at all.

    `DictReader` already drops genuinely empty lines, but a trailing `,,,` — the
    shape a spreadsheet leaves behind when a formatted range extends past the
    data — arrives as a full row of empty strings. Counting those as failures
    would report a clean file as having twelve bad rows, so they are skipped
    silently and excluded from `total_rows`.
    """
    for value in row.values():
        if isinstance(value, list):  # the ragged overflow under EXTRA_FIELDS_KEY
            if any(item and item.strip() for item in value):
                return False
        elif value is not None and value.strip():
            return False
    return True


def _ragged_reason(row: Mapping[str, str | None]) -> str | None:
    """Describe a row whose field count doesn't match the header, else `None`.

    This is what `restkey`/`restval` on the reader are for, and the reason both
    are set explicitly: by default a short row's missing columns are simply
    absent from the dict and a long row's extra values are *discarded*. Either
    way the row parses, and either way the values that did arrive are in the
    wrong columns — an amount read out of the description field, a date read out
    of nothing.

    A ragged row is nearly always a quoting bug (an unescaped comma inside a
    memo), and importing it would file a real transaction against the wrong
    date or amount. Skipping it is the only safe reading, and it is reported
    without a `field` because no single column is at fault.
    """
    if EXTRA_FIELDS_KEY in row:
        return "row has more fields than the header (check for an unquoted comma)"
    if any(value is None for key, value in row.items() if key != EXTRA_FIELDS_KEY):
        return "row has fewer fields than the header"
    return None


def _build_payload(
    account_id: int,
    category_id: int | None,
    amount: Decimal,
    transaction_type: TransactionType,
    occurred_on: date,
    description: str | None,
) -> TransactionCreate:
    """Run the parsed row through the same schema `POST /transactions` uses.

    **This is the most important line in the module.** Everything above turns
    the file's notation into Python values; this hands those values to
    `TransactionCreate`, which is where the *rules* live — amount strictly
    positive, at most two decimal places and twelve digits, no dates beyond
    tomorrow, description trimmed to `None` when blank and capped at 255.

    Re-implementing those checks here is the obvious alternative and it is a
    trap, because the two copies would not stay equal. The failure is
    directional and quiet: the import path is the one that gets thousands of
    rows at a time and no human reading them, so it is the path where a rule
    that quietly went missing does the most damage before anyone notices. One
    schema, two callers, no drift.

    `ValidationError` is caught by the loop and rendered into the row report, so
    a rule added to the schema later starts producing sensible per-row messages
    here without this module being touched.
    """
    return TransactionCreate(
        account_id=account_id,
        category_id=category_id,
        amount=amount,
        type=transaction_type,
        occurred_on=occurred_on,
        description=description,
    )


def _first_error(exc: ValidationError, row_number: int) -> RowError:
    """Render a Pydantic failure as one line of the import report.

    Only the first error, deliberately. A row that violates two rules is a row
    the user will fix and re-upload either way, and reporting both doubles the
    response for a file where every row is wrong in the same two ways.
    """
    detail = exc.errors()[0]
    location = detail.get("loc") or ()
    raw_input = detail.get("input")
    return RowError(
        row=row_number,
        field=str(location[0]) if location else None,
        value=None if raw_input is None else str(raw_input),
        reason=detail.get("msg", "invalid value"),
    )


# --- Endpoint --------------------------------------------------------------


@router.post(
    "/import",
    response_model=ImportSummaryRead,
    summary="Import transactions from a bank-statement CSV",
)
def import_transactions_csv(
    current_user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File(description="A CSV with at least `date` and `amount`")],
    account_id: Annotated[int, Form(gt=0, description="The account this statement belongs to")],
    dry_run: Annotated[
        bool,
        Query(description="Parse and validate, reporting what would happen, but write nothing"),
    ] = False,
) -> ImportSummaryRead:
    """Parse an uploaded CSV and insert every row that survives validation.

    Returns 200 with a report even when most of the file was rejected, because
    partial success is the normal outcome of a bulk import and there is no
    status code that means "mostly". The interesting failures — a file that is
    too large, undecodable, or missing its required columns — are the ones the
    endpoint cannot produce a per-row answer for, and those are the ones that
    get a 4xx.

    **Why the whole batch is one commit.** The good rows go in together or not
    at all. Committing per row would mean a failure halfway through leaves a
    half-imported statement that the user then has to reconcile by hand before
    they can retry — the worst of both designs, since the rows that made it are
    exactly the ones a re-upload would duplicate. One `INSERT` batch, one
    transaction, one outcome to reason about.

    Note also that `dry_run` runs *this same code path* and stops one line short
    of the commit. A preview implemented as a separate, simpler validation pass
    would be a preview of a different program, and the rows it disagreed about
    would be the ones that mattered.
    """
    # Ownership before parsing: cheapest check first, and it is the one that
    # must not be reachable around. Note the account is fetched but not
    # otherwise used — proving it belongs to `current_user` is the entire
    # purpose, and it is what makes writing `user_id=current_user.id` alongside
    # this `account_id` correct rather than corrupting (see the note on the
    # denormalized column in `models/transaction.py`).
    _require_owned_account(db, current_user.id, account_id)

    text = _decode(_read_capped(file))

    # `newline=""` is the incantation the `csv` docs require and the one that is
    # always omitted. Without it the text stream translates line endings before
    # the CSV reader sees them, which corrupts any field containing a genuine
    # embedded newline — a two-line address in a memo, most commonly. The reader
    # handles line endings itself; the stream must not help.
    stream = io.StringIO(text, newline="")

    # `csv.DictReader`, never `line.split(",")`. The split version is four
    # characters shorter and wrong on the first row containing
    # `"COFFEE, LARGE"` — it silently shifts every field after the comma into
    # the wrong column, which for this file means an amount parsed out of a
    # description. Quoting, escaped quotes and embedded newlines are the format,
    # not edge cases, and this is the parser that implements them.
    reader = csv.DictReader(stream, restkey=EXTRA_FIELDS_KEY, restval=None)
    header = _map_header(reader.fieldnames)

    categories = _CategoryIndex(
        db.scalars(select(Category).where(Category.user_id == current_user.id)).all()
    )

    pending: list[Transaction] = []
    errors: list[RowError] = []
    total_rows = 0
    failed = 0

    for row in reader:
        if _is_blank(row):
            continue

        total_rows += 1
        if total_rows > MAX_ROWS:
            # Raised mid-stream rather than after the loop, so a pathological
            # file stops costing memory at the limit instead of at its end.
            # Nothing has been committed at this point, so nothing is written.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"File contains more than {MAX_ROWS} rows; split it and import in parts",
            )

        # The physical line the reader finished this record on. Not the same as
        # a record index when a field contains an embedded newline, and it is
        # the more useful of the two anyway: it is the line a person will find
        # when they open the file to fix it.
        row_number = reader.line_num

        try:
            ragged = _ragged_reason(row)
            if ragged is not None:
                raise _RowRejected(ragged)

            occurred_on = _parse_date(_cell(row, header, "date") or "")
            signed_amount = _parse_amount(_cell(row, header, "amount") or "")
            amount, transaction_type = _resolve_direction(
                signed_amount, _parse_type(_cell(row, header, "type"))
            )

            # An absent or blank category column is not an error — the column is
            # optional and `Transaction.category_id` is nullable precisely so an
            # imported row can be uncategorized (see the model). A *named* one
            # that doesn't resolve is a different matter; `resolve` raises.
            raw_category = _cell(row, header, "category")
            category_id = (
                categories.resolve(raw_category, transaction_type)
                if raw_category and raw_category.strip()
                else None
            )

            payload = _build_payload(
                account_id=account_id,
                category_id=category_id,
                amount=amount,
                transaction_type=transaction_type,
                occurred_on=occurred_on,
                description=_cell(row, header, "description"),
            )

        except _RowRejected as exc:
            failed += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(
                    RowError(row=row_number, field=exc.field, value=exc.value, reason=exc.reason)
                )
            continue

        except ValidationError as exc:
            failed += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(_first_error(exc, row_number))
            continue

        pending.append(
            Transaction(
                # From the token, never from the file. There is no column that
                # maps to it and no code path that reads one.
                user_id=current_user.id,
                account_id=account_id,
                # `category_id=`, not `category=`. Assigning the ORM *object*
                # would set the backref on a category already in this session,
                # and the save-update cascade would then pull this transaction
                # into the session on flush — quietly defeating `dry_run`, which
                # relies on nothing being added until the line below.
                category_id=payload.category_id,
                amount=payload.amount,
                type=payload.type,
                occurred_on=payload.occurred_on,
                description=payload.description,
            )
        )

    if pending and not dry_run:
        # One `add_all` and one commit, not `db.add(...)` + `db.commit()` per
        # row. The per-row version opens and closes a transaction 400 times,
        # which is 400 fsyncs and 400 opportunities to end up half-imported;
        # this is one transaction with one outcome.
        #
        # The INSERTs *inside* it are batched by SQLAlchemy 2.0's
        # "insertmanyvalues", and the reason that works here is worth knowing,
        # because it is a property of the database rather than of `add_all`.
        # The ORM needs the generated `id` of every row it writes. Where the
        # driver offers `cursor.lastrowid` it can only learn that one row at a
        # time, so it emits one INSERT per object — which is exactly what
        # happens on SQLite. PostgreSQL has no lastrowid at all
        # (`postfetch_lastrowid` is False for psycopg2), so the ORM fetches the
        # ids with RETURNING instead — and RETURNING works fine on a multi-row
        # INSERT. Against this app's actual database the batch therefore goes
        # out as `INSERT ... VALUES (...), (...), ... RETURNING id`, in pages of
        # a thousand, rather than as 400 statements.
        #
        # `bulk_save_objects` is the SQLAlchemy 1.x tool for this job, and it is
        # legacy in 2.0 precisely because the above now happens without it.
        #
        # Nothing is read back afterwards — no `db.refresh`, no RETURNING the
        # response needs. The report is a tally, so serializing several thousand
        # freshly-created rows to build it would be work done purely to throw
        # away (compare `create_transaction`, which refreshes because it returns
        # the row it made).
        db.add_all(pending)
        _commit_or_conflict(db)

    return ImportSummaryRead(
        filename=file.filename,
        account_id=account_id,
        dry_run=dry_run,
        total_rows=total_rows,
        # `len(pending)` rather than a counter incremented in the loop: the list
        # is the thing that was actually inserted, so the number cannot drift
        # from it. `imported + failed == total_rows` holds by construction.
        imported=len(pending),
        failed=failed,
        errors=errors,
        errors_truncated=failed > len(errors),
    )
