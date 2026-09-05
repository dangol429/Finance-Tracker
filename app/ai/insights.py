"""#18 — A short monthly write-up, grounded in the user's own aggregates.

The pipeline, and the order matters:

    1. PostgreSQL computes the month's figures — and last month's, for contrast.
    2. Those figures, and nothing else, go into the prompt.
    3. The model writes three or four sentences about them.
    4. The response carries the prose **and the figures it was written from**.

**Step 4 is the feature.** Anyone can generate a paragraph about spending; the
hard part is making it trustworthy, and a paragraph on its own is not — it reads
identically whether the numbers in it are real or invented. Returning
`MonthlyInsightFacts` alongside means the claim "dining out rose 34%" sits next
to the two totals it was derived from, in the same response, and a client can
render both. A wrong number stops being an undetectable failure and becomes a
visible mismatch.

**The model is given no tools and no database access**, which is the difference
between this module and `query.py`. There is exactly one thing to summarize —
this month against last — so there is nothing to decide about *which* data to
fetch, and a tool loop would be machinery around a fixed query. Retrieval here
is a plain function call; the model's only job is prose.

**An empty month never reaches the model at all.** With no transactions there is
nothing to summarize, and asking for a write-up anyway is paying for an
invitation to invent one. The honest output is a fixed sentence, and the
response says so by returning `model: null` — a client can tell generated text
from deterministic text without guessing.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import anthropic
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.ai.client import translate_api_errors, usage_of
from app.core.config import settings
from app.models.enums import TransactionType
from app.models.user import User

# The aggregation handlers, called directly as the plain functions they are.
#
# **This is a deliberate exception to a rule this codebase otherwise keeps.**
# `routers/csv_import.py` copies two small helpers rather than import them from
# `routers/transactions.py`, on the grounds that one router depending on another
# is worse than twelve duplicated lines. The reasoning inverts here because the
# quantity inverts: what would be duplicated is not twelve lines but the entire
# aggregation layer — `SUM ... FILTER`, the `date_trunc` casts, the rounding
# rules, the zero-denominator guards. A second copy of that is exactly the drift
# `schemas/transaction.py` warns about ("one schema, two callers, no drift"), and
# the copy that would silently diverge is the one feeding a paragraph of prose
# nobody cross-checks against the dashboard.
#
# Note the arrow still points one way: `routers/summary.py` imports nothing from
# here. The right long-term shape is `app/services/summary.py` holding these
# functions with both routers as callers, and the moment a third caller appears
# is the moment that refactor pays for itself.
from app.routers.summary import category_breakdown, income_vs_expense
from app.schemas.ai import MonthlyInsightFacts, MonthlyInsightRead

# The most bullets a summary may carry. Enforced in Python rather than in the
# schema — see the note on `highlights` below.
MAX_HIGHLIGHTS = 4

# The write-up's shape. Three fields rather than one blob of markdown, because a
# client needs to lay them out differently — a headline is a heading, highlights
# are a list — and splitting prose back apart with a regex on the way out is how
# a formatting change in the model's output silently breaks a page.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "One sentence summing the month up. No greeting, no preamble.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Two or three sentences of plain prose about what happened and how it "
                    "compares to the previous month. No markdown, no bullet points."
                ),
            },
            "highlights": {
                "type": "array",
                "items": {"type": "string"},
                # No `minItems`/`maxItems`. Structured output rejects a
                # `minItems` above 1 outright (400: "values other than 0 or 1
                # are not supported"), so the count is asked for in the
                # description and enforced in Python where it is actually
                # enforceable. Worth knowing that the JSON Schema accepted here
                # is a *subset* of the spec — a constraint that looks valid can
                # still be a request the API refuses to make.
                "description": (
                    "Two to four short bullets, each anchored to a specific figure from "
                    "the data given."
                ),
            },
        },
        "required": ["headline", "summary", "highlights"],
        "additionalProperties": False,
    },
}


SYSTEM_PROMPT = """\
You write a brief monthly summary for a personal finance tracker.

