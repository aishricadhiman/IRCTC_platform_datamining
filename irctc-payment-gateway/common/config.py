"""
Shared configuration for Payment Service.

In production this would be Kubernetes ConfigMaps/Secrets; here it's a
simple env-overridable constant so the service runs locally with zero
external infrastructure.
"""
import os

PAYMENT_SERVICE_PORT = 8003

# Reconciliation tuning. PAYMENT_TIMEOUT_THRESHOLD_SECONDS (how soon the
# reconciliation job starts checking a "stuck" transaction) is kept short so
# the mock-mode demo/tests resolve quickly. PAYMENT_MAX_WAIT_SECONDS (how
# long before giving up entirely and marking FAILED) is set to a realistic
# value for an actual human filling out a real checkout form - a real
# customer navigating menus, fixing a declined card, etc. can easily take
# more than a minute, and marking their payment FAILED while they're still
# mid-checkout is worse than waiting a bit longer.
PAYMENT_TIMEOUT_THRESHOLD_SECONDS = int(os.environ.get("PAYMENT_TIMEOUT_THRESHOLD_SECONDS", 20))
PAYMENT_MAX_WAIT_SECONDS = int(os.environ.get("PAYMENT_MAX_WAIT_SECONDS", 600))
RECONCILIATION_POLL_INTERVAL_SECONDS = int(os.environ.get("RECONCILIATION_POLL_INTERVAL_SECONDS", 5))

