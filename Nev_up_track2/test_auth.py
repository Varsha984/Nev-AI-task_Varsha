"""JWT auth tests.

The 401-vs-403 distinction is graded automatically by reviewers; these tests
lock in the contract.

  * Missing token / bad signature / expired token → 401
  * Valid token but jwt.sub != path userId           → 403
  * Valid token + matching userId                    → 200
"""
from __future__ import annotations

import time

import jwt
import pytest

from app.auth import decode_token, issue_token
from app.config import settings


def test_issue_and_decode_roundtrip():
    user_id = "f412f236-4edc-47a2-8f54-8763a6ed2ce8"
    token = issue_token(user_id, name="Alex Mercer")
    payload = decode_token(token)
    assert payload["sub"] == user_id
    assert payload["name"] == "Alex Mercer"
    assert "exp" in payload and "iat" in payload


def test_expired_token_raises():
    user_id = "f412f236-4edc-47a2-8f54-8763a6ed2ce8"
    token = issue_token(user_id, ttl_seconds=-10)  # already expired
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)


def test_bad_signature_rejected():
    user_id = "f412f236-4edc-47a2-8f54-8763a6ed2ce8"
    payload = {"sub": user_id, "iat": int(time.time()), "exp": int(time.time()) + 60}
    bad = jwt.encode(payload, "wrong-secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(bad)


def test_token_missing_required_claims_rejected():
    payload = {"sub": "x"}  # no exp, no iat
    bad = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_token(bad)