You are given one month of the user's aggregated figures and the previous \
month's, computed from their transaction records. Write about those figures.

Rules:

1. Every number you state must appear in the data you were given, or be a \
difference or percentage change between two numbers in it. Do not estimate, do \
not round to something friendlier, and do not introduce a figure that is not \
derivable from what is in front of you.
2. Describe what changed. A list of totals is not a summary; the useful content \
is which categories moved and by how much.
3. Be plain and neutral. No congratulating, no scolding, no financial advice, \
no exclamation marks. The user can see their own numbers; your job is to point \
at what is worth noticing in them.
4. Amounts as plain numbers with two decimals. Do not add a currency symbol.
5. If a comparison is not meaningful — no income at all, or no previous month's \
data — say so rather than computing a percentage of zero.
"""


class _RawInsight(BaseModel):
    """The model's structured response, before it is trusted."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    summary: str
    highlights: list[str]


def _month_bounds(month: str) -> tuple[date, date]:
    """Turn `"2026-06"` into the first and last day of that month.

    Parsed strictly rather than with a permissive `strptime`: `"2026-6"`,
    `"26-06"` and `"2026-13"` all have to be errors, because each of them would
    otherwise silently produce a report about a month the user did not ask for.

    The last day is computed by stepping to the first of the next month and back
    one day, which is correct for February in a leap year without a table of
    month lengths or a special case for December.
    """
    try:
        year_text, month_text = month.split("-")
        if len(year_text) != 4 or len(month_text) != 2:
            raise ValueError
        year, month_number = int(year_text), int(month_text)
        first = date(year, month_number, 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{month!r} is not a valid month (expected YYYY-MM, e.g. 2026-06)",
        ) from exc

    next_first = (
        date(year + 1, 1, 1) if month_number == 12 else date(year, month_number + 1, 1)
    )
    return first, next_first - date.resolution


def _previous_month(first: date) -> tuple[date, date]:
    """The month before `first`, as bounds."""
    previous_first = (
        date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)
    )
    return previous_first, first - date.resolution


def _label(day: date) -> str:
    """A month as `"YYYY-MM"`, built from the parts.

    Not `strftime("%Y-%m")`, matching `_month_label` in `routers/summary.py`:
    that function's output for years before 1000 differs between glibc, musl and
    the Windows CRT, and this is the same amount of code with the same answer
    everywhere.
    """
    return f"{day.year:04d}-{day.month:02d}"


def _gather_facts(
    db: Session, user: User, month: str, account_id: int | None
) -> MonthlyInsightFacts:
    """Compute every figure the write-up may refer to.

    Four calls into the aggregation layer: totals and a category breakdown, for
    this month and the one before. Each is the *same* function the corresponding
    `/summary/*` endpoint runs, so a client that renders the facts beside a chart
    is rendering two views of one query rather than two queries that might
    disagree.

    Only the expense breakdown is fetched. Income categories are a short list
    that rarely moves — a salary and perhaps a side income — and a paragraph
    noting that a paycheck arrived again is filler. Where the interesting change
    lives is spending, which is what the breakdown is for.
    """
    first, last = _month_bounds(month)
    previous_first, previous_last = _previous_month(first)

    def totals(date_from: date, date_to: date):
        return income_vs_expense(
            current_user=user,
            db=db,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
        )

    def breakdown(date_from: date, date_to: date):
        return category_breakdown(
            current_user=user,
            db=db,
            type=TransactionType.EXPENSE,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
        )

    return MonthlyInsightFacts(
        month=_label(first),
        previous_month=_label(previous_first),
        totals=totals(first, last),
        previous_totals=totals(previous_first, previous_last),
        categories=breakdown(first, last),
        previous_categories=breakdown(previous_first, previous_last),
    )


