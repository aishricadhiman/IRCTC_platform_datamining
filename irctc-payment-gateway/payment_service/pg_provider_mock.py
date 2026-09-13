"""
Mock external Payment Gateway Provider (stands in for Razorpay / Billdesk /
a bank's own PG in this local demo).

Outcomes are randomized across SUCCESS / FAILED / PENDING so the system
naturally exercises all three flows described in the design document. An
optional `force_outcome` lets tests/demos deterministically trigger a
specific flow (e.g. to reliably demonstrate the timeout-recovery path)
without waiting on real randomness - this parameter is a TEST HOOK ONLY and
would not exist on a real PG's API.
"""
import random
import time
import uuid

TERMINAL_STATES = {"SUCCESS", "FAILED"}


class MockPaymentGatewayProvider:
    def __init__(self, success_rate: float = 0.7, failure_rate: float = 0.15):
        self.success_rate = success_rate
        self.failure_rate = failure_rate
        # pending_rate is implicitly 1 - success_rate - failure_rate
        self._store = {}  # pg_payment_id -> record

    def create_order(self, txn_reference: str, amount: float, idempotency_key: str, force_outcome: str = None):
        pg_payment_id = f"pay_{uuid.uuid4().hex[:14]}"

        if force_outcome in ("SUCCESS", "FAILED", "PENDING"):
            outcome = force_outcome
        else:
            roll = random.random()
            if roll < self.success_rate:
                outcome = "SUCCESS"
            elif roll < self.success_rate + self.failure_rate:
                outcome = "FAILED"
            else:
                outcome = "PENDING"

        self._store[pg_payment_id] = {
            "status": outcome,
            "amount": amount,
            "txn_reference": txn_reference,
            "created_at": time.time(),
            # A PENDING order resolves itself after a short random delay, simulating
            # a delayed bank/UPI response that the reconciliation job will pick up.
            "resolve_after": time.time() + random.uniform(5, 12) if outcome == "PENDING" else None,
        }
        return {
            "pg_payment_id": pg_payment_id,
            "status": outcome if outcome != "PENDING" else "PROCESSING",
        }

    def get_status(self, pg_payment_id: str):
        record = self._store.get(pg_payment_id)
        if not record:
            return {"status": "UNKNOWN"}
        if record["status"] == "PENDING" and record["resolve_after"] and time.time() >= record["resolve_after"]:
            record["status"] = random.choice(["SUCCESS", "FAILED"])
        return {"status": record["status"] if record["status"] != "PENDING" else "PROCESSING"}

    def refund(self, pg_payment_id: str, amount: float):
        # Real PGs settle refunds over 5-7 business days; this demo completes instantly.
        return {"pg_refund_id": f"rfnd_{uuid.uuid4().hex[:14]}", "status": "COMPLETED"}


# Singleton instance shared by all requests within the Payment Service process.
pg_provider = MockPaymentGatewayProvider()
