"""
Payment Service

Manages the end-to-end payment lifecycle for bookings.

Responsibilities:
- Create and track payment transactions.
- Enforce idempotency to prevent duplicate payments.
- Integrate with Mock or Razorpay payment gateways.
- Verify payment outcomes through checkout verification and gateway callbacks.
- Maintain payment audit logs and state transitions.
- Process refunds for successful payments.
- Publish payment and refund events to other services.
- Reconcile stuck transactions through a background recovery job.

Modes:
- MOCK: Simulated gateway for local development and testing.
- RAZORPAY: Real Razorpay Test Mode with checkout and signature verification.
"""


import asyncio
import os
import uuid as uuid_lib
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from common.database import SessionLocal, Base, engine
from common import models, event_bus
from common.utils import gen_txn_reference, gen_refund_reference
from common.config import (
    PAYMENT_TIMEOUT_THRESHOLD_SECONDS,
    PAYMENT_MAX_WAIT_SECONDS,
    RECONCILIATION_POLL_INTERVAL_SECONDS,
)
from .pg_provider_mock import MockPaymentGatewayProvider, TERMINAL_STATES

# Load variables from a local .env file (if present) into os.environ. Real
# environment variables set outside .env always take precedence and are
# never overwritten. Safe to call even when no .env file exists.
load_dotenv()

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Payment Service")

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")

