IRCTC Payment Gateway — Payment Service Prototype (Python / FastAPI)

A standalone payment processing service that demonstrates payment lifecycle management, idempotency, gateway integration, refunds, audit logging, and transaction reconciliation.

The service can run entirely on a local machine using SQLite and supports both a mock payment gateway and Razorpay Test Mode.

Features
Payment transaction management
Payment state machine
Idempotency protection
Payment audit logging
Refund processing
Mock payment gateway for testing
Razorpay Test Mode integration
Background reconciliation for stuck transactions
REST APIs with Swagger documentation
Payment Lifecycle
INITIATED
    ↓
PROCESSING
    ↓
SUCCESS | FAILED

REFUNDED (only after SUCCESS)

The service maintains transaction state transitions and records all changes in an audit log.

Project Structure
irctc-payment-gateway-code/

├── .env.example
├── .gitignore

├── common/
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── event_bus.py
│   └── utils.py

├── payment_service/
│   ├── main.py
│   ├── pg_provider_mock.py
│   └── pg_provider_razorpay.py

├── scripts/
│   └── run_payment_only.py

├── tests/
│   └── test_payment_service.py

└── requirements.txt
Payment Gateway Modes
Mock Mode (Default)

No setup required.

Features:

Simulated payment processing
SUCCESS / FAILED / PENDING outcomes
Deterministic testing via force_outcome
No external dependencies
Razorpay Test Mode

Uses Razorpay's official Test Environment.

Features:

Real checkout experience
Official test cards and UPI IDs
HMAC-SHA256 signature verification
No real money involved

Activated automatically when:

RAZORPAY_KEY_ID=...
RAZORPAY_KEY_SECRET=...

are configured.

Running the Service

Install dependencies:

pip install -r requirements.txt

Start the server:

python scripts/run_payment_only.py

Swagger UI:

http://127.0.0.1:8003/docs
Running with Razorpay Test Mode
1. Create a Razorpay Test Account

Generate Test API Keys from:

Dashboard → Settings → API Keys
2. Configure Environment Variables

Create a .env file:

RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx
3. Start the Service
python scripts/run_payment_only.py

The startup log should show:

[payment-service] Using REAL Razorpay Test Mode provider.
Creating a Payment
POST /payment/initiate
{
  "booking_id": 5001,
  "user_id": 1,
  "amount": 1500,
  "payment_method": "UPI",
  "idempotency_key": "unique-key"
}

Response:

{
  "transactionId": "...",
  "status": "PROCESSING",
  "checkoutPageUrl": "..."
}

Open checkoutPageUrl in a browser to complete payment.

Razorpay Test Credentials
Outcome	Card Number	UPI ID
Success	4111 1111 1111 1111	success@razorpay
Failure	4000 0000 0000 0002	failure@razorpay

Any future expiry date and any CVV may be used.

Transaction Status

Check payment status:

GET /payment/status?txn_reference=<txn_id>

Possible states:

INITIATED
PROCESSING
SUCCESS
FAILED
Refunds
POST /payment/refund

Processes refunds only for successful transactions.

{
  "booking_id": 5001,
  "txn_reference": "...",
  "refund_amount": 1000,
  "cancellation_charge": 200,
  "idempotency_key": "refund-key"
}
Automated Reconciliation

A background reconciliation job continuously checks transactions stuck in the PROCESSING state.

Responsibilities:

Recover from lost callbacks
Synchronize with gateway status
Resolve long-running transactions
Mark expired transactions as FAILED

This prevents transactions from remaining in an indeterminate state indefinitely.

Test Suite

Run:

pytest tests/test_payment_service.py -v

The test suite covers:

Successful payments
Failed payments
Idempotency protection
Refund processing
Reconciliation of pending transactions
Production Considerations

Before deploying to production:

Replace SQLite with PostgreSQL
Add authentication and authorization
Implement webhook signature validation
Add monitoring and observability
Add structured logging and metrics
Configure secure secret management
Use production payment gateway credentials
Deploy behind a reverse proxy/load balancer