"""Password hashing and JSON Web Tokens — the two cryptographic primitives auth
is built from.

Deliberately free of FastAPI: nothing here imports `HTTPException`, and nothing
here touches the database. These are pure functions over strings, which is what
makes them testable without a client or a session, and what lets the same
`create_access_token` serve a login route today and a password-reset email
later. Turning a failure into a 401 is the *web* layer's job — see
`app/core/deps.py`.

The two halves solve genuinely different problems, and conflating them is a
common source of confusion:

  - Hashing answers "is this the right password?" and must be **slow and
    irreversible**. There is no un-hash.
  - JWTs answer "who is this request from?" and are **signed, not encrypted** —
    readable by anyone, forgeable by no one without the key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# --- Password hashing ------------------------------------------------------

# bcrypt only reads the first 72 bytes of a password and — as of bcrypt 4.x —
# does so *silently*, without raising. Left unchecked that is a real
# vulnerability, not a curiosity: for a 100-character passphrase, every string
# sharing its first 72 bytes authenticates successfully, so the last 28
# characters are decorative. Callers must reject anything longer; the schema in
# `app/schemas/user.py` does it at the edge so a bad request is a 422, not a
# surprise deep in this module.
#
# Bytes, not characters: it's the UTF-8 encoding that gets truncated, and
# "é" is two bytes while "😀" is four. Validating `len(password)` would let a
# 72-character emoji passphrase through at 288 bytes.
MAX_PASSWORD_BYTES = 72


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password for storage.

    `gensalt()` generates a fresh random salt per call, which is why hashing the
    same password twice yields two different strings — and why an attacker can't
    build one rainbow table that cracks every user at once. The salt isn't a
    secret and doesn't need storing separately: bcrypt packs the algorithm,
    cost, and salt into the output, so the hash is self-describing.

        $2b$12$eIm3F0S4EJDBu8FTVZ0Dxe...
        │   │  └─ 22-char salt        └─ 31-char digest
        │   └──── cost: 2^12 iterations
        └──────── algorithm variant

    The default cost of 12 is the whole point of using bcrypt over SHA-256:
    it makes a single guess take ~0.25s instead of microseconds, so an attacker
    who steals the table is throttled to a few guesses per second per core
    rather than billions. That deliberate slowness is a *feature*, and it's why
    login is measurably slower than other routes.
    """
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored hash.

    Works by re-hashing the candidate with the salt embedded in `hashed_password`
    and comparing — hashes are one-way, so there is no decrypting to compare
    against. `checkpw` does that comparison in constant time, so an attacker
    can't narrow down the correct hash by measuring how long a rejection took.

    Returns False rather than raising on a malformed stored hash (a truncated
    column, a value written by some earlier scheme). A corrupt row should read
    as "wrong password" and lock that one account out, not 500 the endpoint.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        return False


# A hash of a throwaway password, computed once at import.
#
# Its only job is to burn the same ~0.25s that a real `verify_password` costs.
# Without it, login is a timing oracle: an unknown email returns immediately
# while a known one pays for the bcrypt comparison, and that gap is measurable
# over the network. An attacker who can distinguish the two can enumerate which
# email addresses have accounts here — worth hiding on a *finance* app, where
# the mere fact that someone is a user is sensitive.
#
# See `authenticate_user` in app/routers/auth.py for the use.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


# --- JSON Web Tokens -------------------------------------------------------


def create_access_token(subject: str | int, expires_delta: timedelta | None = None) -> str:
    """Mint a signed access token identifying `subject` (here, a user id).

    The result is three base64 segments joined by dots — header.payload.signature
    — and only the third is cryptographic. **The payload is encoded, not
    encrypted**: anyone holding the token can paste it into jwt.io and read
    every claim. Never put anything secret in it. What the signature guarantees
    is *integrity*, not privacy: change a single byte of the payload and
    verification fails, because reproducing the signature needs `secret_key`.

    Claim names are the registered ones from RFC 7519, not inventions:
      sub — "subject": who the token is about.
      iat — "issued at": lets you later reject tokens minted before a password
            change, without tracking individual tokens.
      exp — "expires": the one claim PyJWT enforces on its own during decode.
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))

    # `sub` is cast to str because the JWT spec says it's a string, and PyJWT
    # 2.10+ enforces that on decode — passing the raw int id produces tokens
    # this app's own `decode_access_token` would then reject.
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify a token's signature and expiry, returning its claims.

    Raises `jwt.PyJWTError` (subclasses: `ExpiredSignatureError`,
    `InvalidSignatureError`, `DecodeError`, ...) on anything wrong. Callers that
    only need "valid or not" can catch the base class — deps.py does exactly
    that, since every failure mode maps to the same 401. Distinguishing them in
    the *response* would tell an attacker which part of their forgery to fix.

    `algorithms=[...]` is a security control, not boilerplate. It's a whitelist:
    without it the library would trust the `alg` field in the token's own
    header, which is attacker-controlled. That's the classic JWT vulnerability —
    submit `alg: none` and the signature is skipped entirely, or downgrade an
    RS256 setup to HS256 so the *public* key gets used as the HMAC secret.
    Pinning the algorithm server-side means the token doesn't get a vote.
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
