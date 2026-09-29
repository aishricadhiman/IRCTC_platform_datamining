"""
Outbound calls to the other services, in one place.

Every cross-service call Booking Service makes goes through a function here,
so timeouts are consistent and every call site is unmistakably awaited - the
Phase 2 bug was a notification POST that was never awaited and therefore
never actually sent, which is easy to miss when calls are written inline.
"""

import os

import httpx


INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8003")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://localhost:8005")
NOTIFICATION_URL = os.getenv("NOTIFICATION_URL", "http://localhost:8006")

DEFAULT_TIMEOUT = 30.0
NOTIFICATION_TIMEOUT = 3.0


# --------------------------------------------------
# Inventory - per-seat API (Phase 6/7)
# --------------------------------------------------

async def allocate_seats(client, train_id, class_type, allocation_ref,
                         booking_id, passengers):
    """
    passengers: [{passenger_ref, age, gender, berth_preference}, ...]

    Returns Inventory's response: per-passenger CNF/RAC/WL outcomes. Safe to
    retry - Inventory keys idempotency on allocation_ref.
    """
    return await client.post(
        f"{INVENTORY_URL}/internal/inventory/{train_id}/allocate",
        json={
            "class_type": class_type,
            "allocation_ref": allocation_ref,
            "booking_id": booking_id,
            "passengers": passengers,
        },
        timeout=DEFAULT_TIMEOUT,
    )


async def cancel_seats(client, train_id, class_type, allocation_ref,
                       passenger_refs=None):
    """
    Cancels berths and triggers Inventory's RAC -> CNF promotion. The
    response also reports the RAC capacity now free, which this service uses
    to decide how many waitlisted passengers it can promote.
    """
    payload = {
        "class_type": class_type,
        "allocation_ref": allocation_ref,
    }

    if passenger_refs is not None:
        payload["passenger_refs"] = passenger_refs

    return await client.post(
        f"{INVENTORY_URL}/internal/inventory/{train_id}/cancel",
        json=payload,
        timeout=DEFAULT_TIMEOUT,
    )


# --------------------------------------------------
# Inventory - legacy counter API (Phases 1-5)
# --------------------------------------------------

async def reserve_legacy(client, train_id):
    return await client.post(
        f"{INVENTORY_URL}/reserve/{train_id}",
        timeout=DEFAULT_TIMEOUT,
    )


async def release_legacy(client, train_id):
    return await client.post(
        f"{INVENTORY_URL}/release/{train_id}",
        timeout=DEFAULT_TIMEOUT,
    )


# --------------------------------------------------
# Payment Gateway (Phase 4/5)
# --------------------------------------------------

async def initiate_payment(client, booking_id, user_id, amount,
                           payment_method, idempotency_key):
    return await client.post(
        f"{PAYMENT_URL}/payment/initiate",
        json={
            "booking_id": booking_id,
            "user_id": user_id,
            "amount": amount,
            "payment_method": payment_method,
            "idempotency_key": idempotency_key,
        },
        timeout=DEFAULT_TIMEOUT,
    )


# --------------------------------------------------
# Notification
# --------------------------------------------------

async def send_notification(client, user_id, booking_id, message,
                            notification_type="BOOKING_CONFIRMED"):
    """
    Best-effort by contract: callers are expected to swallow failures. A
    notification must never decide whether a paid booking is confirmed.
    """
    return await client.post(
        f"{NOTIFICATION_URL}/notifications",
        json={
            "user_id": user_id,
            "booking_id": booking_id,
            "message": message,
            "notification_type": notification_type,
        },
        timeout=NOTIFICATION_TIMEOUT,
    )
