"""Registration, login and the protected-route dependency.

The theme: **most of these assert on what the API refuses to say.** A password
hash that never appears in a response, an error message that doesn't reveal
whether an account exists, a token that stops working when it's been tampered
with. Those are the properties that are easy to break in a refactor and
invisible when you break them, which is exactly what makes them worth pinning.
"""

import jwt
import pytest

from app.core.config import settings
from tests.conftest import USER_EMAIL, USER_PASSWORD


def test_register_creates_user_and_never_returns_the_password(client):
    """201, and the response body has no trace of the credential.

    `hashed_password` is a column on the object the handler returns, so this is
    not testing that nobody set a field — it's testing that `UserRead` is what
    serializes the response. Add a field to that schema carelessly and this
    fails, which is the point: the leak is prevented by the contract, and the
    contract deserves a test.
    """
    response = client.post(
        "/auth/register",
        json={"email": "new@example.com", "password": "a-long-enough-password"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["is_active"] is True
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_rejects_a_duplicate_email(client, user):
    """409, and the same message whichever way the collision is detected.

    The handler checks with a SELECT first and relies on the UNIQUE index
    underneath. Both paths must answer identically — a difference between them
    would be a timing-dependent error message, which is a miserable thing to
    debug.
    """
    response = client.post(
        "/auth/register",
        json={"email": USER_EMAIL, "password": "some-other-password"},
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_login_returns_a_bearer_token(client, user):
    """The happy path, and a reminder that this endpoint takes a *form*.

    `data=` not `json=`, and the field is `username` even though the value is an
    email — that's `OAuth2PasswordRequestForm`'s shape, and getting it wrong is
    the most common way a client fails against this route.
    """
    response = client.post(
        "/auth/login",
        data={"username": USER_EMAIL, "password": USER_PASSWORD},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"

    claims = jwt.decode(
        body["access_token"], settings.secret_key, algorithms=[settings.jwt_algorithm]
    )
    # `sub` is a string by JWT spec even though the user id is an integer.
    assert claims["sub"] == str(user.id)


@pytest.mark.parametrize(
    ("email", "password"),
    [
        (USER_EMAIL, "wrong-password"),
        ("nobody@example.com", USER_PASSWORD),
    ],
    ids=["wrong-password", "no-such-account"],
)
def test_login_does_not_reveal_whether_an_account_exists(client, user, email, password):
    """Both failures answer 401 with the *identical* message.

    This is the whole reason the two cases are parametrized into one test rather
    than written as two: the assertion is that they are indistinguishable. Split
    them and it becomes possible for the messages to drift apart while both
    tests still pass.

    An attacker who can tell "wrong password" from "no such account" has a free
    account-enumeration oracle — they learn which addresses are registered here,
    which is worth real money to whoever buys credential-stuffing lists.
    """
    response = client.post("/auth/login", data={"username": email, "password": password})

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_protected_route_requires_a_valid_token(client, user, auth_headers):
    """`/auth/me` with a good token, no token, and a forged one.

    The forged case is the interesting third: a token with a perfectly
    well-formed payload claiming to be this same user, signed with a key that
    isn't the server's. It must fail — otherwise the signature is decoration and
    anyone can mint a token for any user id they can guess.
    """
    ok = client.get("/auth/me", headers=auth_headers)
    assert ok.status_code == 200
    assert ok.json()["email"] == USER_EMAIL

    anonymous = client.get("/auth/me")
    assert anonymous.status_code == 401

    forged = jwt.encode({"sub": str(user.id)}, "not-the-servers-secret", algorithm="HS256")
    tampered = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert tampered.status_code == 401
    # One message for every cause: expired, forged, malformed, deleted user.
    assert tampered.json()["detail"] == "Could not validate credentials"
