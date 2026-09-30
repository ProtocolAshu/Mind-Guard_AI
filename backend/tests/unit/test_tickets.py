from datetime import UTC, datetime, timedelta

import pytest

from app.security.tickets import TicketError, derive_ticket_key, issue_ticket, verify_ticket

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
KEY = derive_ticket_key("x" * 48)
CLAIMS = {"run_id": "r1", "user_id": "u1", "decision": "DELAY", "duration_minutes": 2, "app_package": "com.x.y"}


def test_round_trip_and_expected_claims():
    token = issue_ticket(KEY, CLAIMS, now=NOW)
    claims = verify_ticket(KEY, token, now=NOW + timedelta(seconds=10), expected={"user_id": "u1", "decision": "DELAY"})
    assert claims["run_id"] == "r1"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t[:-2] + ("AA" if not t.endswith("AA") else "BB"),
        lambda t: "e30." + t.split(".", 1)[1],
        lambda t: "garbage",
    ],
)
def test_tampered_tickets_are_rejected(mutate):
    with pytest.raises(TicketError):
        verify_ticket(KEY, mutate(issue_ticket(KEY, CLAIMS, now=NOW)), now=NOW)


def test_expired_wrong_key_and_claim_mismatch_rejected():
    token = issue_ticket(KEY, CLAIMS, now=NOW, ttl_seconds=60)
    with pytest.raises(TicketError, match="expired"):
        verify_ticket(KEY, token, now=NOW + timedelta(minutes=5))
    with pytest.raises(TicketError, match="signature"):
        verify_ticket(derive_ticket_key("y" * 48), token, now=NOW)
    with pytest.raises(TicketError, match="mismatch"):
        verify_ticket(KEY, token, now=NOW, expected={"decision": "TEMPORARY_BLOCK"})
