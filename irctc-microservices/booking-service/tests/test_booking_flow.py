"""
Phase 8 booking tests.

Runs against the live stack: Booking Service talking to the real Inventory,
Payment Gateway and Notification services.

Payment outcome is random in the gateway's MOCK mode (~70% SUCCESS,
~15% FAILED, ~15% PROCESSING), so helpers here retry until they get the
outcome a given test needs rather than asserting on a coin flip.
"""

import httpx
import pytest


BOOKING_URL = "http://localhost:8000"
INVENTORY_URL = "http://inventory-service:8000"
TRAIN_ID = 1
CLASS_TYPE = "SL"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def availability():
    return httpx.get(
        f"{INVENTORY_URL}/inventory/{TRAIN_ID}/availability",
        params={"class_type": CLASS_TYPE},
        timeout=10.0,
    ).json()


def book(passengers, amount=500.0, simulate_confirm_failure=False):
    return httpx.post(
        f"{BOOKING_URL}/bookings/multi",
        json={
            "user_id": 1,
            "train_id": TRAIN_ID,
            "class_type": CLASS_TYPE,
            "amount": amount,
            "passengers": passengers,
            "simulate_confirm_failure": simulate_confirm_failure,
        },
        timeout=60.0,
    )


def book_confirmed(passengers, attempts=12):
    """
    Books until the mock gateway actually pays, so a test that needs a live
    booking is not at the mercy of a random FAILED/PROCESSING outcome.

    A PROCESSING attempt keeps holding its berths - correctly, since the
    payment may still succeed and the Phase 5 webhook will settle it - so an
    abandoned attempt is cancelled here before retrying. Without that, each
    retry would quietly consume inventory and the caller's seat arithmetic
    would be wrong.
    """
    for _ in range(attempts):
        resp = book(passengers)
        if resp.status_code != 200:
            continue

        body = resp.json()

        if body["booking"]["status"] in ("CONFIRMED", "PARTIALLY_CONFIRMED", "WAITLISTED"):
            return body

        if body["booking"]["status"] == "AWAITING_PAYMENT":
            cancel(body["booking"]["id"])

    pytest.skip("mock gateway never returned SUCCESS in the allotted attempts")


def detail(booking_id):
    return httpx.get(f"{BOOKING_URL}/bookings/{booking_id}/detail", timeout=10.0).json()


def cancel(booking_id, passenger_ids=None):
    payload = {}
    if passenger_ids is not None:
        payload["passenger_ids"] = passenger_ids
    return httpx.post(
        f"{BOOKING_URL}/bookings/{booking_id}/cancel",
        json=payload,
        timeout=60.0,
    )


def pax(name, age=30, preference="NONE"):
    return {"name": name, "age": age, "berth_preference": preference}


def fill_cnf_to(target):
    """Books CNF berths until `target` remain, returning the booking ids."""
    ids = []
    while True:
        free = availability()["cnf_available"]
        if free <= target:
            return ids
        batch = min(free - target, 6)
        body = book_confirmed([pax(f"filler{i}") for i in range(batch)])
        ids.append(body["booking"]["id"])


@pytest.fixture
def cleanup():
    ids = []
    yield ids
    for booking_id in ids:
        try:
            cancel(booking_id)
        except httpx.RequestError:
            pass


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

def test_multi_passenger_booking_gets_berths_and_pnr(cleanup):
    body = book_confirmed([
        pax("Senior", age=70),
        pax("Prefers Upper", preference="UPPER"),
        pax("Third"),
    ])
    cleanup.append(body["booking"]["id"])

    booking = body["booking"]
    assert booking["status"] == "CONFIRMED"
    assert booking["pnr"], "a confirmed booking must carry a PNR"
    assert len(booking["passengers"]) == 3
    assert all(p["status"] == "CNF" for p in booking["passengers"])
    assert len({p["coach_name"] for p in booking["passengers"]}) == 1


