"""The response shape of a successful login.

Small, but not arbitrary: the field names come from OAuth2 (RFC 6749 §5.1), and
matching them is what lets standard clients — including the "Authorize" button
in FastAPI's own /docs — consume this endpoint without custom glue.
"""

from typing import Literal

from pydantic import BaseModel


class Token(BaseModel):
    """What POST /auth/login returns.

    ```json
    {"access_token": "eyJhbGciOiJIUzI1NiIs...", "token_type": "bearer"}
    ```

    `token_type` looks redundant when there's only one type, but it tells the
    client how to *use* the token — "bearer" means the literal header is
    `Authorization: Bearer <token>`. Hardcoding that on the client is what makes
    swapping schemes later a breaking change. It's typed as a `Literal` so the
    generated OpenAPI spec advertises the exact constant, not just "some string".
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
