"""Shared FastAPI dependencies — the bridge between a raw HTTP request and a
`User` object.

This is the layer `security.py` deliberately isn't. Those functions know about
bytes and signatures; these know about headers and status codes. Keeping the
seam here means the crypto is testable without a request, and the HTTP handling
is testable without valid tokens.

**Why dependencies rather than a middleware or a decorator.** A dependency is
just a function FastAPI calls before the handler, caching the result per
request. Three things fall out of that, and together they're the reason this
pattern won:

  1. *It's typed.* `user: CurrentUser` in a signature gives you a real `User`
     with autocomplete, not `request.state.user` typed as `Any`.
  2. *It's in the schema.* FastAPI walks the dependency tree to build OpenAPI,
     so a protected route automatically shows a padlock in /docs and the
     Authorize button actually works. Middleware is invisible to the spec.
  3. *It composes.* `get_current_active_user` depends on `get_current_user`
     depends on `oauth2_scheme` and `get_db`. Each link is small and separately
     testable, and any route can enter the chain at whatever level it needs.

And the failure mode it removes: with middleware you match protected paths by
URL pattern, so a new route is unprotected *by default* and a typo in a regex
opens a hole silently. Here, protection is a parameter in the signature — a
route either asks for a user or it doesn't, visible in the same three lines as
the handler.
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

# Pulls the token out of the `Authorization: Bearer <token>` header, 401s if the
# header is missing or malformed, and — the part that isn't obvious — declares
# the scheme in the OpenAPI spec.
#
# `tokenUrl` is *documentation*, not routing. This class never calls that URL;
# it tells clients (and the /docs Authorize dialog) where to exchange a
# username and password for a token. It's relative and has no leading slash so
# the docs page resolves it correctly when the app is mounted under a prefix.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def _credentials_error() -> HTTPException:
    """Build the one 401 that every authentication failure returns.

    A factory rather than a module-level constant on purpose: raising the same
    exception *instance* from every request would let Python attach each new
    traceback to that shared object, so it accumulates across requests. Cheap to
    build a fresh one; annoying to debug the alternative.

    `WWW-Authenticate` isn't decoration either — RFC 7235 says a 401 must say
    *how* to authenticate, and HTTP clients look for it to decide whether to
    prompt for credentials or retry.

    One message for every cause, deliberately. Expired, forged, malformed,
    deleted user — the client learns only "that didn't work". Reporting which
    part failed would hand an attacker a debugger for their own forgery: "bad
    signature" versus "no such user" confirms the user id was a real one.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the bearer token on this request into the `User` who owns it.

    Note that this depends on *other* dependencies (`oauth2_scheme`, `get_db`).
    FastAPI resolves that graph depth-first and caches each node per request, so
    a route depending on both this and `get_db` shares one session and one
    database transaction rather than opening two.

    The database lookup at the end is a real design choice, not a formality. The
    token alone is cryptographically sufficient — it's signed, so the id in it
    is trustworthy — and skipping the query would make auth zero-database. But
    a token stays valid until it expires, so without this check a user deleted
    or deactivated five minutes ago keeps working for the rest of the window,
    and every downstream route gets a `user_id` pointing at a row that may not
    exist. One indexed primary-key lookup is a fair price for "the account still
    exists right now".
    """
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        # Catches the whole family at once: expired signature, bad signature,
        # malformed segments, wrong algorithm. They're one outcome to the
        # client, so they're one `except` here — see `_credentials_error`.
        raise _credentials_error() from exc

    subject = payload.get("sub")
    if subject is None:
        # A validly-signed token that doesn't say who it's for. Shouldn't happen
        # with tokens this app minted, which is exactly why it's worth handling:
        # it means someone else's key signed it, or the claim set changed.
        raise _credentials_error()

    try:
        # `sub` is a string by spec (see create_access_token), so it has to be
        # converted back. Guarded because the value ultimately arrived over the
        # network — "trust the signature" doesn't mean "trust the parse".
        user_id = int(subject)
    except ValueError as exc:
        raise _credentials_error() from exc

    # `db.get()` rather than a query: it checks the session's identity map first
    # and only emits SELECT on a miss, and it's the idiomatic primary-key
    # fetch in SQLAlchemy 2.0.
    user = db.get(User, user_id)
    if user is None:
        # Signed correctly but the account is gone — deleted since the token was
        # issued. Still a 401: the credentials no longer identify anyone.
        raise _credentials_error()

    return user


def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """`get_current_user`, plus the check that the account isn't disabled.

    Split into its own dependency because the two questions are different, and
    a few routes will want the first without the second — a re-activation
    endpoint has to be reachable *by* a deactivated user, and so does "download
    my data" for someone who closed their account.

    403, not 401: the credentials were perfectly valid and we know exactly who
    this is. That's the line between the two codes — 401 means "I don't know who
    you are", 403 means "I do, and the answer is still no". Returning 401 here
    would tell a well-behaved client to throw away a working token and prompt
    for a password that was never the problem.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )
    return current_user


# The alias every protected route should use:
#
#     @router.get("/transactions")
#     def list_transactions(user: CurrentUser, db: DbSession): ...
#
# `Annotated[X, Depends(f)]` is a type and a dependency in one object, so naming
# it once here means routes never repeat the `Depends(...)` wiring — and the day
# this needs to become `get_current_verified_user`, it changes in one place
# instead of in every signature across the codebase.
CurrentUser = Annotated[User, Depends(get_current_active_user)]
DbSession = Annotated[Session, Depends(get_db)]