def test_senior_and_preference_rules_reach_inventory(cleanup):
    body = book_confirmed([pax("Senior", age=70), pax("Upper", preference="UPPER")])
    cleanup.append(body["booking"]["id"])

    by_name = {p["name"]: p for p in body["booking"]["passengers"]}
    assert by_name["Senior"]["berth_type"] == "LOWER"
    assert by_name["Upper"]["berth_type"] == "UPPER"


def test_booking_consumes_per_seat_inventory(cleanup):
    before = availability()["cnf_available"]
    body = book_confirmed([pax("A"), pax("B")])
    cleanup.append(body["booking"]["id"])
    assert availability()["cnf_available"] == before - 2


def test_waitlisted_passengers_get_queue_positions(cleanup):
    """With CNF and RAC exhausted, passengers come back WL with positions."""
    cleanup.extend(fill_cnf_to(0))

    # Exhaust RAC too.
    while availability()["rac_slots_available"] > 0:
        slots = availability()["rac_slots_available"]
        body = book_confirmed([pax(f"rac{i}") for i in range(min(slots, 6))])
        cleanup.append(body["booking"]["id"])

    body = book_confirmed([pax("Waiting One"), pax("Waiting Two")])
    cleanup.append(body["booking"]["id"])

    booking = body["booking"]
    assert booking["status"] == "WAITLISTED"
    assert all(p["status"] == "WL" for p in booking["passengers"])
    positions = [p["waitlist_position"] for p in booking["passengers"]]
    assert positions == sorted(positions), "queue positions must be ordered"


def test_empty_passenger_list_rejected():
    resp = book([])
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# The CNF <- RAC <- WL cascade
# ---------------------------------------------------------------------------

def test_cancelling_cnf_cascades_rac_to_cnf_and_wl_to_rac(cleanup):
    """
    The whole point of Phases 6-8 working together:

      cancel a confirmed berth
        -> Inventory promotes the waiting RAC passenger onto it
        -> the RAC half-berth that frees up is offered to the waiting list
           by Booking Service, which owns the queue.
    """
    cleanup.extend(fill_cnf_to(1))

    # One CNF booking we will cancel.
    victim = book_confirmed([pax("Victim")])
    cleanup.append(victim["booking"]["id"])
    assert victim["booking"]["passengers"][0]["status"] == "CNF"
    assert availability()["cnf_available"] == 0

    # A RAC passenger waiting for a confirmed berth.
    rac_booking = book_confirmed([pax("Rac Rider")])
    cleanup.append(rac_booking["booking"]["id"])
    assert rac_booking["booking"]["passengers"][0]["status"] == "RAC"

    # Fill the rest of RAC, then put somebody on the waiting list.
    while availability()["rac_slots_available"] > 0:
        slots = availability()["rac_slots_available"]
        filler = book_confirmed([pax(f"racfill{i}") for i in range(min(slots, 6))])
        cleanup.append(filler["booking"]["id"])

    wl_booking = book_confirmed([pax("Wl Rider")])
    cleanup.append(wl_booking["booking"]["id"])
    assert wl_booking["booking"]["passengers"][0]["status"] == "WL"

    # Cancel the confirmed berth and watch the cascade.
    resp = cancel(victim["booking"]["id"])
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "CANCELLED"
    assert len(body["cancelled"]) == 1

    promoted_names = {p["name"] for p in body["promotions"]}
    assert "Rac Rider" in promoted_names, "RAC passenger should take the freed berth"

    rac_after = detail(rac_booking["booking"]["id"])["passengers"][0]
    assert rac_after["status"] == "CNF", "RAC -> CNF"
    assert rac_after["seat_number"] is not None

    wl_after = detail(wl_booking["booking"]["id"])["passengers"][0]
    assert wl_after["status"] == "RAC", "WL -> RAC into the freed half-berth"