def _has_activity(facts: MonthlyInsightFacts) -> bool:
    """Whether the month being described contains any transactions at all."""
    return (
        facts.totals.income.transaction_count + facts.totals.expense.transaction_count
    ) > 0


def _empty_month(facts: MonthlyInsightFacts) -> MonthlyInsightRead:
    """The deterministic answer for a month with nothing in it.

    `model` and `usage` are null here, which is the signal that no model ran.
    A client can render this differently — without an "AI generated" marker, for
    instance — and, more usefully, nobody has to wonder later why a paragraph
    about an empty month sounded so confident.
    """
    return MonthlyInsightRead(
        month=facts.month,
        headline=f"No transactions recorded in {facts.month}.",
        summary=(
            f"There is nothing to summarise for {facts.month} — no income and no spending "
            "was recorded in that month. Import a statement or add a transaction, and a "
            "summary will appear here."
        ),
        highlights=[],
        facts=facts,
        model=None,
        usage=None,
    )


def _prompt_facts(facts: MonthlyInsightFacts) -> str:
    """Render the aggregates as compact JSON for the prompt.

    `model_dump(mode="json")` rather than hand-building a dict: it walks the same
    Pydantic models the API returns, so the numbers the model reads are byte-for-
    byte the numbers the client receives in `facts`. Two renderings of the same
    data is two chances for the prose to describe a figure the response does not
    contain.

    Category lists are trimmed to the largest few. The tail of a breakdown is a
    long run of small slices that no summary will mention, and every one of them
    is prompt tokens paid for on a request a user is waiting on.
    """
    payload = facts.model_dump(mode="json")

    for key in ("categories", "previous_categories"):
        payload[key]["categories"] = payload[key]["categories"][:10]

    return json.dumps(payload, separators=(",", ":"))


def monthly_insight(
    client: anthropic.Anthropic,
    db: Session,
    user: User,
    month: str,
    account_id: int | None = None,
) -> MonthlyInsightRead:
    """Write a short summary of `month` for `user`, grounded in their aggregates."""
    facts = _gather_facts(db, user, month, account_id)

    if not _has_activity(facts):
        return _empty_month(facts)

    prompt = (
        f"Write the monthly summary for {facts.month}. "
        f"Here are the figures, including {facts.previous_month} for comparison:\n\n"
        f"{_prompt_facts(facts)}"
    )

    with translate_api_errors():
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=settings.ai_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            # Adaptive thinking, and effort left at its default. Unlike the bulk
            # classification in `categorize.py`, the work here is comparing a
            # dozen figures and deciding which two or three are worth a
            # sentence — judgement rather than pattern-matching, and the place
            # where reasoning earns its cost.
            thinking={"type": "adaptive"},
            output_config={"format": OUTPUT_SCHEMA},
        )

    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The model declined to write this summary ({category or 'unspecified'}).",
        )

    text = "\n".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model returned no summary.",
        )

    try:
        parsed = _RawInsight.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The model's summary was cut off by the token limit."
                if response.stop_reason == "max_tokens"
                else "The model returned a summary that did not match the expected shape."
            ),
        ) from exc

    return MonthlyInsightRead(
        month=facts.month,
        headline=parsed.headline.strip(),
        summary=parsed.summary.strip(),
        # Blank bullets are dropped rather than rendered as empty list items,
        # and the list is capped here because the schema cannot cap it (see
        # `OUTPUT_SCHEMA`). A model that returns nine highlights produces a card
        # that scrolls; four is the number the prompt asks for and this is the
        # only place it can be made true.
        highlights=[line.strip() for line in parsed.highlights if line.strip()][:MAX_HIGHLIGHTS],
        # The same facts object that was serialized into the prompt, returned
        # unchanged. This is the grounding contract: the figures the client
        # receives are provably the ones the model was shown.
        facts=facts,
        model=response.model,
        usage=usage_of(response),
    )
