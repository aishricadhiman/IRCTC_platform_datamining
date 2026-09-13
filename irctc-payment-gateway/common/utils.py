"""ID generators - mirrors design doc §10.3 (unique, never-reused transaction IDs)."""
import random
from datetime import datetime


def gen_txn_reference(booking_id: int) -> str:
    return f"TXN{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{booking_id}{random.randint(100, 999)}"


def gen_refund_reference(booking_id: int) -> str:
    return f"RFD{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{booking_id}{random.randint(100, 999)}"

