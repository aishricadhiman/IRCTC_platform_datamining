"""
Pytest suite for Payment Service, run in isolation - no Booking or Seat
Reservation service required. Uses a made-up booking_id since Payment
Service doesn't enforce that foreign key itself (that check belongs to
Booking Service in the full system).

Prerequisites (leave this running in a separate terminal):
    python scripts/run_payment_only.py

Then run the tests:
    pytest tests/test_payment_service.py -v
"""
import time
import uuid

import httpx
import pytest

PAYMENT_URL = "http://127.0.0.1:8003"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def ensure_service_running():
    """
    Runs once before any test. Fails fast with a clear message instead of a
    confusing connection-refused error buried inside the first test, if
    Payment Service isn't up yet.
    """
    try:
        httpx.get(f"{PAYMENT_URL}/docs", timeout=3)
    except httpx.RequestError:
        pytest.fail(
            f"Payment Service is not reachable at {PAYMENT_URL}.\n"
            f"Start it first in another terminal with:\n"
            f"    python scripts/run_payment_only.py",
            pytrace=False,
        )


def initiate(booking_id, amount, idempotency_key, force_outcome=None):
    payload = {
        "booking_id": booking_id, "user_id": 1, "amount": amount,
        "payment_method": "UPI", "idempotency_key": idempotency_key,
    }
    if force_outcome:
        payload["force_outcome"] = force_outcome
    resp = httpx.post(f"{PAYMENT_URL}/payment/initiate", json=payload)
    resp.raise_for_status()
    return resp.json()


def get_status(txn_reference):
    resp = httpx.get(f"{PAYMENT_URL}/payment/status", params={"txn_reference": txn_reference})
    resp.raise_for_status()
    return resp.json()


def wait_for_terminal(txn_reference, timeout=40):
    start = time.time()
    while time.time() - start < timeout:
        status = get_status(txn_reference)
        if status["status"] in ("SUCCESS", "FAILED"):
            return status
        time.sleep(1)
    raise TimeoutError(f"{txn_reference} did not resolve within {timeout}s")


def pay_successfully(booking_id=1001, amount=2520.0):
    """Shared helper for tests that need an already-SUCCESS transaction (e.g. refund)."""
    txn = initiate(booking_id, amount, str(uuid.uuid4()), force_outcome="SUCCESS")
    return wait_for_terminal(txn["transactionId"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_success_flow():
    txn = initiate(1001, 2520.0, str(uuid.uuid4()), force_outcome="SUCCESS")
    final = wait_for_terminal(txn["transactionId"])
    assert final["status"] == "SUCCESS", final


def test_failure_flow():
    txn = initiate(1002, 2520.0, str(uuid.uuid4()), force_outcome="FAILED")
    final = wait_for_terminal(txn["transactionId"])
    assert final["status"] == "FAILED", final


def test_idempotency_returns_same_transaction():
    key = str(uuid.uuid4())
    txn1 = initiate(1003, 2520.0, key, force_outcome="SUCCESS")
    txn2 = initiate(1003, 2520.0, key, force_outcome="SUCCESS")
    assert txn1["transactionId"] == txn2["transactionId"]


def test_idempotency_rejects_conflicting_payload():
    key = str(uuid.uuid4())
    initiate(1003, 2520.0, key, force_outcome="SUCCESS")
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        # Same idempotency key, DIFFERENT amount -> must be rejected, not silently accepted.
        initiate(1003, 999.0, key, force_outcome="SUCCESS")
    assert exc_info.value.response.status_code == 409


def test_refund_flow():
    success = pay_successfully(booking_id=1005)
    resp = httpx.post(f"{PAYMENT_URL}/payment/refund", json={
        "booking_id": success["bookingId"], "txn_reference": success["transactionId"],
        "refund_amount": 2268.0, "cancellation_charge": 252.0,
        "idempotency_key": str(uuid.uuid4()),
    })
    resp.raise_for_status()
    refund = resp.json()
    assert refund["status"] == "COMPLETED", refund


def test_timeout_then_reconciliation_recovers():
    txn = initiate(1004, 2520.0, str(uuid.uuid4()), force_outcome="PENDING")
    assert txn["status"] == "PROCESSING"
    # 90s gives headroom over PAYMENT_MAX_WAIT_SECONDS (60s default) so this
    # isn't a razor-thin race against the reconciliation job's own deadline.
    final = wait_for_terminal(txn["transactionId"], timeout=90)
    assert final["status"] in ("SUCCESS", "FAILED"), final