if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    from .pg_provider_razorpay import RazorpayProvider
    pg_provider = RazorpayProvider(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    PG_MODE = "RAZORPAY"
    print("[payment-service] Using REAL Razorpay Test Mode provider.")
else:
    pg_provider = MockPaymentGatewayProvider()
    PG_MODE = "MOCK"
    print("[payment-service] Using MOCK provider (set RAZORPAY_KEY_ID / "
          "RAZORPAY_KEY_SECRET env vars to switch to real Razorpay Test Mode).")


@app.get("/payment/mode")
def get_mode():
    """Quick diagnostic: confirms whether this server is running in MOCK or
    RAZORPAY mode, without needing to scroll through startup logs."""
    return {"mode": PG_MODE}


# ---------------------------------------------------------------------------
# GET /payment/new  (browser convenience page)
#
# Swagger/curl can't make your browser navigate anywhere - a server can only
# redirect a request that a BROWSER itself made. This page solves that: you
# open it once, click Start Payment, and its own JavaScript calls
# /payment/initiate and then redirects your browser straight to the
# checkout page - no manual copy/pasting a URL between tabs.
# ---------------------------------------------------------------------------

@app.get("/payment/new", response_class=HTMLResponse)
def new_payment_page():
    html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>New Payment - IRCTC Payment Gateway</title>
  <style>
    body { font-family: Arial, sans-serif; text-align: center; padding-top: 60px; background:#f4f6f9; }
    .card { display:inline-block; padding:32px 40px; border-radius:12px; background:white;
            box-shadow:0 2px 10px rgba(0,0,0,0.1); text-align:left; width:320px; }
    label { display:block; margin-top:14px; font-size:13px; color:#555; }
    input, select { width:100%; padding:8px; margin-top:4px; box-sizing:border-box;
                    border:1px solid #ccc; border-radius:4px; }
    button { background:#0B2545; color:white; border:none; padding:12px 28px; font-size:16px;
             border-radius:6px; cursor:pointer; margin-top:20px; width:100%; }
  </style>
</head>
<body>
  <div class="card">
    <h2 style="text-align:center;">Start a Payment</h2>
    <!--
      Plain HTML form, no JavaScript at all. Submitting this IS a real
      browser navigation (a top-level POST), so the server's redirect
      response in /payment/pay-now takes the browser straight to the
      checkout page in one hop - genuinely "click once, land on the pay
      page directly."
    -->
    <form method="post" action="/payment/pay-now">
      <label>Booking ID <input name="booking_id" type="number" value="9001" required></label>
      <label>Amount (INR) <input name="amount" type="number" value="1200" step="0.01" required></label>
      <label>Payment Method
        <select name="payment_method">
          <option value="UPI">UPI</option>
          <option value="CARD">Card</option>
          <option value="NETBANKING">Netbanking</option>
        </select>
      </label>
      <button type="submit">Start Payment</button>
    </form>
  </div>
</body>
</html>
"""
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# POST /payment/initiate
# ---------------------------------------------------------------------------

class InitiateRequest(BaseModel):
    booking_id: int
    user_id: int
    amount: float
    payment_method: str
    idempotency_key: str
    force_outcome: Optional[str] = None  # MOCK MODE ONLY - ignored by Razorpay


def _create_transaction(booking_id: int, user_id: int, amount: float, payment_method: str,
                         idempotency_key: str, force_outcome: str = None):
    """
    Shared core logic behind both /payment/initiate (JSON API) and
    /payment/pay-now (native-form redirect). Returns the Transaction row.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(models.Transaction)
            .filter(models.Transaction.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            if float(existing.amount) != amount or existing.booking_id != booking_id:
                raise HTTPException(status_code=409, detail="Idempotency key reused with a different payload")
            return existing

        txn_ref = gen_txn_reference(booking_id)
        txn = models.Transaction(
            txn_reference=txn_ref,
            booking_id=booking_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            amount=amount,
            payment_method=payment_method,
            pg_provider=PG_MODE,
            status="INITIATED",
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)
        _audit(db, txn, "INITIATED", None, "INITIATED", "SYSTEM", {"booking_id": booking_id, "amount": amount})

        if PG_MODE == "RAZORPAY":
            pg_result = pg_provider.create_order(txn_ref, amount, idempotency_key)
        else:
            pg_result = pg_provider.create_order(txn_ref, amount, idempotency_key, force_outcome)

        old_status = txn.status
        txn.pg_order_id = pg_result.get("pg_order_id")
        txn.pg_payment_id = pg_result.get("pg_payment_id")
        txn.status = pg_result["status"]
        txn.version += 1
        db.commit()
        db.refresh(txn)
        _audit(db, txn, "PG_ORDER_CREATED", old_status, txn.status, "SYSTEM", pg_result)

        if txn.status in TERMINAL_STATES:
            _publish_outcome(txn)

        return txn
    finally:
        db.close()


@app.post("/payment/initiate")
def initiate_payment(req: InitiateRequest):
    txn = _create_transaction(
        req.booking_id, req.user_id, req.amount, req.payment_method,
        req.idempotency_key, req.force_outcome,
    )
    response = _txn_response(txn)
    if PG_MODE == "RAZORPAY":
        response["checkoutPageUrl"] = f"http://127.0.0.1:8003/payment/checkout/{txn.txn_reference}"
    return response


# ---------------------------------------------------------------------------
# POST /payment/pay-now  (native HTML form -> server-side redirect)
#
# This is the "truly one click" path: a plain HTML <form> POSTs here directly
# (no JavaScript involved at all). Because the BROWSER itself made this
# request (a real top-level navigation, not a background fetch), the server
# CAN respond with an HTTP redirect and the browser follows it automatically
# - landing straight on the real Razorpay checkout page in a single hop.
# This is the same pattern traditional (pre-SPA) web apps have always used
# to hand off to a payment gateway.
# ---------------------------------------------------------------------------

@app.post("/payment/pay-now")
def pay_now(
    booking_id: int = Form(...),
    amount: float = Form(...),
    payment_method: str = Form("UPI"),
):
    idempotency_key = f"web-{uuid_lib.uuid4()}"
    txn = _create_transaction(booking_id, 1, amount, payment_method, idempotency_key)

    if PG_MODE == "RAZORPAY":
        # 303 See Other: correct status for "redirect after a POST", tells
        # the browser to follow with a GET rather than resubmitting the form.
        return RedirectResponse(url=f"/payment/checkout/{txn.txn_reference}", status_code=303)

    # MOCK mode has no real checkout page to send you to - there's nothing
    # to click through, since force_outcome isn't exposed on this simplified
    # form. Send the browser to the plain JSON status endpoint instead.
    return RedirectResponse(url=f"/payment/status?txn_reference={txn.txn_reference}", status_code=303)


# ---------------------------------------------------------------------------
# GET /payment/checkout/{txn_reference}  (RAZORPAY MODE ONLY)
# Serves a real Razorpay Checkout page - open this URL in a browser to
# actually pay with a test card/UPI id and see a genuine result.
# ---------------------------------------------------------------------------

@app.get("/payment/checkout/{txn_reference}", response_class=HTMLResponse)
def checkout_page(txn_reference: str):
    if PG_MODE != "RAZORPAY":
        raise HTTPException(status_code=400, detail="Real checkout is only available in RAZORPAY mode")

    db = SessionLocal()
    try:
        txn = db.query(models.Transaction).filter(models.Transaction.txn_reference == txn_reference).first()
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
        if not txn.pg_order_id:
            raise HTTPException(status_code=422, detail="No Razorpay order associated with this transaction")

        amount_paise = int(round(float(txn.amount) * 100))
        html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>IRCTC Payment Gateway (Test Mode)</title>
  <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
  <style>
    body {{ font-family: Arial, sans-serif; text-align: center; padding-top: 80px; background:#f4f6f9; }}
    .card {{ display:inline-block; padding:32px 40px; border-radius:12px; background:white;
             box-shadow:0 2px 10px rgba(0,0,0,0.1); }}
    button {{ background:#0B2545; color:white; border:none; padding:12px 28px; font-size:16px;
              border-radius:6px; cursor:pointer; margin-top:16px; }}
    #result {{ margin-top:20px; font-weight:bold; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>IRCTC Ticket Payment</h2>
    <p>Booking #{txn.booking_id} &nbsp;|&nbsp; Amount: &#8377;{float(txn.amount):.2f}</p>
    <p style="color:#888;font-size:13px;">Test Mode - use official Razorpay test card/UPI ids,<br/>
       no real money will be charged.</p>
    <button id="pay-btn">Pay Now</button>
    <div id="result"></div>
  </div>
  <script>
    var options = {{
      "key": "{RAZORPAY_KEY_ID}",
      "amount": "{amount_paise}",
      "currency": "INR",
      "name": "IRCTC Payment Gateway (Demo)",
      "description": "Booking #{txn.booking_id}",
      "order_id": "{txn.pg_order_id}",
      "handler": function (response) {{
        document.getElementById('result').innerText = "Verifying payment...";
        fetch("/payment/verify", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            txn_reference: "{txn_reference}",
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature
          }})
        }})
        .then(r => r.json())
        .then(data => {{
          document.getElementById('result').innerText =
            data.verified ? "Payment SUCCESS - you may close this tab." : "Verification FAILED.";
        }});
      }},
      "modal": {{
        "ondismiss": function () {{
          document.getElementById('result').innerText = "Checkout closed without completing payment.";
        }}
      }},
      "theme": {{ "color": "#0B2545" }}
    }};
    var rzp = new Razorpay(options);
    document.getElementById('pay-btn').onclick = function (e) {{
      rzp.open();
      e.preventDefault();
    }};
  </script>
</body>
</html>
"""
        return HTMLResponse(content=html)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /payment/verify  (RAZORPAY MODE - browser-side checkout confirmation)
#
# Razorpay's Checkout.js returns razorpay_payment_id/order_id/signature to
# the BROWSER on success. The browser then sends them here so the SERVER can
# independently verify the signature with the (secret) key - the browser
# alone is never trusted to say "payment succeeded".
# ---------------------------------------------------------------------------

class VerifyRequest(BaseModel):
    txn_reference: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@app.post("/payment/verify")
def verify_payment(req: VerifyRequest):
    if PG_MODE != "RAZORPAY":
        raise HTTPException(status_code=400, detail="Verification only applies in RAZORPAY mode")

    db = SessionLocal()
    try:
        txn = db.query(models.Transaction).filter(models.Transaction.txn_reference == req.txn_reference).first()
        if not txn:
            raise HTTPException(status_code=404, detail="Unknown transaction")

        if txn.status in TERMINAL_STATES:
            return {"verified": txn.status == "SUCCESS", "note": "already-terminal, no-op"}

        is_valid = pg_provider.verify_payment_signature(
            req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature
        )
        _audit(db, txn, "CHECKOUT_VERIFY_ATTEMPT", txn.status, "SUCCESS" if is_valid else "FAILED",
               "CLIENT_CHECKOUT", req.dict())

        old_status = txn.status
        txn.pg_payment_id = req.razorpay_payment_id
        txn.status = "SUCCESS" if is_valid else "FAILED"
        txn.version += 1
        db.commit()
        _audit(db, txn, "STATE_CHANGED", old_status, txn.status, "CLIENT_CHECKOUT", req.dict())
        _publish_outcome(txn)

        return {"verified": is_valid, "status": txn.status}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /payment/callback  (server-to-server webhook from the PG provider)
# ---------------------------------------------------------------------------

class CallbackRequest(BaseModel):
    txn_reference: str
    pg_payment_id: str
    status: str  # SUCCESS | FAILED
    amount: float
    signature: Optional[str] = "mock-signature"


def _verify_signature(payload: dict) -> bool:
    # In production: verify the PG's HMAC signature against a shared secret,
    # check timestamp freshness against a replay window, and allow-list the
    # PG's source IPs (design doc §11).
    return True


@app.post("/payment/callback")
def payment_callback(req: CallbackRequest):
    if not _verify_signature(req.dict()):
        raise HTTPException(status_code=400, detail="Invalid signature")

    db = SessionLocal()
    try:
        txn = db.query(models.Transaction).filter(models.Transaction.txn_reference == req.txn_reference).first()
        if not txn:
            raise HTTPException(status_code=404, detail="Unknown transaction")

        _audit(db, txn, "CALLBACK_RECEIVED", txn.status, req.status, "PG_CALLBACK", req.dict())

        if txn.status in TERMINAL_STATES:
            # Idempotent no-op: duplicate/late callback on an already-terminal
            # transaction never reapplies a state change (design doc §10.2).
            return {"acknowledged": True, "note": "already-terminal, no-op"}

        old_status = txn.status
        txn.status = req.status
        txn.version += 1
        db.commit()
        _audit(db, txn, "STATE_CHANGED", old_status, txn.status, "PG_CALLBACK", req.dict())

        if txn.status in TERMINAL_STATES:
            _publish_outcome(txn)

        return {"acknowledged": True}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# GET /payment/status
# ---------------------------------------------------------------------------

@app.get("/payment/status")
def payment_status(txn_reference: str):
    db = SessionLocal()
    try:
        txn = db.query(models.Transaction).filter(models.Transaction.txn_reference == txn_reference).first()
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return _txn_response(txn)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /payment/refund
# ---------------------------------------------------------------------------

class RefundRequest(BaseModel):
    booking_id: int
    txn_reference: str
    refund_amount: float
    cancellation_charge: float = 0
    idempotency_key: str


@app.post("/payment/refund")
def initiate_refund(req: RefundRequest):
    db = SessionLocal()
    try:
        existing = db.query(models.Refund).filter(models.Refund.idempotency_key == req.idempotency_key).first()
        if existing:
            return _refund_response(existing)

        txn = db.query(models.Transaction).filter(models.Transaction.txn_reference == req.txn_reference).first()
        if not txn or txn.status != "SUCCESS":
            raise HTTPException(status_code=422, detail="Original transaction not found or not successful")

        refund = models.Refund(
            refund_reference=gen_refund_reference(req.booking_id),
            transaction_id=txn.transaction_id,
            booking_id=req.booking_id,
            idempotency_key=req.idempotency_key,
            refund_amount=req.refund_amount,
            cancellation_charge=req.cancellation_charge,
            status="PROCESSING",
        )
        db.add(refund)
        db.commit()
        db.refresh(refund)

        pg_result = pg_provider.refund(txn.pg_payment_id, req.refund_amount)
        refund.status = "COMPLETED" if pg_result["status"] == "COMPLETED" else "FAILED"
        db.commit()

        event_bus.publish(
            "RefundCompleted" if refund.status == "COMPLETED" else "RefundFailed",
            {
                "booking_id": req.booking_id,
                "refund_reference": refund.refund_reference,
                "refund_amount": req.refund_amount,
                "status": refund.status,
            },
        )
        return _refund_response(refund)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _txn_response(txn):
    return {
        "transactionId": txn.txn_reference,
        "status": txn.status,
        "bookingId": txn.booking_id,
        "amount": float(txn.amount),
        "pgOrderId": txn.pg_order_id,
        "pgPaymentId": txn.pg_payment_id,
        "updatedAt": txn.updated_at.isoformat() if txn.updated_at else None,
    }


def _refund_response(refund):
    return {
        "refundId": refund.refund_reference,
        "status": refund.status,
        "refundAmount": float(refund.refund_amount),
        "cancellationCharge": float(refund.cancellation_charge),
    }


def _audit(db, txn, event_type, old_status, new_status, source, payload):
    db.add(models.PaymentAuditLog(
        transaction_id=txn.transaction_id,
        booking_id=txn.booking_id,
        event_type=event_type,
        old_status=old_status,
        new_status=new_status,
        source=source,
        raw_payload=payload,
    ))
    db.commit()


def _publish_outcome(txn):
    topic = "PaymentSuccess" if txn.status == "SUCCESS" else "PaymentFailed"
    event_bus.publish(topic, {
        "booking_id": txn.booking_id,
        "txn_reference": txn.txn_reference,
        "amount": float(txn.amount),
        "status": txn.status,
    })


# ---------------------------------------------------------------------------
# Reconciliation job (design doc §5.3 Timeout Flow / §14 lost callback)
# ---------------------------------------------------------------------------

async def reconciliation_loop():
    while True:
        await asyncio.sleep(RECONCILIATION_POLL_INTERVAL_SECONDS)
        db = SessionLocal()
        try:
            threshold = datetime.utcnow() - timedelta(seconds=PAYMENT_TIMEOUT_THRESHOLD_SECONDS)
            stuck = db.query(models.Transaction).filter(
                models.Transaction.status == "PROCESSING",
                models.Transaction.updated_at < threshold,
            ).all()

            for txn in stuck:
                pg_reference = txn.pg_payment_id or txn.pg_order_id
                pg_status = pg_provider.get_status(pg_reference)
                age_seconds = (datetime.utcnow() - txn.created_at).total_seconds()

                if pg_status["status"] in TERMINAL_STATES:
                    old_status = txn.status
                    txn.status = pg_status["status"]
                    # Don't overwrite an existing id with None - only fill it
                    # in if the reconciliation job actually found one.
                    if pg_status.get("pg_payment_id"):
                        txn.pg_payment_id = pg_status["pg_payment_id"]
                    txn.version += 1
                    db.commit()
                    _audit(db, txn, "RECONCILED", old_status, txn.status, "RECONCILIATION_JOB", pg_status)
                    _publish_outcome(txn)
                    print(f"[reconciliation] {txn.txn_reference} resolved -> {txn.status}")

                elif age_seconds > PAYMENT_MAX_WAIT_SECONDS:
                    old_status = txn.status
                    txn.status = "FAILED"
                    txn.failure_reason = "MAX_WAIT_EXCEEDED"
                    txn.version += 1
                    db.commit()
                    _audit(db, txn, "RECONCILED_TIMEOUT", old_status, txn.status, "RECONCILIATION_JOB",
                           {"reason": "max_wait_exceeded"})
                    _publish_outcome(txn)
                    print(f"[reconciliation] {txn.txn_reference} timed out -> FAILED")
        finally:
            db.close()


@app.on_event("startup")
async def startup():
    asyncio.create_task(reconciliation_loop())