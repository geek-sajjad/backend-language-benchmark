"""Test 9: RS256 JWT check for the /secure routes (PyJWT)."""

from pathlib import Path
from typing import Annotated

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .schemas import INT32_MAX


def load_public_key(path: str) -> RSAPublicKey:
    """Read the PEM once at startup; no JWKS fetching (docs/02-test-spec.md, Authentication)."""
    key = load_pem_public_key(Path(path).read_bytes())
    if not isinstance(key, RSAPublicKey):
        raise ValueError(f"{path} is not an RSA public key")
    return key


bearer = HTTPBearer(auto_error=False)


async def current_customer(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> int:
    """Signature, exp, iss and aud are checked by PyJWT; sub must be a customer id."""
    if credentials is None:
        raise HTTPException(401)
    try:
        claims = jwt.decode(
            credentials.credentials,
            request.state.jwt_key,
            algorithms=["RS256"],
            audience="bench-api",
            issuer="bench-idp",
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(401) from None
    sub = claims["sub"]
    if not sub.isdecimal() or int(sub) > INT32_MAX:
        raise HTTPException(401)
    return int(sub)


# FastAPI caches a dependency per request, so the router-level guard and a handler's
# CurrentCustomer parameter verify the token once.
CurrentCustomer = Annotated[int, Depends(current_customer)]
require_auth = Depends(current_customer)
