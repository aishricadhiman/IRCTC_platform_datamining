"""
Phase 6 Inventory Service test suite.

Runs against a live Inventory Service over HTTP (the same style as
irctc-payment-gateway/tests/test_payment_service.py), so it exercises the
real Redis locks, real PostgreSQL transactions and the real seeded data
rather than an in-process stub.

Run from inside the container:
    docker compose exec inventory-service pytest tests/ -v

Each test uses a unique allocation_ref and releases whatever it allocated,
so the suite can be re-run against the same database repeatedly.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest


BASE_URL = "http://localhost:8000"
TRAIN_ID = 1
CLASS_TYPE = "SL"

CNF_INELIGIBLE = "SIDE_LOWER"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ref(name):
    return f"test-{name}-{uuid.uuid4().hex[:8]}"


def allocate(passengers, allocation_ref=None, booking_id=None, simulate_failure=False):
    payload = {
        "class_type": CLASS_TYPE,
        "allocation_ref": allocation_ref or _ref("alloc"),
        "booking_id": booking_id,
        "passengers": passengers,
        "simulate_failure": simulate_failure,
    }
    return httpx.post(
        f"{BASE_URL}/internal/inventory/{TRAIN_ID}/allocate",
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
    resp = httpx.get(
        f"{BASE_URL}/inventory/{TRAIN_ID}/availability",
        params={"class_type": CLASS_TYPE},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def pax(ref, age=30, preference="NONE"):
    return {"passenger_ref": ref, "age": age, "berth_preference": preference}


def leave_exactly(target, hold_refs):
    """
    Allocates CNF berths until exactly `target` remain free, registering each
    allocation for cleanup. Note the seats must be consumed by ALLOCATING
    them - once cnf_available hits 0, any further allocation falls through to
    RAC, so "allocate then release" cannot be used to free CNF stock.
    """
    while True:
        free = availability()["cnf_available"]
        if free <= target:
            return
        batch = min(free - target, 20)
        ref = _ref("fill")
        allocate([pax(f"d{i}") for i in range(batch)], allocation_ref=ref).raise_for_status()
        hold_refs.append(ref)


def drain_cnf(hold_refs):
    """Allocates every remaining CNF berth so the next allocation hits RAC."""
    leave_exactly(0, hold_refs)


def drain_rac(hold_refs):
    """Fills every RAC slot. CNF must already be exhausted."""
    while True:
        slots = availability()["rac_slots_available"]
        if slots == 0:
            return
        batch = min(slots, 20)
        ref = _ref("racfill")
        allocate([pax(f"r{i}") for i in range(batch)], allocation_ref=ref).raise_for_status()
        hold_refs.append(ref)


@pytest.fixture(scope="session", autouse=True)
def ensure_service_running():
    try:
        httpx.get(f"{BASE_URL}/docs", timeout=5)
    except httpx.RequestError:
        pytest.fail(
            f"Inventory Service is not reachable at {BASE_URL}.",
            pytrace=False,
        )


@pytest.fixture
def cleanup():
    """Releases every allocation_ref a test registered, even if it failed."""
    refs = []
    yield refs
    for ref in refs:
        try:
            release(ref)
        except httpx.RequestError:
            pass


# ---------------------------------------------------------------------------
# Seeded state
# ---------------------------------------------------------------------------

def test_seeded_state_reconciles_with_legacy_counter():
    """72 physical seats, 27 BLOCKED historical, leaving 45 allocatable."""
    data = availability()
    assert data["total_seats"] == 72
    assert data["blocked_seats"] == 27

    legacy = httpx.get(f"{BASE_URL}/availability/{TRAIN_ID}", timeout=10.0).json()
    assert legacy["total_seats"] == 72

    # 45 allocatable = 40 CNF-eligible + 5 SIDE_LOWER RAC berths
    seats = httpx.get(
        f"{BASE_URL}/inventory/{TRAIN_ID}/seats",
        params={"class_type": CLASS_TYPE},
        timeout=10.0,
    ).json()
    all_seats = seats["coaches"][0]["seats"]
    assert len(all_seats) == 72
    allocatable = [s for s in all_seats if s["status"] != "BLOCKED"]
    assert len(allocatable) == 45


def test_blocked_seats_are_never_allocated(cleanup):
    ref = _ref("blocked")
    cleanup.append(ref)
    resp = allocate([pax("p1")], allocation_ref=ref)
    assert resp.status_code == 200
    seat_number = resp.json()["passengers"][0]["seat_number"]
    # BLOCKED seats are 46-72; an allocation must never land there.
    assert seat_number <= 45


# ---------------------------------------------------------------------------
# CNF allocation
# ---------------------------------------------------------------------------

def test_single_cnf_allocation(cleanup):
    ref = _ref("single")
    cleanup.append(ref)
    resp = allocate([pax("p1")], allocation_ref=ref)
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "CONFIRMED"
    assert len(body["passengers"]) == 1
    p = body["passengers"][0]
    assert p["outcome"] == "CNF"
    assert p["coach_name"] == "S1"
    assert p["berth_type"] != CNF_INELIGIBLE


def test_berth_preference_honoured(cleanup):
    ref = _ref("pref")
    cleanup.append(ref)
    resp = allocate([pax("p1", preference="UPPER")], allocation_ref=ref)
    assert resp.status_code == 200
    assert resp.json()["passengers"][0]["berth_type"] == "UPPER"


def test_senior_citizen_gets_lower(cleanup):
    ref = _ref("senior")
    cleanup.append(ref)
    resp = allocate([pax("p1", age=65, preference="NONE")], allocation_ref=ref)
    assert resp.status_code == 200
    assert resp.json()["passengers"][0]["berth_type"] == "LOWER"


def test_preference_fallback_still_returns_cnf(cleanup):
    """
    With every LOWER berth taken, a LOWER request must still be CNF on some
    other berth - preference is best-effort and never demotes to RAC while
    CNF stock remains.
    """
    seats = httpx.get(
        f"{BASE_URL}/inventory/{TRAIN_ID}/seats",
        params={"class_type": CLASS_TYPE},
        timeout=10.0,
    ).json()["coaches"][0]["seats"]
    free_lowers = [
        s for s in seats if s["berth_type"] == "LOWER" and s["status"] == "VACANT"
    ]

    hog = _ref("hog-lowers")
    cleanup.append(hog)
    resp = allocate(
        [pax(f"l{i}", preference="LOWER") for i in range(len(free_lowers))],
        allocation_ref=hog,
    )
    assert resp.status_code == 200

    ref = _ref("fallback")
    cleanup.append(ref)
    resp = allocate([pax("p1", preference="LOWER")], allocation_ref=ref)
    assert resp.status_code == 200
    p = resp.json()["passengers"][0]
    assert p["outcome"] == "CNF"
    assert p["berth_type"] != "LOWER"


def test_group_allocation_same_coach(cleanup):
    ref = _ref("group")
    cleanup.append(ref)
    resp = allocate([pax(f"p{i}") for i in range(4)], allocation_ref=ref)
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "CONFIRMED"
    assert len(body["passengers"]) == 4
    assert all(p["outcome"] == "CNF" for p in body["passengers"])
    assert len({p["coach_name"] for p in body["passengers"]}) == 1


# ---------------------------------------------------------------------------
# RAC and WL
# ---------------------------------------------------------------------------

def test_rac_one_then_rac_full_then_wl(cleanup):
    """
    Drains CNF, then walks the RAC pool: the first RAC passenger opens a
    SIDE_LOWER berth (RAC_ONE), the second shares it (RAC_FULL), and once
    every RAC slot is gone the next passenger is waitlisted.
    """
    drain_cnf(cleanup)
    assert availability()["cnf_available"] == 0

    slots = availability()["rac_slots_available"]
    assert slots > 0

    # First two RAC passengers must land on the SAME SIDE_LOWER berth.
    ref_a = _ref("rac-a")
    cleanup.append(ref_a)
    a = allocate([pax("r1")], allocation_ref=ref_a).json()["passengers"][0]
    assert a["outcome"] == "RAC"
    assert a["berth_type"] == CNF_INELIGIBLE

    ref_b = _ref("rac-b")
    cleanup.append(ref_b)
    b = allocate([pax("r2")], allocation_ref=ref_b).json()["passengers"][0]
    assert b["outcome"] == "RAC"
    assert b["seat_number"] == a["seat_number"], "second RAC shares the berth"

    # Exhaust whatever RAC capacity is left, then the next one is WL.
    remaining = availability()["rac_slots_available"]
    if remaining:
        ref_rest = _ref("rac-rest")
        cleanup.append(ref_rest)
        rest = allocate(
            [pax(f"r{i}") for i in range(remaining)], allocation_ref=ref_rest
        ).json()
        assert all(p["outcome"] == "RAC" for p in rest["passengers"])

    assert availability()["rac_slots_available"] == 0

    ref_wl = _ref("wl")
    cleanup.append(ref_wl)
    wl = allocate([pax("w1")], allocation_ref=ref_wl).json()
    assert wl["overall"] == "WAITLISTED"
    assert wl["passengers"][0]["outcome"] == "WL"
    assert wl["passengers"][0]["seat_number"] is None


def test_mixed_group_outcome(cleanup):
    """
    A group larger than the remaining stock comes back mixed. Set up so that
    exactly 2 CNF berths remain and RAC is fully exhausted, then ask for 4:
    the result must be 2 CNF + 2 WL, rolling up to PARTIALLY_CONFIRMED.
    """
    # Leave 2 CNF free, then park those last 2 in their own ref.
    leave_exactly(2, cleanup)
    parked = _ref("parked-two")
    allocate([pax("t1"), pax("t2")], allocation_ref=parked).raise_for_status()
    assert availability()["cnf_available"] == 0

    # With CNF at zero, fill every RAC slot.
    drain_rac(cleanup)
    assert availability()["rac_slots_available"] == 0

    # Hand the 2 CNF berths back.
    release(parked)
    assert availability()["cnf_available"] == 2

    ref = _ref("mixed")
    cleanup.append(ref)
    body = allocate([pax(f"m{i}") for i in range(4)], allocation_ref=ref).json()

    outcomes = [p["outcome"] for p in body["passengers"]]
    assert outcomes == ["CNF", "CNF", "WL", "WL"]
    assert body["overall"] == "PARTIALLY_CONFIRMED"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_duplicate_allocation_ref_is_idempotent(cleanup):
    ref = _ref("idem")
    cleanup.append(ref)

    before = availability()["cnf_available"]

    first = allocate([pax("p1"), pax("p2")], allocation_ref=ref).json()
    after_first = availability()["cnf_available"]

    second = allocate([pax("p1"), pax("p2")], allocation_ref=ref).json()
    after_second = availability()["cnf_available"]

    assert first["passengers"] == second["passengers"], "same seats returned"
    assert second["replayed"] is True
    assert after_first == before - 2
    assert after_second == after_first, "replay must not consume more seats"


def test_release_and_duplicate_release(cleanup):
    ref = _ref("rel")

    before = availability()["cnf_available"]
    allocate([pax("p1")], allocation_ref=ref).raise_for_status()
    assert availability()["cnf_available"] == before - 1

    first = release(ref)
    assert first.status_code == 200
    assert len(first.json()["released"]) == 1
    assert availability()["cnf_available"] == before

    second = release(ref)
    assert second.status_code == 200
    assert second.json()["released"] == [], "duplicate release is a no-op"
    assert availability()["cnf_available"] == before, "not released twice"


def test_rac_release_steps_down_through_rac_one(cleanup):
    drain_cnf(cleanup)

    ref_a = _ref("racrel-a")
    ref_b = _ref("racrel-b")
    cleanup.extend([ref_a, ref_b])

    a = allocate([pax("r1")], allocation_ref=ref_a).json()["passengers"][0]
    b = allocate([pax("r2")], allocation_ref=ref_b).json()["passengers"][0]
    assert a["seat_number"] == b["seat_number"]

    # Releasing one half leaves the other passenger on the berth.
    released = release(ref_b).json()["released"]
    assert released[0]["new_status"] == "RAC_ONE"

    released = release(ref_a).json()["released"]
    assert released[0]["new_status"] == "VACANT"


# ---------------------------------------------------------------------------
# Concurrency and failure handling
# ---------------------------------------------------------------------------

def test_concurrent_allocation_does_not_oversell(cleanup):
    """
    Drains CNF down to exactly 5 berths, then fires 20 concurrent single-seat
    allocations. Exactly 5 may come back CNF; the rest must fall to RAC/WL.
    """
    leave_exactly(5, cleanup)
    assert availability()["cnf_available"] == 5

    refs = [_ref(f"conc{i}") for i in range(20)]
    cleanup.extend(refs)

    def fire(ref):
        return allocate([pax("c1")], allocation_ref=ref).json()

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(fire, refs))

    cnf = [r for r in results if r["passengers"][0]["outcome"] == "CNF"]
    seats = [r["passengers"][0]["seat_number"] for r in cnf]

    assert len(cnf) == 5, f"expected exactly 5 CNF, got {len(cnf)}"
    assert len(set(seats)) == 5, "the same berth was sold twice"
    assert availability()["cnf_available"] == 0


def test_transaction_rolls_back_on_failure(cleanup):
    """A failure after seats are modified must leave no trace."""
    before = availability()
    ref = _ref("rollback")

    resp = allocate([pax("p1"), pax("p2")], allocation_ref=ref, simulate_failure=True)
    assert resp.status_code == 500

    after = availability()
    assert after["cnf_available"] == before["cnf_available"], "seats were not rolled back"

    # Nothing was persisted, so the ref is still unused and usable.
    cleanup.append(ref)
    retry = allocate([pax("p1"), pax("p2")], allocation_ref=ref)
    assert retry.status_code == 200
    assert retry.json()["replayed"] is False


def test_unknown_class_returns_404(cleanup):
    sanity_ref = _ref("sanity")
    cleanup.append(sanity_ref)
    resp = allocate([pax("p1")], allocation_ref=sanity_ref)
    assert resp.status_code == 200  # sanity: SL exists

    resp = httpx.post(
        f"{BASE_URL}/internal/inventory/{TRAIN_ID}/allocate",
        json={
            "class_type": "DOES_NOT_EXIST",
            "allocation_ref": _ref("badclass"),
            "passengers": [pax("p1")],
        },
        timeout=30.0,
    )
    assert resp.status_code == 404


def test_empty_passenger_list_rejected():
    resp = httpx.post(
        f"{BASE_URL}/internal/inventory/{TRAIN_ID}/allocate",
        json={
            "class_type": CLASS_TYPE,
            "allocation_ref": _ref("empty"),
            "passengers": [],
        },
        timeout=30.0,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Legacy counter API must be untouched by all of the above
# ---------------------------------------------------------------------------

def test_legacy_counter_endpoints_still_work():
    before = httpx.get(f"{BASE_URL}/availability/{TRAIN_ID}", timeout=10.0).json()

    reserved = httpx.post(f"{BASE_URL}/reserve/{TRAIN_ID}", timeout=10.0).json()
    assert reserved["reserved"] is True
    assert reserved["available_seats"] == before["available_seats"] - 1

    released = httpx.post(f"{BASE_URL}/release/{TRAIN_ID}", timeout=10.0).json()
    assert released["available_seats"] == before["available_seats"]


def test_legacy_counter_is_independent_of_seat_model(cleanup):
    """
    The two models are deliberately parallel until the Phase 8 cutover:
    allocating a berth must not move the legacy counter, and vice versa.
    """
    legacy_before = httpx.get(
        f"{BASE_URL}/availability/{TRAIN_ID}", timeout=10.0
    ).json()["available_seats"]

    ref = _ref("independence")
    cleanup.append(ref)
    allocate([pax("p1")], allocation_ref=ref).raise_for_status()

    legacy_after = httpx.get(
        f"{BASE_URL}/availability/{TRAIN_ID}", timeout=10.0
    ).json()["available_seats"]

    assert legacy_after == legacy_before
