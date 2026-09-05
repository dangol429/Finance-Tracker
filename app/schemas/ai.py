"""Request and response shapes for the three AI endpoints.

**Every response in this file carries its own evidence.** That is the one design
rule the whole module is built around, and it is what separates these shapes
from the obvious version of each — a string of prose and nothing else.

    /ai/query              answer  +  `evidence`: the tool calls and the exact
                                      rows/aggregates they returned
    /ai/categorize         suggestions  +  `confidence` and `reasoning` per row,
                                      and `skipped` for what the model got wrong
    /ai/insights/monthly   summary  +  `facts`: the identical aggregates
                                      `/summary/*` would return for that month

The reason is not transparency for its own sake. A language model produces text
that is fluent whether or not it is correct, and a *number* in fluent text
carries an authority it has not earned. Shipping the grounding data alongside
the prose means a user who doubts a figure can check it in the same response
rather than trusting it, and it means a bug in a prompt shows up as a visible
mismatch instead of as a plausible sentence nobody questions.

**Money stays a `Decimal`, so it goes out as a JSON string** — `"1550.00"`, not
`1550.0`. Same contract as every other schema in this app; `schemas/summary.py`
has the full argument for why. It matters slightly more here, because these
responses put a number in prose *and* in structured data, and the two disagreeing
in the last decimal is the exact failure the string form exists to prevent.

**Confidence is a `float`, and that is deliberate inconsistency.** It is not
money — it is a model's own estimate of itself, accurate to nothing like two
decimal places — so exact decimal arithmetic would be false precision. Treating
it as the rough signal it is keeps it from being mistaken for a measurement.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TransactionType
from app.schemas.summary import CategoryBreakdownRead, IncomeVsExpenseRead
from app.schemas.transaction import TransactionRead

# Unknown keys are rejected rather than ignored, exactly as in
# `schemas/transaction.py` — a typo'd field in a hand-written JSON body should
# be a 422 at the edge, not a silently-defaulted value the client spends an
# afternoon looking for.
STRICT_INPUT = ConfigDict(extra="forbid")


class AiUsage(BaseModel):
    """Tokens spent answering one request.

    Returned to the client rather than only logged, because these endpoints cost
    real money per call and the cost is invisible everywhere else in this API.
    A user (or a developer watching the network tab) can see what a question
    cost at the moment they asked it, which is the only time anyone is motivated
    to care.
    """

    input_tokens: int
    output_tokens: int


# --- #16  Natural-language spending queries --------------------------------


class EvidenceStep(BaseModel):
    """One tool call the model made, and exactly what came back.

    **This is the field that makes the answer checkable.** `arguments` is the
    structured query the model chose — the date window, the category filter, the
    grouping — and `result` is the verbatim aggregate PostgreSQL returned for it.
    A user reading "you spent 412.30 on groceries in June" can see that the
    number came from a `SUM` over `2026-06-01..2026-06-30` filtered to their
    Groceries category, rather than from the model's imagination.

    `result` is typed `Any` because each tool returns a different shape, and
    modelling all of them here would mean a union that has to be edited every
    time a tool is added. What it holds is always JSON-serializable — it is the
    same object that was handed to the model, not a summary of it, because an
    evidence trail that has been through a second transformation is evidence of
    the transformation.
    """

    tool: str = Field(description="Which tool ran")
    arguments: dict[str, Any] = Field(description="The structured query the model emitted")
    result: Any = Field(description="Exactly what that query returned, as the model saw it")


class AiQueryCreate(BaseModel):
    """The body of `POST /ai/query`."""

    model_config = STRICT_INPUT

    # Bounded on both ends. The lower bound rejects an empty box submitted by
    # accident before it costs a paid API call; the upper bound is not about
    # tokens (500 characters is nothing) but about what the endpoint is *for* —
    # it answers questions about a ledger, and a 20,000-character body is either
    # a mistake or someone using this app as a general-purpose LLM proxy.
    question: str = Field(
        min_length=1,
        max_length=500,
        description="A question about your own transactions",
        examples=["How much did I spend on food in June?"],
    )


class AiQueryRead(BaseModel):
    """`POST /ai/query` — a grounded answer, plus what it was grounded in."""

    question: str = Field(description="Echoed back, so a cached response is self-describing")
    answer: str

    # Empty when the model answered without querying anything — which happens
    # for a question the ledger cannot answer ("what's a good savings rate?").
    # An empty list is therefore meaningful rather than a failure: it says the
    # answer contains no figures from this user's data, and a client can render
    # it differently on that basis.
    evidence: list[EvidenceStep] = Field(
        description="Every tool call made, in order, with its exact result"
    )

    model: str = Field(description="Which model produced this, so an answer is reproducible")
    usage: AiUsage


# --- #17  Auto-categorisation ----------------------------------------------


class CategorizeCreate(BaseModel):
    """The body of `POST /ai/categorize`.

    Every field is optional: an empty body means "suggest categories for my
    most recent uncategorized transactions", which is the thing a user actually
    wants ninety percent of the time.
    """

    model_config = STRICT_INPUT

    # When given, exactly these transactions are considered — including ones
    # that already have a category, which is what makes this endpoint usable for
    # *re*-categorising a batch the user thinks was filed wrong. When omitted,
    # the router selects uncategorized rows itself.
    transaction_ids: list[int] | None = Field(
        default=None,
        max_length=200,
        description="Specific transactions to categorise; omit for uncategorized ones",
    )

    # Only consulted when `transaction_ids` is omitted. Capped at 200 because
    # each batch of 25 is one paid API call, so an uncapped limit is an
    # unbounded bill triggered by a single request.
    limit: int = Field(
        default=50,
        gt=0,
        le=200,
        description="How many uncategorized transactions to consider",
    )

    account_id: int | None = Field(default=None, gt=0, description="Only this account")

    # The line between "recommended" and "shown but unticked" in the UI. A
    # parameter rather than a constant because the right threshold depends on
    # how tidy the user's category list is, and because the honest default is a
    # judgement call rather than a measured value.
    min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Below this, a suggestion is returned but not recommended",
    )


class CategorySuggestion(BaseModel):
    """One proposed category, with everything needed to judge it.

    The transaction's own fields are repeated here rather than left for the
    client to join against a separate list. A review UI has to show the user
    *what* is being categorised next to *how* — description, amount and date on
    the same row as the proposed category — and making the client fetch and
    zip two lists to build that row is work with a bug in it.
    """

    transaction_id: int
    description: str | None
    amount: Decimal
    type: TransactionType
    occurred_on: date

    # Guaranteed to be one of this user's own categories, of the matching type.
    # The JSON schema sent to the model constrains the name to an `enum` of
    # their categories, and `categorize.py` re-checks the type in Python — see
    # the note there on why a schema cannot enforce the second rule.
    category_id: int
    category_name: str

    confidence: float = Field(ge=0.0, le=1.0, description="The model's own estimate, clamped")
    reasoning: str = Field(description="One short sentence, for a human deciding whether to accept")

    # Precomputed against `min_confidence` so the client's checkbox default and
    # the server's notion of "confident" cannot drift apart. Two places
    # comparing the same float against the same threshold is one place too many.
    recommended: bool = Field(description="Whether confidence met the requested threshold")


class SkippedTransaction(BaseModel):
    """A transaction the model did not produce a usable suggestion for.

    Reported rather than silently dropped, on the same reasoning as the CSV
    importer's error list: a response that says "12 suggestions" for a request
    about 15 transactions leaves the user unable to tell which three are missing
    or why. The `reason` distinguishes the honest cases ("no category fits")
    from the ones worth investigating ("suggested an income category for an
    expense") — and the second kind is exactly what a validation layer exists to
    catch, so surfacing it is how anyone ever learns it happened.
    """

    transaction_id: int
    description: str | None
    reason: str


class CategorizeRead(BaseModel):
    """`POST /ai/categorize` — suggestions only. Nothing has been written.

    The endpoint is deliberately read-only against the ledger. Applying a
    suggestion is a separate, explicit call (`POST /ai/categorize/apply`), so an
    AI can never change a user's financial records as a side effect of being
    asked for an opinion. The correction path is the same one: the client sends
    back whichever `category_id` the user actually chose, which need not be the
    one suggested.
    """

    considered: int = Field(description="How many transactions were sent to the model")
    suggestions: list[CategorySuggestion]
    skipped: list[SkippedTransaction]

    min_confidence: float = Field(description="Echoed, so `recommended` is interpretable")
    model: str
    usage: AiUsage


class CategoryAssignment(BaseModel):
    """One accepted (or corrected) category choice."""

    model_config = STRICT_INPUT

    transaction_id: int = Field(gt=0)
    category_id: int = Field(gt=0)


class ApplyCategoriesCreate(BaseModel):
    """The body of `POST /ai/categorize/apply`.

    Note what this shape does *not* contain: any reference to a previous
    `/ai/categorize` response, a suggestion id, or a token proving the
    assignment came from the model. That is on purpose. This endpoint applies
    the user's decision, and the user is free to have changed every one of them
    — so it takes plain `(transaction, category)` pairs and validates them
    against the database exactly as `PATCH /transactions/{id}` would. An
    endpoint that could only apply what the AI suggested would be an endpoint
    that makes correcting the AI harder than accepting it.
    """

    model_config = STRICT_INPUT

    assignments: list[CategoryAssignment] = Field(
        min_length=1,
        max_length=200,
        description="The (transaction, category) pairs to write",
    )


class ApplyCategoriesRead(BaseModel):
    """`POST /ai/categorize/apply` — what was actually written."""

    updated: int
    # The full updated rows, so a client can drop them straight into its cache
    # rather than refetching the ledger to find out what changed.
    transactions: list[TransactionRead]


# --- #18  Monthly insights --------------------------------------------------


class MonthlyInsightFacts(BaseModel):
    """The aggregates the write-up was generated from — and nothing else.

    **These are the same objects `/summary/income-vs-expense` and
    `/summary/by-category` return**, for the month in question and for the one
    before it. Not a reshaped copy: the identical Pydantic models, produced by
    the identical SQL. That is what lets a client show the prose and the chart
    from one request and know they cannot disagree, and it is why a figure in
    the summary can be traced to a row in `categories` without trusting anything
    the model said.

    The previous month is included because a monthly insight that cannot say
    "up from last month" is a list of totals with adjectives attached. Change is
    the entire content of the genre — and supplying the comparison as *data*
    rather than letting the model recall it is what stops "up 12%" from being a
    number nobody computed.
    """

    month: str = Field(description='The month described, as "YYYY-MM"', examples=["2026-06"])
    previous_month: str

    totals: IncomeVsExpenseRead
    previous_totals: IncomeVsExpenseRead

    categories: CategoryBreakdownRead
    previous_categories: CategoryBreakdownRead


class MonthlyInsightRead(BaseModel):
    """`GET /ai/insights/monthly` — a short write-up, and its source data."""

    month: str

    headline: str = Field(description="One sentence: the month in a line")
    summary: str = Field(description="Two or three sentences of plain prose")
    highlights: list[str] = Field(description="Short bullets, each tied to a figure in `facts`")

    facts: MonthlyInsightFacts

    # Both null when the month held no transactions at all. In that case no
    # model was called: there is nothing to summarize, the honest write-up is
    # one fixed sentence, and asking a model to produce prose about an empty
    # ledger is paying for an invitation to invent something. Null here is the
    # signal that the text below is deterministic rather than generated — a
    # distinction a client may well want to render.
    model: str | None
    usage: AiUsage | None
