#!/usr/bin/env python3
"""Writes k6/data/jwt/tokens.json: 10,000 RS256 tokens, token i has sub = i + 1, valid 30 days.

Signed with the test key from k6/data/jwt/gen-keys.sh. Needs PyJWT with cryptography.
Usage: python3 k6/data/gen-tokens.py [count]
"""
import json
import pathlib
import sys
import time

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key

here = pathlib.Path(__file__).resolve().parent / "jwt"
key = load_pem_private_key((here / "private.pem").read_bytes(), password=None)  # parse once, not per token
count = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
now = int(time.time())
tokens = [
    jwt.encode({"sub": str(i), "iss": "bench-idp", "aud": "bench-api", "iat": now, "exp": now + 30 * 86400},
               key, algorithm="RS256")
    for i in range(1, count + 1)
]
(here / "tokens.json").write_text(json.dumps(tokens))
print(f"wrote {count} tokens to {here / 'tokens.json'} (valid 30 days)")
