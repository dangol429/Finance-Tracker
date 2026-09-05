"""#16 — Natural-language spending queries, answered from real SQL.

    "How much did I spend on food in June?"
      -> the model emits summarize_transactions(date_from="2026-06-01",
                                                date_to="2026-06-30",
                                                type="expense",
                                                category_ids=[3, 7])
      -> PostgreSQL answers  {"total": "412.30", "transaction_count": 9}
      -> the model writes    "You spent 412.30 on food in June, across 9
                              transactions."

**The model never sees the ledger and never does arithmetic.** It sees a
question, a list of the user's category and account *names*, and two tools. It
chooses a structured query; this module runs that query with `SUM` and
`GROUP BY`; the model turns the result into a sentence. Every figure in the
answer came out of the database.

That division is the whole point, and the alternative shows why. The obvious
implementation — fetch the user's transactions, paste them into the prompt, ask
for an answer — fails in three separate ways at once. It does not scale (a year
of history does not fit, and truncating it silently changes the answer). It is
slow and expensive (thousands of rows re-sent on every question). And it asks a
language model to add up a column of numbers, which it will do fluently and
sometimes wrongly, with no signal at all about which time it got it wrong. A
wrong total in a finance app that *looks* right is the worst possible output.

**Two tools, not three.** There is no `list_categories` tool: the user's
categories and accounts are small, hand-maintained lists, so they go straight
into the system prompt. That is a round trip saved on every question, and it is
what lets the model map "food" onto the two category ids that actually mean food
for *this* user before it makes any call at all. Tools are for the data that is
unbounded; context is for the data that is not.

**Why a manual loop rather than the SDK's tool runner.** The runner would drive
the request/execute/respond cycle in a few lines, and for most agents it is the
right choice. Two things here argue against it. Every tool call has to be
recorded — arguments *and* result — because that trail is the `evidence` this
endpoint returns, and it is the feature that makes an answer checkable rather
than merely fluent. And the tools are not free functions: each one closes over
this request's database session and user id, which is what makes "scoped to the
caller" a property of the executor rather than a rule the model is asked to
follow. The loop below is fifteen lines and owns both.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

import anthropic
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import ColumnElement, Row, func, select
from sqlalchemy.orm import Session

from app.ai.client import translate_api_errors, usage_of
from app.core.config import settings
from app.models.account import Account
from app.models.category import Category
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.models.user import User

# The month-bucketing expression, imported rather than rewritten. It carries two
# non-obvious casts that exist to keep `date_trunc` out of timezone space — see
# the long note above its definition in `routers/summary.py`. A second copy here
# would be a second chance to get those casts wrong, and the symptom of getting
# them wrong is a purchase landing in the neighbouring month, which nobody
# notices until a chart is being reconciled against a bank statement.
from app.routers.summary import MONTH_START, UNCATEGORIZED_LABEL
from app.schemas.ai import AiQueryRead, EvidenceStep

# --- Limits ----------------------------------------------------------------
#
# Module constants rather than settings, following `MAX_MONTHS` in
# `routers/summary.py` and `MAX_ROWS` in `routers/csv_import.py`: guardrails
# against a pathological interaction, not knobs an operator tunes.

# How many times the model may call tools before the loop gives up. Each turn is
# a paid API call, so an unbounded loop is an unbounded bill triggered by one
# question — and a model that has not answered in six queries is not converging.
# Three or four is the realistic ceiling for a well-formed question: look up a
# window, maybe compare it to another, answer.
MAX_TOOL_TURNS = 6

# The most individual transactions one `list_transactions` call may return.
# Small on purpose: this tool exists for "what was my biggest purchase" and
# "show me the ones I mean", not for pulling the ledger into the context window
# one page at a time. Aggregates are what `summarize_transactions` is for.
MAX_LISTED_TRANSACTIONS = 25

# The ceiling on how many categories and accounts are named in the system
# prompt. A user with four hundred categories has a different problem, and
# listing all of them would push the prompt past the point where the model
# reliably reads any of it.
MAX_CONTEXT_ITEMS = 100


# --- Tool inputs -----------------------------------------------------------
#
# Pydantic models, validated on the way in from the model exactly as
# `TransactionCreate` validates a JSON body from a browser. **A tool call is
# untrusted input.** It arrives as JSON produced by a probabilistic process, and
# "the schema said the field was a date" is a statement about what was asked
# for, not about what came back. A malformed call is turned into an error tool
# result below, which the model can read and correct — the same self-healing
# loop a 422 gives a human developer.


class _SummarizeInput(BaseModel):
    """Arguments for `summarize_transactions`."""

    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    type: Literal["income", "expense", "both"] = "both"
    category_ids: list[int] | None = None
    account_id: int | None = None
    group_by: Literal["none", "category", "month"] = "none"


class _ListInput(BaseModel):
    """Arguments for `list_transactions`."""

    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    type: Literal["income", "expense", "both"] = "both"
    category_ids: list[int] | None = None
    account_id: int | None = None
    order_by: Literal["date_desc", "amount_desc"] = "date_desc"
    limit: int = Field(default=10, ge=1, le=MAX_LISTED_TRANSACTIONS)


# --- Tool definitions ------------------------------------------------------
#
# Hand-written JSON Schema rather than generated from the Pydantic models above.
# The two describe the same shape but serve different audiences: these
# descriptions are *prompt text* — the model reads them to decide which tool to
# reach for and what to put in it — and a generated schema carries none of that
# guidance. The `description` on `category_ids` below, telling the model to map
# a word like "food" onto ids from the system prompt, is the single line that
# most affects whether this endpoint answers correctly.
#
# `strict` is deliberately not set. Strict mode requires every property to be
# listed in `required`, which would force the model to supply `category_ids`,
# `account_id` and `group_by` on every call even when it has nothing to say
# about them. The validation those models above perform is the same guarantee,
# applied where a failure can be handed back as a correctable error rather than
# as a rejected request.

_DATE_RANGE_PROPERTIES: dict[str, Any] = {
    "date_from": {
        "type": "string",
        "format": "date",
        "description": "Inclusive start of the window, YYYY-MM-DD.",
    },
    "date_to": {
        "type": "string",
        "format": "date",
        "description": "Inclusive end of the window, YYYY-MM-DD.",
    },
    "type": {
        "type": "string",
        "enum": ["income", "expense", "both"],
        "description": "Which side of the ledger. Spending questions are 'expense'.",
    },
    "category_ids": {
        "type": "array",
        "items": {"type": "integer"},
        "description": (
            "Restrict to these category ids. Map the user's wording onto ids from the "
            "category list in the system prompt — 'food' may well mean several of them "
            "(Groceries and Dining Out, say). Omit to include every category."
        ),
    },
    "account_id": {
        "type": "integer",
        "description": "Restrict to one account id. Omit for all accounts.",
    },
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "summarize_transactions",
        "description": (
            "Total the user's transactions over a date window, computed in SQL. "
            "This is the right tool for any question about how much — a total, an "
            "average, a comparison between periods or categories. Returns exact "
            "figures; use them verbatim."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_DATE_RANGE_PROPERTIES,
                "group_by": {
                    "type": "string",
                    "enum": ["none", "category", "month"],
                    "description": (
                        "'none' for a single total; 'category' to break the window down by "
                        "category; 'month' for a month-by-month series."
                    ),
                },
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_transactions",
        "description": (
            "List individual transactions from a date window, at most "
            f"{MAX_LISTED_TRANSACTIONS} of them. Use this only when the question is about "
            "specific transactions — the largest one, the most recent, what a particular "
            "charge was. For totals, use summarize_transactions instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_DATE_RANGE_PROPERTIES,
                "order_by": {
                    "type": "string",
                    "enum": ["date_desc", "amount_desc"],
                    "description": "'amount_desc' for 'biggest'; 'date_desc' for 'latest'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LISTED_TRANSACTIONS,
                    "description": "How many rows to return.",
                },
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
    },
]


# --- The system prompt -----------------------------------------------------


SYSTEM_RULES = """\
You are the assistant inside a personal finance tracker. You answer questions \
about one user's own transaction history, and only that.

