"""Authorization tickets: proof that a decision passed the guardrail engine.

The guardrails sign `(run, user, app, decision, duration, expiry)`; the Action tool
refuses to execute without a valid ticket. LLM output or a forged tool call can
therefore never reach the device-command path (security test case 3).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import ensure_utc


class TicketError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def derive_ticket_key(master_secret: str) -> bytes:
    return hmac.new(master_secret.encode("utf-8"), b"mindguard/authorization-ticket/v1", hashlib.sha256).digest()


def issue_ticket(key: bytes, claims: dict[str, Any], *, now: datetime, ttl_seconds: int = 300) -> str:
    body = dict(claims)
    body["exp"] = int((ensure_utc(now) + timedelta(seconds=ttl_seconds)).timestamp())
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_ticket(key: bytes, token: str, *, now: datetime, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        signature = _unb64(sig_b64)
    except (ValueError, TypeError) as exc:
        raise TicketError("malformed ticket") from exc
    if not hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).digest(), signature):
        raise TicketError("invalid ticket signature")
    claims: dict[str, Any] = json.loads(payload)
    if int(claims.get("exp", 0)) < int(ensure_utc(now).timestamp()):
        raise TicketError("ticket expired")
    for field, value in (expected or {}).items():
        if str(claims.get(field)) != str(value):
            raise TicketError(f"ticket claim mismatch: {field}")
    return claims
