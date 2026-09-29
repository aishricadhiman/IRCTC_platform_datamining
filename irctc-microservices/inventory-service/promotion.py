"""
Phase 7 cancellation and promotion.

Reimplements the cascading upgrade rules from the RailSarthi monolith
(app/core/waitlist.py) against this service's own model, with one deliberate
split dictated by the microservice boundary:

  * Freeing a CNF berth promotes the longest-waiting RAC passenger onto it,
    and the half-berth that passenger vacates steps back down
    (RAC_FULL -> RAC_ONE -> VACANT). All of that is physical berth state, so
    Inventory does it itself.

  * Filling the RAC capacity that just opened up from the waiting list is
    NOT done here. Inventory has no waiting list - Booking Service owns the
    queue and its ordering. This module therefore reports how much RAC room
    was created, and Booking decides who gets it and calls allocate() for
    them. That is the same CNF <- RAC <- WL cascade RailSarthi performed in
    one function, split across the two services that own each half.
"""

from datetime import datetime

from models import Coach, Seat, SeatAllocation


def _lock_class_seats(db, train_id, class_type):
    """
    Locks every seat of a train+class FOR UPDATE in primary-key order, the
    same discipline allocate_seats() uses, so concurrent cancels and
    allocations cannot interleave on the same berths or deadlock.
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

    seats = db.query(Seat).filter(
        Seat.coach_id.in_(coach_ids)
    ).order_by(Seat.id.asc()).with_for_update().all()

    coach_map = {coach.id: coach for coach in coaches}

    return coach_ids, {seat.id: seat for seat in seats}, coach_map


def _promote_first_rac(db, coach_ids, freed_seat, coach_map, seat_map):
    """
    Moves the longest-waiting live RAC passenger onto a freed CNF berth.

    The allocation row is updated in place rather than replaced, so the
    passenger keeps its original allocation_ref - Booking Service can still
    find and release it later under the same reference it was booked with.

    Returns a description of the promotion, or None if nobody was waiting on
    RAC.
    """

    rac_allocation = db.query(SeatAllocation).join(
        Seat, Seat.id == SeatAllocation.seat_id
    ).filter(
        Seat.coach_id.in_(coach_ids),
        SeatAllocation.outcome == "RAC",
        SeatAllocation.released_at.is_(None)
    ).order_by(SeatAllocation.id.asc()).first()

    if rac_allocation is None:
        return None

    vacated_seat = seat_map.get(rac_allocation.seat_id)

    previous = {
        "coach_name": rac_allocation.coach_name,
        "seat_number": rac_allocation.seat_number,
        "berth_type": rac_allocation.berth_type,
    }

    # Upgrade the passenger onto the freed confirmed berth.
    rac_allocation.outcome = "CNF"
    rac_allocation.seat_id = freed_seat.id
    rac_allocation.coach_name = coach_map[freed_seat.coach_id].name
    rac_allocation.seat_number = freed_seat.seat_number
    rac_allocation.berth_type = freed_seat.berth_type

    freed_seat.status = "BOOKED"

    # The shared berth they came from loses one occupant.
    if vacated_seat is not None:
        if vacated_seat.status == "RAC_FULL":
            vacated_seat.status = "RAC_ONE"
        else:
            vacated_seat.status = "VACANT"

    return {
        "allocation_ref": rac_allocation.allocation_ref,
        "booking_id": rac_allocation.booking_id,
        "passenger_ref": rac_allocation.passenger_ref,
        "from_outcome": "RAC",
        "to_outcome": "CNF",
        "from_seat": previous,
        "to_seat": {
            "coach_name": rac_allocation.coach_name,
            "seat_number": rac_allocation.seat_number,
            "berth_type": rac_allocation.berth_type,
        },
        "vacated_berth_status": vacated_seat.status if vacated_seat else None,
    }


def cancel_allocations(db, train_id, class_type, allocation_ref, passenger_refs=None):
    """
    Cancels live allocations under allocation_ref - all of them, or just the
    passengers named in passenger_refs - and runs the RAC -> CNF promotion
    that each freed confirmed berth triggers.

    Idempotent: allocations already released are simply not found, so a
    repeated cancel returns empty lists rather than failing or promoting
    somebody a second time.

    Must be called inside a Redis lock for this train+class and an open
    transaction.
    """

    coach_ids, seat_map, coach_map = _lock_class_seats(db, train_id, class_type)

    query = db.query(SeatAllocation).filter(
        SeatAllocation.allocation_ref == allocation_ref,
        SeatAllocation.released_at.is_(None)
    )

    if passenger_refs:
        query = query.filter(SeatAllocation.passenger_ref.in_(passenger_refs))

    allocations = query.order_by(SeatAllocation.id.asc()).all()

    cancelled = []
    promotions = []
    now = datetime.utcnow()

    for allocation in allocations:

        seat = seat_map.get(allocation.seat_id)

        cancelled.append({
            "passenger_ref": allocation.passenger_ref,
            "outcome": allocation.outcome,
            "coach_name": allocation.coach_name,
            "seat_number": allocation.seat_number,
            "berth_type": allocation.berth_type,
        })

        allocation.released_at = now

        if seat is None:
            continue

        if allocation.outcome == "CNF":

            seat.status = "VACANT"

            promotion = _promote_first_rac(
                db, coach_ids, seat, coach_map, seat_map
            )

            if promotion is not None:
                promotions.append(promotion)

        else:
            # Giving up one half of a shared RAC berth leaves the other
            # passenger where they are; only the second release frees it.
            if seat.status == "RAC_FULL":
                seat.status = "RAC_ONE"
            else:
                seat.status = "VACANT"

    return cancelled, promotions


def count_rac_capacity(seat_map):
    """
    RAC slots currently free across the locked seats. Booking Service uses
    this to decide how many waitlisted passengers it can now promote.
    """
    available = 0

    for seat in seat_map.values():
        if seat.berth_type != "SIDE_LOWER":
            continue
        if seat.status == "VACANT":
            available += 2
        elif seat.status == "RAC_ONE":
            available += 1

    return available
