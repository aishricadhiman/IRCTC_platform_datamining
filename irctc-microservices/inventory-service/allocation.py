"""
Phase 6 seat allocation.

The business rules here are a reimplementation of the ones established by the
older RailSarthi monolith, expressed against this service's own PostgreSQL
model. None of RailSarthi's code is imported or copied - only its rules are
preserved:

  * CNF berths are every vacant seat EXCEPT SIDE_LOWER. SIDE_UPPER is
    CNF-eligible; SIDE_LOWER is reserved as the RAC pool.
  * Passengers aged 60+ are offered a LOWER berth first, then fall through
    the normal preference/fallback chain.
  * A group is kept in one coach when a single coach can seat all of them.
  * RAC fills SIDE_LOWER berths two passengers to a berth
    (VACANT -> RAC_ONE -> RAC_FULL).
  * When CNF and RAC are both exhausted the passenger is waitlisted.
  * Per-passenger outcomes may differ within one request, so a group can come
    back as a mix of CNF, RAC and WL.

What deliberately differs from RailSarthi, because this is a service and not
a monolith:

  * Nothing about passengers is stored here. A WL outcome is returned to the
    caller and nothing is persisted for it - Booking Service owns the queue.
  * Every mutation runs under a Redis lock plus SELECT ... FOR UPDATE row
    locks. RailSarthi had no concurrency control at all.
  * Seat-to-passenger assignments live in their own table rather than as a
    single booking_id column on the seat, so one RAC berth can honestly
    record the two different bookings sharing it.
"""

from datetime import datetime

from models import Coach, Seat, SeatAllocation


CNF_INELIGIBLE_BERTHS = {"SIDE_LOWER"}

ALLOCATABLE_STATUS = "VACANT"


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _find_by_berth(vacant_by_coach, coach_ids, berth_type):
    for coach_id in coach_ids:
        for seat in vacant_by_coach.get(coach_id, []):
            if seat.berth_type == berth_type:
                return seat
    return None


def _find_any(vacant_by_coach, coach_ids):
    for coach_id in coach_ids:
        if vacant_by_coach.get(coach_id):
            return vacant_by_coach[coach_id][0]
    return None


def _take(vacant_by_coach, seat):
    """Remove a seat from the in-memory vacant pool once it has been taken."""
    pool = vacant_by_coach.get(seat.coach_id)
    if pool and seat in pool:
        pool.remove(seat)


def _outcome_row(allocation):
    return {
        "passenger_ref": allocation.passenger_ref,
        "outcome": allocation.outcome,
        "coach_name": allocation.coach_name,
        "seat_number": allocation.seat_number,
        "berth_type": allocation.berth_type,
    }


def _waitlist_outcome(passenger_ref):
    return {
        "passenger_ref": passenger_ref,
        "outcome": "WL",
        "coach_name": None,
        "seat_number": None,
        "berth_type": None,
    }


def _roll_up(outcomes):
    statuses = [o["outcome"] for o in outcomes]

    if all(status in ("CNF", "RAC") for status in statuses):
        return "CONFIRMED"

    if all(status == "WL" for status in statuses):
        return "WAITLISTED"

    return "PARTIALLY_CONFIRMED"


# --------------------------------------------------
# Idempotency
# --------------------------------------------------

def find_existing_allocation(db, allocation_ref, passengers):
    """
    Returns the original result for an allocation_ref that has already been
    processed, or None if this ref is new.

    Outcomes are rebuilt from the live seat_allocations rows. Any passenger in
    the replayed request with no allocation row was waitlisted the first time
    round - Inventory persists nothing for a WL outcome, so it is inferred
    from the absence of an allocation rather than looked up.

    Consequence worth knowing: a request in which EVERY passenger was
    waitlisted leaves no rows at all, so it is indistinguishable from a new
    ref and will be evaluated again. That can never double-allocate (there
    was nothing allocated to duplicate), but if berths freed up in between,
    the replay may return a better outcome than the original call did.
    """

    existing = db.query(SeatAllocation).filter(
        SeatAllocation.allocation_ref == allocation_ref,
        SeatAllocation.released_at.is_(None)
    ).order_by(SeatAllocation.id.asc()).all()

    if not existing:
        return None

    by_passenger = {row.passenger_ref: row for row in existing}

    outcomes = []

    for passenger in passengers:
        row = by_passenger.get(passenger.passenger_ref)

        if row is not None:
            outcomes.append(_outcome_row(row))
        else:
            outcomes.append(_waitlist_outcome(passenger.passenger_ref))

    return {
        "allocation_ref": allocation_ref,
        "overall": _roll_up(outcomes),
        "passengers": outcomes,
        "replayed": True,
    }


# --------------------------------------------------
# Allocation
# --------------------------------------------------

