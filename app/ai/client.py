"""The Anthropic client, and the one place its failures become HTTP responses.

Two jobs, both of which exist so the three feature modules don't have to think
about them:

  1. **Construct the client once**, and refuse clearly when the app has no key.
  2. **Translate SDK exceptions into HTTPExceptions**, so a provider outage
     leaves this API returning 502 rather than 500 — the difference between
     "the upstream service failed" and "this app has a bug", which is the
     distinction an on-call person needs at 3am.

**Why a FastAPI dependency and not a module-level singleton.** `AiClient` is
`Annotated[Anthropic, Depends(get_anthropic_client)]`, exactly like `DbSession`
and `CurrentUser` in `core/deps.py`, and for the same three reasons set out at
length there: it is typed, it composes, and — the one that matters most here —
it is *overridable*. `app.dependency_overrides[get_anthropic_client]` is what
lets the test suite run the whole `/ai` surface against a scripted fake, with no
network, no API key, and no `unittest.mock.patch` reaching into another module's
globals. A test double injected through the front door is one the application
code cannot tell apart from the real thing, which is the property that makes the
tests worth having.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Annotated

import anthropic
from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.schemas.ai import AiUsage


@lru_cache(maxsize=1)
def _build_client() -> anthropic.Anthropic:
    """Construct the SDK client, once per process.

    Cached because the client owns an HTTP connection pool. Building a fresh one
    per request would open a new TLS connection to the API every time — the
    handshake alone is a meaningful share of a short call's latency, and the
    discarded pools are garbage the process then has to collect under load.

    `max_retries` is left at the SDK's default of 2. The SDK already retries the
    failures worth retrying (429, 5xx, connection errors) with exponential
    backoff, so a retry loop written here would multiply against that one and
    turn a brief rate-limit into a request that hangs for minutes.
    """
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        # Overrides the SDK's ten-minute default. See the note on the setting in
        # `core/config.py`: ten minutes is a batch-job timeout, and there is a
        # person waiting on the other end of every call this app makes.
        timeout=settings.ai_timeout_seconds,
    )


def get_anthropic_client() -> anthropic.Anthropic:
    """The dependency every `/ai` route asks for.

    503, not 500, when no key is configured: the request was perfectly valid and
    the server simply cannot serve this feature right now. That is precisely
    what 503 means, and it is a *deployment* state rather than a bug — which is
    why the message names the variable to set instead of apologising.

    Checked per request rather than at import time on purpose. Raising at import
    would take the entire API down — accounts, transactions, the dashboard —
    because an optional feature was unconfigured. The AI layer is an addition to
    this app, and an addition that can refuse to work without stopping anything
    else is the shape that keeps it optional in practice as well as in theory.
    """
    if not settings.ai_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "AI features are not configured on this server. "
                "Set ANTHROPIC_API_KEY to enable them."
            ),
        )
    return _build_client()


AiClient = Annotated[anthropic.Anthropic, Depends(get_anthropic_client)]


@contextmanager
def translate_api_errors() -> Iterator[None]:
    """Turn an SDK exception into the HTTP status it actually deserves.

    Without this every one of these arrives at the client as a 500 with no body,
    and 500 means "this app is broken" — so a provider rate-limit gets triaged
    as a bug in the finance tracker. The mapping below says what really
    happened, and each code is chosen for what a *client* should do about it:

        401/403  ->  502   The key is wrong or lacks access. Nothing the caller
                           did, nothing the caller can fix, and emphatically not
                           a 401 to pass on — the user's own token was fine, and
                           echoing 401 would log them out over a server
                           misconfiguration.
        429      ->  429   Rate limited upstream. Passed through unchanged
                           because it is the one failure here a client can act
                           on correctly by waiting and retrying.
        timeout  ->  504   The upstream took too long. A gateway timeout, which
                           is exactly what this app is in that moment.
        other    ->  502   Bad gateway: the dependency failed.

    The chain is ordered most-specific first, because `APIStatusError` is the
    parent of `RateLimitError` and would swallow it if it came first.
    """
    try:
        yield
    except anthropic.APITimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The AI provider took too long to respond. Try again.",
        ) from exc
    except anthropic.RateLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The AI provider is rate limiting this server. Try again shortly.",
        ) from exc
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        # Deliberately not echoing the provider's message. It can name the key
        # prefix and the organization, which is server configuration detail that
        # has no business in a response to an end user.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="This server's AI credentials were rejected by the provider.",
        ) from exc
    except anthropic.APIStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The AI provider returned an error ({exc.status_code}).",
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the AI provider.",
        ) from exc


def usage_of(*responses: anthropic.types.Message) -> AiUsage:
    """Total the token usage across every call a single request made.

    Variadic because `categorize.py` sends one request per batch, and reporting
    only the last one's usage would understate a fifty-transaction run by a
    factor of two or three. Cost is per token, so the number a caller needs is
    the sum over the whole operation, not a sample from the end of it.
    """
    return AiUsage(
        input_tokens=sum(response.usage.input_tokens for response in responses),
        output_tokens=sum(response.usage.output_tokens for response in responses),
    )