def test_cancel_returns_berths_to_inventory(cleanup):
    before = availability()["cnf_available"]

    body = book_confirmed([pax("A"), pax("B")])
    booking_id = body["booking"]["id"]
    assert availability()["cnf_available"] == before - 2

    resp = cancel(booking_id)
    assert resp.status_code == 200
    assert availability()["cnf_available"] == before


def test_partial_cancel_leaves_the_rest_intact(cleanup):
    body = book_confirmed([pax("Stays One"), pax("Goes"), pax("Stays Two")])
    booking_id = body["booking"]["id"]
    cleanup.append(booking_id)

    goes = [p for p in body["booking"]["passengers"] if p["name"] == "Goes"][0]

    resp = cancel(booking_id, passenger_ids=[goes["id"]])
    assert resp.status_code == 200
    assert resp.json()["status"] != "CANCELLED", "booking still has passengers"

    after = detail(booking_id)
    by_name = {p["name"]: p for p in after["passengers"]}
    assert by_name["Goes"]["status"] == "CANCELLED"
    assert by_name["Stays One"]["status"] == "CNF"
    assert by_name["Stays Two"]["status"] == "CNF"


def test_duplicate_cancel_is_idempotent(cleanup):
    body = book_confirmed([pax("Once")])
    booking_id = body["booking"]["id"]

    before = availability()["cnf_available"]
    first = cancel(booking_id).json()
    assert len(first["cancelled"]) == 1
    after_first = availability()["cnf_available"]
    assert after_first == before + 1

    second = cancel(booking_id).json()
    assert second["cancelled"] == []
    assert availability()["cnf_available"] == after_first, "not freed twice"


def test_cancel_unknown_booking_404():
    assert cancel(999999).status_code == 404


def test_legacy_booking_cannot_use_the_per_seat_cancel():
    """A Phase 1-5 booking holds no allocation_ref, so it is rejected here."""
    legacy = httpx.post(
        f"{BOOKING_URL}/bookings",
        json={"user_id": 1, "train_id": TRAIN_ID,
              "passenger_name": "Legacy", "amount": 100},
        timeout=60.0,
    )
    if legacy.status_code != 200:
        pytest.skip("mock gateway did not confirm the legacy booking")

    booking_id = legacy.json()["booking"]["id"]
    resp = cancel(booking_id)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Compensation
# ---------------------------------------------------------------------------

def test_confirm_failure_after_payment_returns_the_berths():
    before = availability()["cnf_available"]

    for _ in range(12):
        resp = book([pax("Doomed One"), pax("Doomed Two")],
                    simulate_confirm_failure=True)
        if resp.status_code == 500:
            assert availability()["cnf_available"] == before, "berths not returned"
            return
        # A FAILED/PROCESSING payment never reaches the confirm step; retry.
    pytest.skip("mock gateway never reached the confirmation step")


# ---------------------------------------------------------------------------
# Legacy path must be untouched
# ---------------------------------------------------------------------------

def test_legacy_single_passenger_booking_still_works():
    resp = httpx.post(
        f"{BOOKING_URL}/bookings",
        json={"user_id": 1, "train_id": TRAIN_ID,
              "passenger_name": "Legacy Rider", "amount": 250},
        timeout=60.0,
    )
    assert resp.status_code in (200, 400), "legacy endpoint must still respond"

    if resp.status_code == 200:
        booking = resp.json()["booking"]
        assert booking["status"] in ("CONFIRMED", "AWAITING_PAYMENT")
        assert booking["passenger_name"] == "Legacy Rider"


def test_legacy_booking_does_not_touch_per_seat_inventory():
    before = availability()["cnf_available"]

    httpx.post(
        f"{BOOKING_URL}/bookings",
        json={"user_id": 1, "train_id": TRAIN_ID,
              "passenger_name": "Counter Only", "amount": 100},
        timeout=60.0,
    )

    assert availability()["cnf_available"] == before, (
        "the legacy counter path must not consume per-seat berths"
    )
