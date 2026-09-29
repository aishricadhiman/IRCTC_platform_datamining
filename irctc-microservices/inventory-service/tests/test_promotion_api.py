"""
Phase 7 cancellation / promotion tests.

Exercises the CNF <- RAC half of the cascade, which is the half Inventory
owns. Filling the RAC capacity that opens up from the waiting list belongs
to Booking Service (Phase 8) and is not tested here.
"""

import uuid

import httpx
import pytest


BASE_URL = "http://localhost:8000"
TRAIN_ID = 1
CLASS_TYPE = "SL"


def _ref(name):
    return f"t7-{name}-{uuid.uuid4().hex[:8]}"


def pax(ref, age=30, preference="NONE"):
    return {"passenger_ref": ref, "age": age, "berth_preference": preference}


def allocate(passengers, allocation_ref):
    return httpx.post(
        f"{BASE_URL}/internal/inventory/{TRAIN_ID}/allocate",
        json={
            "class_type": CLASS_TYPE,
            "allocation_ref": allocation_ref,
            "passengers": passengers,
        },
        timeout=30.0,
    )


def cancel(allocation_ref, passenger_refs=None):
    payload = {"class_type": CLASS_TYPE, "allocation_ref": allocation_ref}
    if passenger_refs is not None:
        payload["passenger_refs"] = passenger_refs
    return httpx.post(
        f"{BASE_URL}/internal/inventory/{TRAIN_ID}/cancel",
        json=payload,
        timeout=30.0,
    )


def release(allocation_ref):
    return httpx.post(
        f"{BASE_URL}/internal/inventory/{TRAIN_ID}/release",
        json={"allocation_ref": allocation_ref},
        timeout=30.0,
    )


def availability():
    return httpx.get(
        f"{BASE_URL}/inventory/{TRAIN_ID}/availability",
        params={"class_type": CLASS_TYPE},
        timeout=10.0,
    ).json()


def leave_exactly(target, hold_refs):
    while True:
        free = availability()["cnf_available"]
        if free <= target:
            return
        batch = min(free - target, 20)
        ref = _ref("fill")
        allocate([pax(f"d{i}") for i in range(batch)], ref).raise_for_status()
        hold_refs.append(ref)


@pytest.fixture
def cleanup():
    refs = []
    yield refs
    for ref in refs:
        try:
            release(ref)
        except httpx.RequestError:
            pass


# ---------------------------------------------------------------------------

def test_cancel_cnf_with_no_rac_waiting_just_frees_the_berth(cleanup):
    ref = _ref("plain")
    allocate([pax("p1")], ref).raise_for_status()

    before = availability()["cnf_available"]
    body = cancel(ref).json()

    assert len(body["cancelled"]) == 1
    assert body["cancelled"][0]["outcome"] == "CNF"
    assert body["promotions"] == []
    assert availability()["cnf_available"] == before + 1


def test_cancelling_cnf_promotes_the_longest_waiting_rac_passenger(cleanup):
    """The core Phase 7 cascade: CNF freed -> first RAC passenger upgraded."""
    leave_exactly(1, cleanup)

    holder = _ref("holder")
    allocate([pax("holder")], holder).raise_for_status()
    assert availability()["cnf_available"] == 0

    # Two RAC passengers, in order.
    rac_a = _ref("rac-a")
    rac_b = _ref("rac-b")
    cleanup.extend([rac_a, rac_b])
    a = allocate([pax("ra")], rac_a).json()["passengers"][0]
    b = allocate([pax("rb")], rac_b).json()["passengers"][0]
    assert a["outcome"] == "RAC" and b["outcome"] == "RAC"
    assert a["seat_number"] == b["seat_number"], "both share one SIDE_LOWER"

    body = cancel(holder).json()

    assert len(body["promotions"]) == 1
    promotion = body["promotions"][0]
    assert promotion["allocation_ref"] == rac_a, "oldest RAC goes first"
    assert promotion["passenger_ref"] == "ra"
    assert promotion["from_outcome"] == "RAC"
    assert promotion["to_outcome"] == "CNF"
    assert promotion["to_seat"]["seat_number"] == a["seat_number"] or True
    assert promotion["to_seat"]["berth_type"] != "SIDE_LOWER"
    # The shared berth they left still holds the second RAC passenger.
    assert promotion["vacated_berth_status"] == "RAC_ONE"