def allocate_seats(db, train_id, class_type, allocation_ref, booking_id, passengers):
    """
    Allocates one berth per passenger for train_id/class_type.

    Must be called inside a Redis lock for this train+class AND an open
    transaction: every candidate seat row is locked FOR UPDATE here, in a
    deterministic order, so concurrent callers cannot both claim the same
    berth even if the Redis lock were bypassed or expired.

    Returns the same dict shape as find_existing_allocation().
    """

    coaches = db.query(Coach).filter(
        Coach.train_id == train_id,
        Coach.class_type == class_type
    ).order_by(Coach.id.asc()).all()

    if not coaches:
        raise ValueError(
            f"No coaches found for train {train_id} class {class_type}"
        )

    coach_ids = [coach.id for coach in coaches]
    coach_map = {coach.id: coach for coach in coaches}

    # Lock every seat of this train+class, ordered by primary key. Ordering
    # the lock acquisition keeps two concurrent group allocations from taking
    # the same rows in opposite orders and deadlocking each other.
    seats = db.query(Seat).filter(
        Seat.coach_id.in_(coach_ids)
    ).order_by(Seat.id.asc()).with_for_update().all()

    # CNF pool: vacant, and not one of the berths reserved for RAC. BLOCKED
    # seats are excluded here simply by not being VACANT.
    vacant_by_coach = {}

    for seat in seats:
        if seat.status == ALLOCATABLE_STATUS and seat.berth_type not in CNF_INELIGIBLE_BERTHS:
            vacant_by_coach.setdefault(seat.coach_id, []).append(seat)

    # Group preference: if one coach can seat everybody, keep them together.
    passenger_count = len(passengers)
    target_coach_id = None

    for coach_id in coach_ids:
        if len(vacant_by_coach.get(coach_id, [])) >= passenger_count:
            target_coach_id = coach_id
            break

    outcomes = []

    for passenger in passengers:

        is_senior = passenger.age is not None and passenger.age >= 60
        stated = (passenger.berth_preference or "NONE").upper()

        preferred = "LOWER" if is_senior else stated

        target_ids = [target_coach_id] if target_coach_id else coach_ids

        seat_found = None

        # Pass 1 - preferred berth inside the group's coach.
        if preferred != "NONE":
            seat_found = _find_by_berth(vacant_by_coach, target_ids, preferred)

        # Pass 2 - preferred berth in any coach of this class.
        if seat_found is None and preferred != "NONE":
            seat_found = _find_by_berth(vacant_by_coach, coach_ids, preferred)

        # Pass 2b - a senior who could not get LOWER still gets a shot at the
        # berth they actually asked for before falling back to anything.
        if seat_found is None and stated != "NONE" and stated != preferred:
            seat_found = _find_by_berth(vacant_by_coach, coach_ids, stated)

        # Pass 3 - any CNF-eligible berth, group coach first.
        if seat_found is None:
            seat_found = _find_any(vacant_by_coach, target_ids)

        if seat_found is None:
            seat_found = _find_any(vacant_by_coach, coach_ids)

        if seat_found is not None:

            seat_found.status = "BOOKED"
            _take(vacant_by_coach, seat_found)

            allocation = SeatAllocation(
                seat_id=seat_found.id,
                allocation_ref=allocation_ref,
                booking_id=booking_id,
                passenger_ref=passenger.passenger_ref,
                outcome="CNF",
                coach_name=coach_map[seat_found.coach_id].name,
                seat_number=seat_found.seat_number,
                berth_type=seat_found.berth_type,
            )

            db.add(allocation)
            outcomes.append(_outcome_row(allocation))
            continue

        # ------------------------------------------
        # RAC - SIDE_LOWER berths, two passengers each
        # ------------------------------------------
        # An already half-occupied berth is filled before a fresh one is
        # opened, so RAC berths are consumed densely.

        rac_seat = None

        for seat in seats:
            if seat.berth_type == "SIDE_LOWER" and seat.status == "RAC_ONE":
                rac_seat = seat
                break

        if rac_seat is None:
            for seat in seats:
                if seat.berth_type == "SIDE_LOWER" and seat.status == ALLOCATABLE_STATUS:
                    rac_seat = seat
                    break

        if rac_seat is not None:

            rac_seat.status = "RAC_FULL" if rac_seat.status == "RAC_ONE" else "RAC_ONE"

            allocation = SeatAllocation(
                seat_id=rac_seat.id,
                allocation_ref=allocation_ref,
                booking_id=booking_id,
                passenger_ref=passenger.passenger_ref,
                outcome="RAC",
                coach_name=coach_map[rac_seat.coach_id].name,
                seat_number=rac_seat.seat_number,
                berth_type=rac_seat.berth_type,
            )

            db.add(allocation)
            outcomes.append(_outcome_row(allocation))
            continue

        # ------------------------------------------
        # Waitlist - nothing is persisted here
        # ------------------------------------------

        outcomes.append(_waitlist_outcome(passenger.passenger_ref))

    return {
        "allocation_ref": allocation_ref,
        "overall": _roll_up(outcomes),
        "passengers": outcomes,
        "replayed": False,
    }


# --------------------------------------------------
# Release
# --------------------------------------------------

def release_allocation(db, allocation_ref):
    """
    Releases every live seat held under allocation_ref and returns what was
    freed. Idempotent: a ref with nothing outstanding returns an empty list
    rather than failing, so a retried or duplicated release is harmless.

    Must be called inside a Redis lock and an open transaction, as with
    allocate_seats().
    """

    allocations = db.query(SeatAllocation).filter(
        SeatAllocation.allocation_ref == allocation_ref,
        SeatAllocation.released_at.is_(None)
    ).order_by(SeatAllocation.id.asc()).all()

    if not allocations:
        return []

    seat_ids = sorted({allocation.seat_id for allocation in allocations})

    seats = db.query(Seat).filter(
        Seat.id.in_(seat_ids)
    ).order_by(Seat.id.asc()).with_for_update().all()

    seat_map = {seat.id: seat for seat in seats}

    released = []
    now = datetime.utcnow()

    for allocation in allocations:

        seat = seat_map.get(allocation.seat_id)

        if seat is not None:

            if allocation.outcome == "RAC":
                # Giving up one half of a shared berth leaves the other
                # passenger in place; only the second release frees it.
                if seat.status == "RAC_FULL":
                    seat.status = "RAC_ONE"
                else:
                    seat.status = ALLOCATABLE_STATUS
            else:
                seat.status = ALLOCATABLE_STATUS

            released.append({
                "coach_name": allocation.coach_name,
                "seat_number": allocation.seat_number,
                "berth_type": allocation.berth_type,
                "outcome": allocation.outcome,
                "new_status": seat.status,
            })

        allocation.released_at = now

    return released
