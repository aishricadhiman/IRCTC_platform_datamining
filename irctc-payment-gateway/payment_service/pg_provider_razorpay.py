"""
Real Payment Gateway Provider - Razorpay (Test Mode).

Unlike pg_provider_mock.py, this talks to Razorpay's actual servers using
TEST API keys. No real money moves - Razorpay's test mode uses official
test card/UPI numbers to genuinely simulate success/failure (not randomly;
the outcome is determined by which test credential you use at checkout),
and the success signal is verified with real HMAC-SHA256 cryptography,
exactly like production.

Setup (one-time):
    1. Sign up free at https://dashboard.razorpay.com/signup (no KYC needed
       for Test Mode).
    2. Go to Settings -> API Keys -> "Generate Test Key". Copy the Key Id
       and Key Secret.
    3. Set them as environment variables before starting the service:

         # Windows PowerShell
         $env:RAZORPAY_KEY_ID="rzp_test_xxxxxxxxxxxx"
         $env:RAZORPAY_KEY_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"

         # macOS/Linux
         export RAZORPAY_KEY_ID="rzp_test_xxxxxxxxxxxx"
         export RAZORPAY_KEY_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"

    4. Start the service as usual. If both variables are set, Payment
       Service automatically uses Razorpay instead of the random mock.

Official Razorpay test credentials (use these at checkout to control the
outcome, instead of it being random):
    Success card:  4111 1111 1111 1111   any future expiry, any CVV
    Failure card:  4000 0000 0000 0002   any future expiry, any CVV
    Success UPI:   success@razorpay
    Failure UPI:   failure@razorpay
Full list: https://razorpay.com/docs/payments/payments/test-card-upi-details/
"""
import hmac
import hashlib

import razorpay


class RazorpayProvider:
    def __init__(self, key_id: str, key_secret: str):
        self.key_id = key_id
        self.key_secret = key_secret
        self.client = razorpay.Client(auth=(key_id, key_secret))

    def create_order(self, txn_reference: str, amount: float, idempotency_key: str = None):
        """
        Creates a real Razorpay Order (test mode). Amount must be in paise
        (smallest currency unit) per Razorpay's API.
        """
        order = self.client.order.create({
            "amount": int(round(amount * 100)),
            "currency": "INR",
            "receipt": txn_reference,
            "payment_capture": 1,  # auto-capture on successful authorization
        })
        return {"pg_order_id": order["id"], "status": "PROCESSING"}

    def verify_payment_signature(self, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
        """
        Verifies the checkout's success callback using the SAME HMAC-SHA256
        scheme Razorpay uses in production. This is the real cryptographic
        check that proves the payment genuinely succeeded and the response
        wasn't forged by a malicious client.
        """
        try:
            self.client.utility.verify_payment_signature({
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            })
            return True
        except razorpay.errors.SignatureVerificationError:
            return False

    def verify_webhook_signature(self, payload_body: bytes, signature: str, webhook_secret: str) -> bool:
        """Verifies a real Razorpay webhook's X-Razorpay-Signature header."""
        expected = hmac.new(
            webhook_secret.encode(), payload_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def fetch_payment(self, payment_id: str):
        return self.client.payment.fetch(payment_id)

    def get_status(self, pg_order_id: str):
        """Used by the reconciliation job to poll an order that never got a
        client-side /payment/verify call (e.g. user closed the tab, or the
        browser callback lost the race to the reconciliation job). When the
        order is paid, also fetches which specific payment completed it -
        the order itself doesn't carry a payment id, only its payments list
        does."""
        order = self.client.order.fetch(pg_order_id)
        if order.get("status") != "paid":
            return {"status": "PROCESSING"}

        payment_id = None
        try:
            payments = self.client.order.payments(pg_order_id)
            items = payments.get("items", [])
            captured = [p for p in items if p.get("status") in ("captured", "authorized")]
            if captured:
                payment_id = captured[0]["id"]
            elif items:
                payment_id = items[0]["id"]
        except Exception:
            pass  # order is confirmed paid either way; payment_id is best-effort detail

        return {"status": "SUCCESS", "pg_payment_id": payment_id}

    def refund(self, payment_id: str, amount: float):
        result = self.client.payment.refund(payment_id, {"amount": int(round(amount * 100))})
        return {"pg_refund_id": result["id"], "status": "COMPLETED" if result.get("status") in (None, "processed") else "FAILED"}