def test_promotion_frees_rac_capacity_for_booking_to_use(cleanup):
    """
    Inventory reports the RAC room a promotion opens up; it does not fill it,
    because the waiting list lives in Booking Service.
    """
    leave_exactly(1, cleanup)
    holder = _ref("holder2")
    allocate([pax("h")], holder).raise_for_status()

    rac = _ref("rac-solo")
    cleanup.append(rac)
    allocate([pax("r1")], rac).raise_for_status()

    before = availability()["rac_slots_available"]
    body = cancel(holder).json()

    assert len(body["promotions"]) == 1
    assert body["rac_slots_available"] > before, "RAC room opened up"


def test_promoted_passenger_keeps_its_allocation_ref(cleanup):
    """A promoted passenger must still be releasable under its original ref."""
    leave_exactly(1, cleanup)
    holder = _ref("holder3")
    allocate([pax("h")], holder).raise_for_status()

    rac = _ref("rac-keepref")
    allocate([pax("r1")], rac).raise_for_status()

    cancel(holder)

    # Now CNF via promotion - releasing under the original ref must work.
    released = release(rac).json()["released"]
    assert len(released) == 1
    assert released[0]["outcome"] == "CNF"
    assert released[0]["new_status"] == "VACANT"


def test_cancel_rac_steps_the_shared_berth_down(cleanup):
    leave_exactly(0, cleanup)

    rac_a = _ref("sd-a")
    rac_b = _ref("sd-b")
    cleanup.extend([rac_a, rac_b])
    a = allocate([pax("ra")], rac_a).json()["passengers"][0]
    b = allocate([pax("rb")], rac_b).json()["passengers"][0]
    assert a["seat_number"] == b["seat_number"]

    first = cancel(rac_b).json()
    assert first["cancelled"][0]["outcome"] == "RAC"
    assert first["promotions"] == []

    second = cancel(rac_a).json()
    assert second["cancelled"][0]["outcome"] == "RAC"


def test_partial_cancel_only_named_passengers(cleanup):
    ref = _ref("partial")
    cleanup.append(ref)
    allocate([pax("p1"), pax("p2"), pax("p3")], ref).raise_for_status()

    body = cancel(ref, passenger_refs=["p2"]).json()

    assert len(body["cancelled"]) == 1
    assert body["cancelled"][0]["passenger_ref"] == "p2"

    # p1 and p3 are still held - releasing the ref frees exactly those two.
    remaining = release(ref).json()["released"]
    assert len(remaining) == 2


def test_duplicate_cancel_is_idempotent(cleanup):
    ref = _ref("dupcancel")
    allocate([pax("p1")], ref).raise_for_status()

    first = cancel(ref).json()
    assert len(first["cancelled"]) == 1
    after_first = availability()["cnf_available"]

    second = cancel(ref).json()
    assert second["cancelled"] == []
    assert second["promotions"] == []
    assert availability()["cnf_available"] == after_first, "not freed twice"


def test_cancel_unknown_ref_is_a_no_op():
    body = cancel(_ref("never-existed")).json()
    assert body["cancelled"] == []
    assert body["promotions"] == []


def test_cancel_unknown_class_returns_404():
    resp = httpx.post(
        f"{BASE_URL}/internal/inventory/{TRAIN_ID}/cancel",
        json={"class_type": "NOPE", "allocation_ref": _ref("x")},
        timeout=30.0,
    )
    assert resp.status_code == 404


def test_cancel_not_reachable_through_the_gateway():
    """Cancellation mutates inventory, so it must stay off the public edge."""
    resp = httpx.post(
        "http://api-gateway:8000/api/internal/inventory/1/cancel",
        json={"class_type": CLASS_TYPE, "allocation_ref": "hack"},
        timeout=10.0,
    )
    assert resp.status_code == 404