Rules, in order of importance:

1. Every number you state must come from a tool result in this conversation. \
Never estimate, never extrapolate from a partial result, and never carry a \
figure over from your own general knowledge. If you have not queried it, you do \
not know it.
2. If a tool returns no matching transactions, say so plainly. "You have no \
recorded spending on that in June" is a correct and useful answer. Inventing a \
plausible figure is not.
3. Resolve relative dates against today's date, given below. A bare month name \
means the most recent occurrence of that month that is not in the future.
4. Answer in one to three sentences of plain prose. No preamble, no restating \
the question, no markdown headings. Amounts as plain numbers with two decimals \
and no currency symbol unless the account currency is given below.
5. If the question cannot be answered from transaction data — a request for \
financial advice, a question about the app itself, anything unrelated — say \
briefly that you can only answer questions about their recorded transactions. \
Do not call a tool for it.
"""


def _build_system_prompt(db: Session, user: User) -> str:
    """Assemble the system prompt, including this user's own categories.

    **The category list is what makes "food" resolvable.** The model cannot know
    that this particular user files restaurant meals under "Dining Out" and not
    under "Restaurants" or "Eating Out", and asking it to guess produces a
    query that filters on nothing and a total that is silently wrong. Handing
    over the real names and ids turns a guess into a lookup.

    Ids are included alongside names because ids are what the tools take. Names
    alone would force a second mapping step — and the place that mapping would
    happen is inside the model, which is the one place in this pipeline that is
    allowed to be approximate.

    Both lists are scoped to `user`, so there is no shape of prompt injection in
    a category name that can widen what the tools will return: the executor
    scopes every statement by user id regardless of what the model asks for.
    """
    categories = db.scalars(
        select(Category)
        .where(Category.user_id == user.id)
        .order_by(Category.type, Category.name)
        .limit(MAX_CONTEXT_ITEMS)
    ).all()

    accounts = db.scalars(
        select(Account)
        .where(Account.user_id == user.id)
        .order_by(Account.name)
        .limit(MAX_CONTEXT_ITEMS)
    ).all()

    category_lines = (
        "\n".join(f"  {c.id}: {c.name} ({c.type.value})" for c in categories)
        or "  (none — this user has not created any categories)"
    )
    account_lines = (
        "\n".join(f"  {a.id}: {a.name} ({a.currency})" for a in accounts)
        or "  (none)"
    )

    # `datetime.now(UTC)` rather than `date.today()`, matching the future-date
    # check in `schemas/transaction.py`: the server's notion of "today" is UTC
    # everywhere in this app, so relative dates resolve the same way here as
    # they are validated there.
    return (
        f"{SYSTEM_RULES}\n"
        f"Today's date is {datetime.now(UTC).date().isoformat()}.\n\n"
        f"The user's categories (id: name (type)):\n{category_lines}\n\n"
        f"The user's accounts (id: name (currency)):\n{account_lines}\n"
    )


# --- Tool execution --------------------------------------------------------


def _criteria(
    user_id: int,
    date_from: date,
    date_to: date,
    type_: str,
    category_ids: list[int] | None,
    account_id: int | None,
) -> list[ColumnElement[bool]]:
    """The `WHERE` clause both tools are built on.

    **The first element is not optional and not conditional**, exactly as in
    `_scope` in `routers/summary.py`. Everything after it is a filter the *model*
    asked for; that one is the boundary those filters narrow, and it is first so
    no later edit can slip a branch above it.

    Note what this means for a model that asks for someone else's `account_id`
    or `category_ids`: the scope has already made the condition unsatisfiable,
    so the answer is an empty result rather than a leak. The model is not
    trusted to stay inside the user's data — it is unable to leave it.
    """
    criteria: list[ColumnElement[bool]] = [
        Transaction.user_id == user_id,
        Transaction.occurred_on >= date_from,
        Transaction.occurred_on <= date_to,
    ]
    if type_ != "both":
        criteria.append(Transaction.type == TransactionType(type_))
    if category_ids:
        criteria.append(Transaction.category_id.in_(category_ids))
    if account_id is not None:
        criteria.append(Transaction.account_id == account_id)
    return criteria


def _money(value: Decimal | None) -> str:
    """Format an aggregate as a plain string for the model to read back.

    A string, not a float, for the reason `schemas/summary.py` gives at length:
    a JSON number is an IEEE double and the cent it loses is the cent a user
    checks against their bank. It matters twice over here, because this value is
    about to be copied into a sentence by a model that has no way to know it was
    rounded on the way.
    """
    return f"{(value or Decimal(0)):.2f}"


def _summarize(db: Session, user_id: int, args: _SummarizeInput) -> dict[str, Any]:
    """Run one `summarize_transactions` call as real SQL.

    One statement, one round trip, `GROUP BY` doing the work — the same design
    `routers/summary.py` sets out in its module docstring. The result's size is
    bounded by the number of groups (categories, or months in the window) rather
    than by how many transactions the user has, which is what makes it safe to
    hand to a model with a context window.
    """
    where = _criteria(
        user_id, args.date_from, args.date_to, args.type, args.category_ids, args.account_id
    )

    if args.group_by == "none":
        row = db.execute(
            select(
                func.coalesce(func.sum(Transaction.amount), Decimal(0)).label("total"),
                func.count().label("transaction_count"),
            ).where(*where)
        ).one()
        return {
            "date_from": args.date_from.isoformat(),
            "date_to": args.date_to.isoformat(),
            "type": args.type,
            "total": _money(row.total),
            "transaction_count": row.transaction_count,
        }

    if args.group_by == "category":
        total_expr = func.sum(Transaction.amount).label("total")
        rows = db.execute(
            select(
                Transaction.category_id,
                Category.name.label("category_name"),
                total_expr,
                func.count().label("transaction_count"),
            )
            # A LEFT JOIN, with the ownership check in the ON clause rather than
            # the WHERE — moving it would silently turn this back into an inner
            # join and drop every uncategorized row. `routers/summary.py`
            # explains that trap in full.
            .join(
                Category,
                (Category.id == Transaction.category_id) & (Category.user_id == user_id),
                isouter=True,
            )
            .where(*where)
            .group_by(Transaction.category_id, Category.name)
            .order_by(total_expr.desc(), Category.name)
        ).all()

        groups = [
            {
                "category_id": row.category_id,
                "category": row.category_name or UNCATEGORIZED_LABEL,
                "total": _money(row.total),
                "transaction_count": row.transaction_count,
            }
            for row in rows
        ]
    else:  # "month"
        rows = db.execute(
            select(
                MONTH_START.label("month_start"),
                func.sum(Transaction.amount).label("total"),
                func.count().label("transaction_count"),
            )
            .where(*where)
            .group_by(MONTH_START)
            .order_by(MONTH_START)
        ).all()

        groups = [
            {
                "month": f"{row.month_start.year:04d}-{row.month_start.month:02d}",
                "total": _money(row.total),
                "transaction_count": row.transaction_count,
            }
            for row in rows
        ]

    # The grand total is summed from the group totals rather than fetched with a
    # second query. Every row in scope belongs to exactly one group — the outer
    # join keeps uncategorized rows in the NULL group rather than dropping them —
    # so the groups partition the whole and adding them is exact.
    grand_total = sum((Decimal(group["total"]) for group in groups), start=Decimal(0))

    return {
        "date_from": args.date_from.isoformat(),
        "date_to": args.date_to.isoformat(),
        "type": args.type,
        "grouped_by": args.group_by,
        "total": _money(grand_total),
        "transaction_count": sum(int(group["transaction_count"]) for group in groups),
        "groups": groups,
    }


def _list(db: Session, user_id: int, args: _ListInput) -> dict[str, Any]:
    """Run one `list_transactions` call.

    `id DESC` as a tiebreaker on both orderings, for the same reason the ledger
    endpoint uses it: two transactions on the same day (or for the same amount)
    would otherwise come back in whatever order the query plan happened to
    produce, and a model asked the same question twice would get two different
    "biggest purchases".
    """
    where = _criteria(
        user_id, args.date_from, args.date_to, args.type, args.category_ids, args.account_id
    )

    order = (
        (Transaction.amount.desc(), Transaction.id.desc())
        if args.order_by == "amount_desc"
        else (Transaction.occurred_on.desc(), Transaction.id.desc())
    )

    rows: list[Row[Any]] = db.execute(
        select(
            Transaction.id,
            Transaction.occurred_on,
            Transaction.description,
            Transaction.amount,
            Transaction.type,
            Category.name.label("category_name"),
        )
        .join(
            Category,
            (Category.id == Transaction.category_id) & (Category.user_id == user_id),
            isouter=True,
        )
        .where(*where)
        .order_by(*order)
        .limit(args.limit)
    ).all()

    return {
        "date_from": args.date_from.isoformat(),
        "date_to": args.date_to.isoformat(),
        "order_by": args.order_by,
        "transaction_count": len(rows),
        "transactions": [
            {
                "id": row.id,
                "date": row.occurred_on.isoformat(),
                "description": row.description,
                "amount": _money(row.amount),
                "type": row.type.value,
                "category": row.category_name or UNCATEGORIZED_LABEL,
            }
            for row in rows
        ],
    }


class _ToolFailed(Exception):
    """A tool call could not be run, with a message the model should read.

    An exception rather than a sentinel return so `_execute` reads as a straight
    line. What makes it different from `_RowRejected` in the CSV importer is
    where the message goes: this one is handed *back to the model* as an error
    tool result, and the model gets to try again. A tool error is a conversation
    turn, not a failure of the request.
    """


def _execute(db: Session, user_id: int, name: str, raw_input: Any) -> dict[str, Any]:
    """Validate one tool call and run it. Raises `_ToolFailed` on bad arguments.

    `raw_input` is whatever the SDK parsed out of the model's output. It is
    typed `Any` because that is honestly what it is — untrusted JSON — and the
    Pydantic models are what turn it into something with a type.
    """
    if not isinstance(raw_input, dict):
        raise _ToolFailed(f"tool input must be an object, got {type(raw_input).__name__}")

    try:
        if name == "summarize_transactions":
            return _summarize(db, user_id, _SummarizeInput.model_validate(raw_input))
        if name == "list_transactions":
            return _list(db, user_id, _ListInput.model_validate(raw_input))
    except ValidationError as exc:
        # The model's own error message, in the model's terms. Pydantic's
        # rendering names the field and says what was wrong with it, which is
        # exactly the feedback needed to produce a corrected call — the same
        # thing a 422 does for a human writing a client.
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ())) or "(root)"
        raise _ToolFailed(f"invalid arguments: {location}: {first.get('msg', 'invalid')}") from exc

    raise _ToolFailed(f"unknown tool {name!r}")


# --- The loop --------------------------------------------------------------


def _final_text(response: anthropic.types.Message) -> str:
    """Pull the answer out of the final response, or fail loudly.

    The idiomatic one-liner — `next(b.text for b in content if b.type == "text")`
    — raises `StopIteration` when there is no text block, and a bare
    `StopIteration` escaping a request handler becomes an unexplained 500. There
    are real ways to get here with no text: the turn was cut off at `max_tokens`
    having spent the budget on thinking, or the model declined the request.
    Both deserve a message that says which.

    Text blocks are joined rather than taking the first, because a response with
    thinking enabled can interleave them.
    """
    if response.stop_reason == "refusal":
        # `stop_details` is populated only for this stop reason — reading it
        # unguarded on any other response gets you `None.category`.
        category = getattr(response.stop_details, "category", None)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The model declined to answer that question ({category or 'unspecified'}).",
        )

    text = "\n".join(block.text for block in response.content if block.type == "text").strip()

    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model returned no answer. Try rephrasing the question.",
        )

    if response.stop_reason == "max_tokens":
        # Returned rather than raised: a truncated answer is still worth showing,
        # and silently presenting half a sentence as a whole one is the failure
        # worth avoiding. The marker is in the text because that is the only
        # place a user will see it.
        return f"{text}\n\n(This answer was cut short by the response limit.)"

    return text


def answer_question(
    client: anthropic.Anthropic,
    db: Session,
    user: User,
    question: str,
) -> AiQueryRead:
    """Answer one natural-language question about `user`'s transactions.

    The loop is the standard agentic shape — ask, run whatever tools came back,
    hand the results over, repeat — with two additions that matter:

    **Every call is recorded.** `evidence` accumulates the arguments and the
    result of each tool call in order, and it travels back to the client. That
    is what turns "the model said 412.30" into "a SUM over these dates in these
    categories returned 412.30", which is a claim a user can check.

    **The loop is bounded.** `MAX_TOOL_TURNS` caps the number of paid round
    trips one question can cause. Falling off the end is not an error — the
    model is asked, one final time and with the tools withheld, to answer from
    what it has already gathered. Withholding the tools is what makes that turn
    terminal: offered them again it would simply ask for more.
    """
    system = _build_system_prompt(db, user)
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": question}]

    evidence: list[EvidenceStep] = []
    responses: list[anthropic.types.Message] = []

    for turn in range(MAX_TOOL_TURNS + 1):
        # On the final turn the tools are withheld, which forces an answer from
        # the evidence already gathered instead of a seventh request for more.
        exhausted = turn == MAX_TOOL_TURNS

        with translate_api_errors():
            response = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=settings.ai_max_tokens,
                system=system,
                # Adaptive thinking. On this model it is also the default, so
                # this line changes nothing today — it is here because the day
                # someone pins an older model in config, the absence of it would
                # quietly turn reasoning off and the only symptom would be worse
                # date arithmetic.
                thinking={"type": "adaptive"},
                messages=messages,
                **({} if exhausted else {"tools": TOOLS}),
            )

        responses.append(response)

        if response.stop_reason != "tool_use":
            break

        tool_calls = [block for block in response.content if block.type == "tool_use"]

        # The assistant turn goes back verbatim — `response.content`, not just
        # the text. It carries the `tool_use` blocks each result must be matched
        # to by id, and the thinking blocks that have to be echoed unchanged for
        # the model to continue its own reasoning.
        messages.append({"role": "assistant", "content": response.content})

        results: list[dict[str, Any]] = []
        for call in tool_calls:
            try:
                output = _execute(db, user.id, call.name, call.input)
            except _ToolFailed as exc:
                # `is_error` marks this as a failure the model should react to
                # rather than a result it should report. The alternative —
                # raising here — throws away a recoverable turn and answers the
                # user with a 500 because a date was malformed.
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": f"Error: {exc}",
                        "is_error": True,
                    }
                )
                continue

            evidence.append(
                EvidenceStep(
                    tool=call.name,
                    # `call.input` is already a parsed object — never re-parsed
                    # from the serialized form, which can carry model-specific
                    # JSON escaping.
                    arguments=call.input if isinstance(call.input, dict) else {},
                    result=output,
                )
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(output),
                }
            )

        # All results in a *single* user message. Splitting them across several
        # is the quiet way to train the model out of making parallel calls at
        # all, and the request would be malformed besides.
        messages.append({"role": "user", "content": results})

    return AiQueryRead(
        question=question,
        answer=_final_text(responses[-1]),
        evidence=evidence,
        model=responses[-1].model,
        usage=usage_of(*responses),
    )
