"""Authentication routes: register, login, and "who am I".

Three endpoints that between them cover the whole loop — create an identity,
exchange a password for a token, and use that token. Everything cryptographic
lives in `app/core/security.py` and everything about turning a token back into a
`User` lives in `app/core/deps.py`; this module is only the HTTP shape of it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, DbSession
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.token import Token
from app.schemas.user import UserCreate, UserRead

# `prefix` means every path below is relative — "/register" becomes
# "/auth/register" — so the URL space moves by editing one string.
# `tags` groups these endpoints under one heading in /docs.
router = APIRouter(prefix="/auth", tags=["auth"])


def _authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Return the user matching these credentials, or None.

    Not a FastAPI dependency — a plain helper, and typed `Session` rather than
    `DbSession` to say so: nothing is injected here, the caller passes the
    session it already has. Dependencies run *before* a handler and can only
    fail by raising; this needs to return None so the caller decides what a
    failure means. Login turns it into a 401; a "confirm your password" step
    would turn the same None into a 422.

    The `else` branch is the interesting part. Verifying against a throwaway
    hash for an email that doesn't exist looks like pointless work, and that's
    precisely the point: it spends the same ~250ms bcrypt would have spent on a
    real user. Skip it and response *time* becomes an oracle — fast means "no
    account here", slow means "that email is registered" — which lets anyone
    enumerate this app's users a few hundred requests at a time. On a finance
    app, membership alone is information worth withholding.
    """
    user = db.scalar(select(User).where(User.email == email))

    if user is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        return None

    if not verify_password(password, user.hashed_password):
        return None

    return user


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
def register(payload: UserCreate, db: DbSession) -> User:
    """Create an account.

    Returns the created user — not a token. Registration and login stay separate
    so there's exactly one place tokens are minted, which is also the place
    you'd later add rate limiting or 2FA. (Auto-login on signup is a fine
    product decision; it just belongs in the client calling both endpoints.)

    The return type is the ORM `User`, but the *response* is `UserRead`.
    FastAPI serializes through `response_model`, so `hashed_password` is
    dropped on the way out even though the object returned here has it. That's
    the models/schemas split doing its job — the leak is prevented by the
    contract, not by remembering to strip a field.
    """
    # The friendly check: catches the ordinary case and gives a clear error.
    # It is *not* what guarantees uniqueness — see the IntegrityError below.
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        # The only place a plaintext password is ever touched, and it isn't
        # stored — `hash_password` is one-way, so a database dump leaks work
        # factors, not passwords.
        hashed_password=hash_password(payload.password),
    )

    db.add(user)
    try:
        # The commit is the route's job, not `get_db`'s — the dependency closes
        # the session but never decides a half-finished request is worth saving.
        db.commit()
    except IntegrityError as exc:
        # Reached when two requests for the same email interleave: both SELECT,
        # both find nothing, both INSERT, and the UNIQUE index rejects the
        # loser. The check above is a race by construction — there's a gap
        # between reading and writing — so the constraint in PostgreSQL is the
        # real enforcement and this is how that enforcement surfaces as a 409
        # instead of a 500.
        #
        # The general rule: application checks are for good error messages,
        # database constraints are for correctness. Never only the first.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from exc

    # Populates server-generated columns (id, created_at) from the row that was
    # actually written. Needed because `expire_on_commit=False` (see
    # db/session.py) means the object isn't automatically refreshed after
    # commit, and `UserRead` requires both fields.
    db.refresh(user)
    return user


@router.post("/login", response_model=Token, summary="Exchange credentials for a token")
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> Token:
    """Verify a password and issue an access token.

    **Why this takes a form and not JSON.** `OAuth2PasswordRequestForm` reads
    `application/x-www-form-urlencoded` with fields named `username` and
    `password`, because that's what the OAuth2 password-grant spec says. It
    feels dated next to the JSON everything else here accepts, and the payoff
    is concrete: the Authorize button in /docs speaks exactly this, so the
    interactive documentation can log in and then send the token on every
    subsequent request without a line of custom code.

    So the field is `username` even though this app authenticates by email —
    renaming it would break the standard clients that are the entire reason for
    using the form. Requests look like:

        curl -X POST http://127.0.0.1:8000/auth/login \\
             -d "username=you@example.com&password=your-password"

    Note also that credentials travel in the *body*, never the query string —
    URLs end up in server logs, browser history, and Referer headers.
    """
    # `UserCreate` lowercases emails on the way in (so the UNIQUE index matches
    # what users expect), but form data skips Pydantic validation entirely —
    # `OAuth2PasswordRequestForm` hands over raw strings. Without normalizing
    # here, registering as "you@x.com" and logging in as "You@X.com" would fail
    # against a stored address that only differs in case.
    email = form_data.username.strip().lower()

    user = _authenticate_user(db, email, form_data.password)
    if user is None:
        # One message for "no such account" and "wrong password" both. Splitting
        # them would confirm which emails are registered — the same enumeration
        # leak the dummy-hash timing work above exists to close, so being
        # careless in the response body would undo it.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        # Checked here as well as in `get_current_active_user`: no reason to
        # hand out a token that every protected route will refuse.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )

    # The user id, not the email, is the token's subject. Ids are immutable;
    # an email can be changed, and a token carrying the old one would either
    # break or — worse — resolve to whoever registers that address next.
    return Token(access_token=create_access_token(subject=user.id))


@router.get("/me", response_model=UserRead, summary="Get the current user")
def read_current_user(current_user: CurrentUser) -> User:
    """Return the authenticated user's own profile.

    The demonstration of the whole chain, and worth reading as one line: there
    is no token parsing here, no session, no `if not authorized`. The single
    parameter `current_user: CurrentUser` is what makes this route protected —
    FastAPI sees the dependency, resolves header → token → signature → database
    row before the body runs, and returns 401 on its own if any step fails.

    Every future protected route (`/transactions`, `/accounts`, ...) is this
    same one-parameter change, and `current_user.id` is the value that scopes
    their queries so one user can never read another's rows.
    """
    return current_user
