"""
Seeds the Phase 6 per-seat inventory model.

Historical capacity note
------------------------
When this model was introduced, the legacy counter row for train 1 read
total_seats=72 / available_seats=45, i.e. 27 seats had already been consumed
by bookings made during Phases 1-5. Those historical bookings carry no coach,
seat or class information anywhere in the system, so there is no trustworthy
way to say WHICH 27 physical berths they correspond to.

Rather than fabricate that mapping, the seeder marks 27 seats BLOCKED and
creates NO seat_allocations rows for them. BLOCKED means "this berth is not
allocatable", not "this berth belongs to booking X" - the seat identity of
that historical consumption is genuinely unknown and is recorded as such.
The specific seats chosen (the last 27, numbers 46-72) are arbitrary but
deterministic, purely so the seeded state is reproducible.

The result reconciles with the legacy counter:
    72 physical seats - 27 BLOCKED = 45 allocatable, matching available_seats.
"""

from models import Coach, Seat, SessionLocal


TRAIN_ID = 1
COACH_NAME = "S1"
CLASS_TYPE = "SL"
TOTAL_SEATS = 72

# Historical consumed capacity carried over from the legacy counter.
HISTORICAL_BLOCKED_SEATS = 27


def get_berth_type(seat_number: int) -> str:
    """
    Standard Indian Railways sleeper bay layout, 8 berths per bay:
        1,4 LOWER   2,5 MIDDLE   3,6 UPPER   7 SIDE_LOWER   8 SIDE_UPPER
    """
    remainder = seat_number % 8

    if remainder in (1, 4):
        return "LOWER"
    if remainder in (2, 5):
        return "MIDDLE"
    if remainder in (3, 6):
        return "UPPER"
    if remainder == 7:
        return "SIDE_LOWER"
    return "SIDE_UPPER"      # remainder == 0


def seed_inventory():
    db = SessionLocal()

    try:

        existing = db.query(Coach).filter(
            Coach.train_id == TRAIN_ID,
            Coach.name == COACH_NAME
        ).first()

        if existing:
            print(f"Coach {COACH_NAME} for train {TRAIN_ID} already seeded.")
            return

        coach = Coach(
            train_id=TRAIN_ID,
            name=COACH_NAME,
            class_type=CLASS_TYPE,
            total_seats=TOTAL_SEATS
        )

        db.add(coach)
        db.commit()
        db.refresh(coach)

        # Seats 1..45 allocatable; 46..72 BLOCKED as historical capacity.
        first_blocked = TOTAL_SEATS - HISTORICAL_BLOCKED_SEATS + 1

        seats = []

        for seat_number in range(1, TOTAL_SEATS + 1):

            seats.append(
                Seat(
                    coach_id=coach.id,
                    seat_number=seat_number,
                    berth_type=get_berth_type(seat_number),
                    status="BLOCKED" if seat_number >= first_blocked else "VACANT"
                )
            )

        db.add_all(seats)
        db.commit()

        print(
            f"Seeded coach {COACH_NAME} ({CLASS_TYPE}) for train {TRAIN_ID}: "
            f"{TOTAL_SEATS} seats, "
            f"{HISTORICAL_BLOCKED_SEATS} BLOCKED (historical capacity, seat "
            f"identity unknown), "
            f"{TOTAL_SEATS - HISTORICAL_BLOCKED_SEATS} allocatable."
        )

    except Exception as error:
        db.rollback()
        print(f"Seeding failed: {error}")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    seed_inventory()
