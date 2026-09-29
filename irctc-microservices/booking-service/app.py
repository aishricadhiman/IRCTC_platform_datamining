import hmac
import os
import time
import uuid
from typing import List, Optional

import httpx

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import clients
from clients import INVENTORY_URL, NOTIFICATION_URL, PAYMENT_URL
from models import (
    Base,
    Booking,
    Passenger,
    SessionLocal,
    WaitlistQueue,
    engine,
)

# --------------------------------------------------
# Internal Webhook Security
# --------------------------------------------------
#
# Shared secret checked on POST /internal/payment-events. This endpoint is
# service-to-service only (Payment Gateway calls it directly, never through
# the API Gateway and never from a browser) - a static shared secret is the
# simplest protection appropriate here, distinct from the user-facing JWT
# auth added in Phase 3, which has no business guarding an internal call.
INTERNAL_WEBHOOK_SECRET = os.getenv(
    "INTERNAL_WEBHOOK_SECRET", "dev-only-insecure-webhook-secret"
)


# --------------------------------------------------
# FastAPI Application
# --------------------------------------------------

app = FastAPI(title="IRCTC Booking Service")


# --------------------------------------------------
# Request Schema
# --------------------------------------------------

class PassengerCreate(BaseModel):

    name: str

    age: Optional[int] = None

    gender: Optional[str] = None

    berth_preference: str = "NONE"


class MultiPassengerBookingCreate(BaseModel):
    """
    Phase 8 booking request: several passengers, a travel class, and per
    passenger berth preferences - everything the per-seat allocator needs and
    the legacy single-passenger request could not express.
    """

    user_id: int

    train_id: int

    class_type: str

    amount: float

    passengers: List[PassengerCreate]

    payment_method: str = "UPI"

    # TEST HOOK ONLY - see the legacy BookingCreate hook below.
    simulate_confirm_failure: bool = False


class CancelBookingRequest(BaseModel):

    # Omit to cancel the whole booking; supply ids to cancel only those
    # passengers.
    passenger_ids: Optional[List[int]] = None


class BookingCreate(BaseModel):

    user_id: int

    train_id: int

    passenger_name: str

    amount: float

    # Passed through to the Payment Gateway's /payment/initiate contract.
    # Optional with a default so existing callers that predate Phase 4
    # (which only ever talked to the old placeholder payment-service, and
    # never had to specify this) keep working unchanged.
    payment_method: str = "UPI"

    # TEST HOOK ONLY - lets tests deterministically reproduce the
    # "payment succeeded, booking confirmation failed" scenario without
    # needing to sever the DB connection mid-request. Mirrors the
    # force_outcome pattern already used by irctc-payment-gateway's mock
    # provider. Defaults to False and is a no-op for real traffic.
    simulate_confirm_failure: bool = False


# --------------------------------------------------
# Database Initialization
# --------------------------------------------------

def initialize_database():

    for attempt in range(10):

        try:

            Base.metadata.create_all(bind=engine)

            print("Connected to Booking PostgreSQL successfully.")
            print("Bookings table is ready.")

            return

        except Exception as error:

            print(f"Database connection attempt {attempt + 1} failed.")
            print(error)

            time.sleep(3)

    raise Exception("Could not connect to Booking PostgreSQL.")


@app.on_event("startup")
def startup_event():

    initialize_database()


# --------------------------------------------------
# Create Booking
# --------------------------------------------------

@app.post("/bookings")
async def create_booking(data: BookingCreate):

    db = SessionLocal()

    booking = None

    try:

        # ------------------------------------------
        # 1. Create a pending booking
        # ------------------------------------------

        booking = Booking(
            user_id=data.user_id,
            train_id=data.train_id,
            passenger_name=data.passenger_name,
            amount=data.amount,
            status="PENDING"
        )

        db.add(booking)

        db.commit()

        db.refresh(booking)

        booking_id = booking.id

        # ------------------------------------------
        # 2. Reserve a seat
        # ------------------------------------------

        async with httpx.AsyncClient() as client:

            reserve_response = await client.post(
                f"{INVENTORY_URL}/reserve/{data.train_id}"
            )

            if reserve_response.status_code != 200:

                booking.status = "FAILED"

                db.commit()

                raise HTTPException(
                    status_code=400,
                    detail="Seat reservation failed"
                )

            # --------------------------------------
            # 3. Process payment via the Payment Gateway
            # --------------------------------------
            # irctc-payment-gateway's /payment/initiate always responds
            # HTTP 200 for a well-formed request - the actual outcome
            # (SUCCESS / FAILED / PROCESSING) is carried in the JSON body's
            # "status" field, unlike the old placeholder payment-service
            # which signalled failure via a non-200 status code. A non-200
            # response here means the gateway itself could not be reached
            # or rejected the request outright (e.g. its idempotency key
            # reused with a different payload -> 409).

            payment_response = await client.post(
                f"{PAYMENT_URL}/payment/initiate",
                json={
                    "booking_id": booking_id,
                    "user_id": data.user_id,
                    "amount": data.amount,
                    "payment_method": data.payment_method,
                    "idempotency_key": f"booking-{booking.id}-payment"
                }
            )

            if payment_response.status_code != 200:

                await client.post(
                    f"{INVENTORY_URL}/release/{data.train_id}"
                )

                booking.status = "PAYMENT_FAILED"

                db.commit()

                raise HTTPException(
                    status_code=502,
                    detail="Payment gateway request failed"
                )

            payment_data = payment_response.json()
            payment_status = payment_data.get("status")

            # ----------------------------------------------------------
            # 3a. Payment FAILED
            # ----------------------------------------------------------

            if payment_status == "FAILED":

                await client.post(
                    f"{INVENTORY_URL}/release/{data.train_id}"
                )

                booking.status = "PAYMENT_FAILED"

                db.commit()

                raise HTTPException(
                    status_code=400,
                    detail={"message": "Payment failed", "payment": payment_data}
                )

            # ----------------------------------------------------------
            # 3b. Payment still PROCESSING
            # ----------------------------------------------------------
            # Not a terminal outcome yet (the gateway's mock provider
            # resolves this asynchronously; a real gateway would resolve
            # it via callback/webhook). The booking must NOT be confirmed,
            # but the seat must also NOT be released, since payment may
            # still succeed.
            #
            # KNOWN LIMITATION / Phase 5 dependency: nothing currently
            # moves this booking out of AWAITING_PAYMENT once this
            # response is returned. The Payment Gateway's own
            # reconciliation job and callback/verify endpoints will
            # eventually settle the underlying transaction to SUCCESS or
            # FAILED, but until Phase 5 adds the webhook
            # (POST /internal/payment-events) on this service, that
            # outcome never reaches this booking record - it stays
            # AWAITING_PAYMENT, still holding its reserved seat, until
            # Phase 5 closes this loop.

            if payment_status == "PROCESSING":

                booking.status = "AWAITING_PAYMENT"

                db.commit()

                db.refresh(booking)

                return {
                    "message": (
                        "Payment is processing; booking is not yet "
                        "confirmed. Resolving this asynchronously is a "
                        "Phase 5 webhook dependency, not yet implemented."
                    ),
                    "booking": {
                        "id": booking.id,
                        "user_id": booking.user_id,
                        "train_id": booking.train_id,
                        "passenger_name": booking.passenger_name,
                        "amount": booking.amount,
                        "status": booking.status
                    },
                    "payment": payment_data
                }

            # ----------------------------------------------------------
            # 3c. Anything else is not a recognized success
            # ----------------------------------------------------------
            # Guards against ever treating an unexpected/unknown status
            # value from the gateway as an implicit success.

            if payment_status != "SUCCESS":

                await client.post(
                    f"{INVENTORY_URL}/release/{data.train_id}"
                )

                booking.status = "PAYMENT_FAILED"

                db.commit()

                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Unrecognized payment status "
                        f"'{payment_status}' from Payment Gateway"
                    )
                )

            # --------------------------------------
            # 4. Confirm booking (payment_status == "SUCCESS")
            # --------------------------------------
            # Payment has already succeeded at this point. If persisting the
            # CONFIRMED status fails for any reason (DB error, connection
            # drop, or the simulate_confirm_failure test hook), the
            # passenger would otherwise be left "paid with no confirmed
            # seat" - the reserved inventory must be released so it is not
            # orphaned.

            try:

                if data.simulate_confirm_failure:
                    raise RuntimeError(
                        "Simulated booking confirmation failure (test hook)"
                    )

                booking.status = "CONFIRMED"

                db.commit()

                db.refresh(booking)

            except Exception as confirm_error:

                db.rollback()

                print(
                    f"Booking confirmation failed after successful payment "
                    f"for booking {booking_id}: {confirm_error}"
                )

                try:

                    await client.post(
                        f"{INVENTORY_URL}/release/{data.train_id}"
                    )

                except httpx.RequestError as release_error:

                    print(
                        f"Failed to release inventory for booking "
                        f"{booking_id} after confirmation failure: "
                        f"{release_error}"
                    )

                booking.status = "FAILED"

                db.commit()

                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Payment succeeded but booking confirmation failed; "
                        "the reserved seat has been released."
                    )
                )

            # --------------------------------------
            # 5. Send notification (best-effort)
            # --------------------------------------
            # Notification delivery must never affect whether an already
            # paid-and-confirmed booking is reported as successful. Bounded
            # by a short timeout; any failure here is logged, not raised.

            try:

                notification_response = await client.post(
                    f"{NOTIFICATION_URL}/notifications",
                    json={
                        "user_id": booking.user_id,
                        "booking_id": booking.id,
                        "message": (
                            f"Your booking for train {booking.train_id} "
                            f"has been confirmed."
                        ),
                        "notification_type": "BOOKING_CONFIRMED"
                    },
                    timeout=3.0
                )

                if notification_response.status_code != 200:

                    print(
                        f"Notification service returned "
                        f"{notification_response.status_code} for "
                        f"booking {booking.id}."
                    )

            except httpx.RequestError as notify_error:

                print(
                    f"Notification delivery failed for booking "
                    f"{booking.id}: {notify_error}"
                )

            return {
                "message": "Booking confirmed",
                "booking": {
                    "id": booking.id,
                    "user_id": booking.user_id,
                    "train_id": booking.train_id,
                    "passenger_name": booking.passenger_name,
                    "amount": booking.amount,
                    "status": booking.status
                },
                "payment": payment_data
            }

    except HTTPException:

        raise

    except Exception as error:

        print("Booking error:", error)

        if booking:

            booking.status = "FAILED"

            db.commit()

        raise HTTPException(
            status_code=500,
            detail="Booking could not be completed"
        )

    finally:

        db.close()


# --------------------------------------------------
# Get All Bookings
# --------------------------------------------------

@app.get("/bookings")
def get_bookings():

    db = SessionLocal()

    try:

        bookings = db.query(Booking).all()

        return [

            {
                "id": booking.id,
                "user_id": booking.user_id,
                "train_id": booking.train_id,
                "passenger_name": booking.passenger_name,
                "amount": booking.amount,
                "status": booking.status
            }

            for booking in bookings

        ]

    finally:

        db.close()


# --------------------------------------------------
# Get Booking by ID
# --------------------------------------------------

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: int):

    db = SessionLocal()

    try:

        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking:

            raise HTTPException(
                status_code=404,
                detail="Booking not found"
            )

        return {

            "id": booking.id,
            "user_id": booking.user_id,
            "train_id": booking.train_id,
            "passenger_name": booking.passenger_name,
            "amount": booking.amount,
            "status": booking.status

        }

    finally:

        db.close()

# --------------------------------------------------
# Internal: Payment Event Webhook (Phase 5)
# --------------------------------------------------
#
# Consumes the Payment Gateway's existing PaymentSuccess / PaymentFailed
# events, delivered directly service-to-service. The body mirrors the
# gateway's own durable event structure exactly - its event row id, its
# topic, and its unmodified payload - rather than introducing a second,
# competing event format.


class PaymentEventPayload(BaseModel):

    booking_id: int

    status: str  # SUCCESS | FAILED

    txn_reference: Optional[str] = None

    amount: Optional[float] = None


class PaymentEventRequest(BaseModel):

    event_id: int

    topic: str

    payload: PaymentEventPayload


@app.post("/internal/payment-events")
async def handle_payment_event(
    event: PaymentEventRequest,
    x_internal_webhook_secret: Optional[str] = Header(default=None)
):
    """
    Resolves a booking left in AWAITING_PAYMENT once the Payment Gateway
    reaches a terminal outcome for its payment.

    Idempotency: the booking's own status is the guard. A booking leaves
    AWAITING_PAYMENT exactly once, so any repeat delivery of the same
    event - or a late event for a booking already settled synchronously
    during the original request - finds a booking that is no longer
    AWAITING_PAYMENT and is a no-op. Nothing is re-confirmed, no seat is
    re-reserved or re-released, and no payment is re-created. This mirrors
    the "already-terminal, no-op" idiom the Payment Gateway itself uses in
    its own /payment/callback handler.

    The row is locked FOR UPDATE so two concurrent deliveries of the same
    event cannot both observe AWAITING_PAYMENT and both act on it.
    """

    if not hmac.compare_digest(
        x_internal_webhook_secret or "", INTERNAL_WEBHOOK_SECRET
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing internal webhook credential"
        )

    db = SessionLocal()

    try:

        booking = db.query(Booking).filter(
            Booking.id == event.payload.booking_id
        ).with_for_update().first()

        if not booking:

            # Acknowledged deliberately: retrying forever for a booking id
            # this service has no record of would block the gateway's queue.
            print(
                f"[payment-event] event {event.event_id}: no booking "
                f"{event.payload.booking_id} on this service; ignoring"
            )

            return {
                "acknowledged": True,
                "note": "booking not found"
            }

        if booking.status != "AWAITING_PAYMENT":

            print(
                f"[payment-event] event {event.event_id}: booking "
                f"{booking.id} is already {booking.status}; no-op"
            )

            return {
                "acknowledged": True,
                "note": "booking not awaiting payment; no-op",
                "booking_status": booking.status
            }

        if event.payload.status == "SUCCESS":

            # A per-seat booking (Phase 8) settles to whatever its passengers
            # actually got - all seated is CONFIRMED, some waitlisted is
            # PARTIALLY_CONFIRMED - and earns its PNR here. A legacy
            # single-passenger booking has no passenger rows and simply
            # becomes CONFIRMED as it always did.
            seated = db.query(Passenger).filter(
                Passenger.booking_id == booking.id
            ).all()

            if seated:
                booking.status = _roll_up_status(seated)
                if booking.pnr is None:
                    booking.pnr = f"PNR-{uuid.uuid4().hex[:10].upper()}"
            else:
                booking.status = "CONFIRMED"

            db.commit()

            db.refresh(booking)

            print(
                f"[payment-event] event {event.event_id}: booking "
                f"{booking.id} AWAITING_PAYMENT -> {booking.status}"
            )

            # Best-effort, exactly as in the synchronous booking path - a
            # notification failure must never undo a confirmed booking.
            try:

                async with httpx.AsyncClient() as client:

                    await client.post(
                        f"{NOTIFICATION_URL}/notifications",
                        json={
                            "user_id": booking.user_id,
                            "booking_id": booking.id,
                            "message": (
                                f"Your booking for train {booking.train_id} "
                                f"has been confirmed."
                            ),
                            "notification_type": "BOOKING_CONFIRMED"
                        },
                        timeout=3.0
                    )

            except httpx.RequestError as notify_error:

                print(
                    f"Notification delivery failed for booking "
                    f"{booking.id}: {notify_error}"
                )

            return {
                "acknowledged": True,
                "note": "booking confirmed",
                "booking_status": booking.status
            }

        if event.payload.status == "FAILED":

            booking.status = "PAYMENT_FAILED"

            db.commit()

            db.refresh(booking)

            print(
                f"[payment-event] event {event.event_id}: booking "
                f"{booking.id} AWAITING_PAYMENT -> PAYMENT_FAILED"
            )

            # Hand back whatever this booking was holding. A per-seat booking
            # cancels its berths by allocation_ref, which also triggers
            # Inventory's RAC promotion; a legacy booking just decrements the
            # counter as before. Best-effort with logging either way - the
            # booking stays PAYMENT_FAILED regardless.
            try:

                async with httpx.AsyncClient() as client:

                    if booking.allocation_ref:

                        await clients.cancel_seats(
                            client=client,
                            train_id=booking.train_id,
                            class_type=booking.class_type,
                            allocation_ref=booking.allocation_ref,
                        )

                    else:

                        await client.post(
                            f"{INVENTORY_URL}/release/{booking.train_id}",
                            timeout=3.0
                        )

            except httpx.RequestError as release_error:

                print(
                    f"Failed to release inventory for booking "
                    f"{booking.id} after payment failure: {release_error}"
                )

            return {
                "acknowledged": True,
                "note": "booking marked payment failed; inventory released",
                "booking_status": booking.status
            }

        # Unrecognized status - never guess at a booking outcome. Logged and
        # acknowledged so one malformed event cannot stall the queue.
        print(
            f"[payment-event] event {event.event_id}: unrecognized status "
            f"'{event.payload.status}' for booking {booking.id}; ignoring"
        )

        return {
            "acknowledged": True,
            "note": f"unrecognized status '{event.payload.status}'; ignored",
            "booking_status": booking.status
        }

    finally:

        db.close()


# ==================================================
# PHASE 8: MULTI-PASSENGER BOOKING (per-seat inventory)
# ==================================================
#
# Runs on a separate path from the legacy POST /bookings so the Phase 1-5
# single-passenger flow keeps working untouched against the legacy counter.
# Phase 12 can retire the legacy endpoint and move this one onto /bookings.


def _roll_up_status(passengers):
    statuses = [p.status for p in passengers]

    if all(status in ("CNF", "RAC") for status in statuses):
        return "CONFIRMED"

    if all(status == "WL" for status in statuses):
        return "WAITLISTED"

    return "PARTIALLY_CONFIRMED"


def _passenger_payload(passenger):
    return {
        "id": passenger.id,
        "passenger_ref": passenger.passenger_ref,
        "name": passenger.name,
        "age": passenger.age,
        "gender": passenger.gender,
        "berth_preference": passenger.berth_preference,
        "status": passenger.status,
        "coach_name": passenger.coach_name,
        "seat_number": passenger.seat_number,
        "berth_type": passenger.berth_type,
    }


def _booking_payload(db, booking):
    passengers = db.query(Passenger).filter(
        Passenger.booking_id == booking.id
    ).order_by(Passenger.id.asc()).all()

    waitlist = {
        entry.passenger_id: entry.priority_number
        for entry in db.query(WaitlistQueue).filter(
            WaitlistQueue.booking_id == booking.id,
            WaitlistQueue.status == "WAITING"
        ).all()
    }

    rows = []

    for passenger in passengers:
        row = _passenger_payload(passenger)
        if passenger.id in waitlist:
            row["waitlist_position"] = waitlist[passenger.id]
        rows.append(row)

    return {
        "id": booking.id,
        "user_id": booking.user_id,
        "train_id": booking.train_id,
        "class_type": booking.class_type,
        "amount": booking.amount,
        "status": booking.status,
        "pnr": booking.pnr,
        "passengers": rows,
    }


def _next_waitlist_priority(db, train_id, class_type):
    current = db.query(WaitlistQueue).filter(
        WaitlistQueue.train_id == train_id,
        WaitlistQueue.class_type == class_type,
        WaitlistQueue.status == "WAITING"
    ).count()

    return current + 1


def _resequence_waitlist(db, train_id, class_type):
    """Closes gaps left by passengers who were upgraded or cancelled."""
    entries = db.query(WaitlistQueue).filter(
        WaitlistQueue.train_id == train_id,
        WaitlistQueue.class_type == class_type,
        WaitlistQueue.status == "WAITING"
    ).order_by(WaitlistQueue.priority_number.asc()).all()

    for index, entry in enumerate(entries):
        entry.priority_number = index + 1


def _apply_outcomes(db, booking, passengers, outcomes):
    """
    Writes Inventory's per-passenger verdict onto the local passenger rows and
    opens a waiting-list entry for anyone who could not be seated.
    """
    by_ref = {p.passenger_ref: p for p in passengers}

    for outcome in outcomes:
        passenger = by_ref.get(outcome["passenger_ref"])

        if passenger is None:
            continue

        passenger.status = outcome["outcome"]
        passenger.coach_name = outcome.get("coach_name")
        passenger.seat_number = outcome.get("seat_number")
        passenger.berth_type = outcome.get("berth_type")

        if outcome["outcome"] == "WL":
            passenger.allocation_ref = None

            db.add(WaitlistQueue(
                booking_id=booking.id,
                passenger_id=passenger.id,
                train_id=booking.train_id,
                class_type=booking.class_type,
                priority_number=_next_waitlist_priority(
                    db, booking.train_id, booking.class_type
                ),
                status="WAITING",
            ))
            db.flush()
        else:
            passenger.allocation_ref = booking.allocation_ref


@app.post("/bookings/multi")
async def create_multi_passenger_booking(data: MultiPassengerBookingCreate):

    if not data.passengers:
        raise HTTPException(
            status_code=400,
            detail="At least one passenger is required"
        )

    db = SessionLocal()

    booking = None

    try:

        # 1. Booking shell + passenger rows.
        booking = Booking(
            user_id=data.user_id,
            train_id=data.train_id,
            class_type=data.class_type,
            amount=data.amount,
            status="PENDING",
        )

        db.add(booking)
        db.commit()
        db.refresh(booking)

        booking.allocation_ref = f"booking-{booking.id}-alloc"

        passengers = []

        for index, passenger_data in enumerate(data.passengers, start=1):
            passenger = Passenger(
                booking_id=booking.id,
                passenger_ref=f"p{index}",
                name=passenger_data.name,
                age=passenger_data.age,
                gender=passenger_data.gender,
                berth_preference=passenger_data.berth_preference,
                status="WL",
            )
            db.add(passenger)
            passengers.append(passenger)

        db.commit()

        for passenger in passengers:
            db.refresh(passenger)

        async with httpx.AsyncClient() as client:

            # 2. Ask Inventory for berths.
            allocate_response = await clients.allocate_seats(
                client=client,
                train_id=data.train_id,
                class_type=data.class_type,
                allocation_ref=booking.allocation_ref,
                booking_id=booking.id,
                passengers=[
                    {
                        "passenger_ref": p.passenger_ref,
                        "age": p.age,
                        "gender": p.gender,
                        "berth_preference": p.berth_preference,
                    }
                    for p in passengers
                ],
            )

            if allocate_response.status_code != 200:

                booking.status = "FAILED"
                db.commit()

                raise HTTPException(
                    status_code=allocate_response.status_code,
                    detail=f"Seat allocation failed: {allocate_response.text}"
                )

            allocation = allocate_response.json()

            _apply_outcomes(db, booking, passengers, allocation["passengers"])
            db.commit()

            # 3. Payment.
            payment_response = await clients.initiate_payment(
                client=client,
                booking_id=booking.id,
                user_id=data.user_id,
                amount=data.amount,
                payment_method=data.payment_method,
                idempotency_key=f"booking-{booking.id}-payment",
            )

            if payment_response.status_code != 200:

                await clients.cancel_seats(
                    client, data.train_id, data.class_type,
                    booking.allocation_ref
                )

                booking.status = "PAYMENT_FAILED"
                db.commit()

                raise HTTPException(
                    status_code=502,
                    detail="Payment gateway request failed"
                )

            payment_data = payment_response.json()
            payment_status = payment_data.get("status")

            if payment_status == "FAILED":

                await clients.cancel_seats(
                    client, data.train_id, data.class_type,
                    booking.allocation_ref
                )

                booking.status = "PAYMENT_FAILED"
                db.commit()

                raise HTTPException(
                    status_code=400,
                    detail={"message": "Payment failed", "payment": payment_data}
                )

            if payment_status == "PROCESSING":

                # Berths stay held: the payment may still succeed, and the
                # Phase 5 webhook settles this booking either way.
                booking.status = "AWAITING_PAYMENT"
                db.commit()

                return {
                    "message": (
                        "Payment is processing; booking is not yet confirmed."
                    ),
                    "booking": _booking_payload(db, booking),
                    "payment": payment_data,
                }

            if payment_status != "SUCCESS":

                await clients.cancel_seats(
                    client, data.train_id, data.class_type,
                    booking.allocation_ref
                )

                booking.status = "PAYMENT_FAILED"
                db.commit()

                raise HTTPException(
                    status_code=502,
                    detail=f"Unrecognized payment status '{payment_status}'"
                )

            # 4. Confirm. Payment has already succeeded, so a failure here
            #    must hand the berths back rather than strand them.
            try:

                if data.simulate_confirm_failure:
                    raise RuntimeError(
                        "Simulated booking confirmation failure (test hook)"
                    )

                booking.status = _roll_up_status(passengers)
                booking.pnr = f"PNR-{uuid.uuid4().hex[:10].upper()}"

                db.commit()
                db.refresh(booking)

            except Exception as confirm_error:

                db.rollback()

                print(
                    f"Booking confirmation failed after successful payment "
                    f"for booking {booking.id}: {confirm_error}"
                )

                try:
                    await clients.cancel_seats(
                        client, data.train_id, data.class_type,
                        booking.allocation_ref
                    )
                except httpx.RequestError as cancel_error:
                    print(
                        f"Failed to release berths for booking "
                        f"{booking.id}: {cancel_error}"
                    )

                booking.status = "FAILED"
                db.commit()

                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Payment succeeded but booking confirmation failed; "
                        "the reserved berths have been released."
                    )
                )

            # 5. Notification - best effort, never blocks the response.
            try:
                await clients.send_notification(
                    client=client,
                    user_id=booking.user_id,
                    booking_id=booking.id,
                    message=(
                        f"Your booking for train {booking.train_id} "
                        f"is {booking.status}. PNR: {booking.pnr}"
                    ),
                )
            except httpx.RequestError as notify_error:
                print(
                    f"Notification delivery failed for booking "
                    f"{booking.id}: {notify_error}"
                )

            return {
                "message": "Booking processed",
                "booking": _booking_payload(db, booking),
                "payment": payment_data,
            }

    except HTTPException:
        raise

    except Exception as error:

        print("Multi-passenger booking error:", error)

        if booking is not None:
            try:
                booking.status = "FAILED"
                db.commit()
            except Exception:
                db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Booking could not be completed"
        )

    finally:

        db.close()


# ==================================================
# PHASE 8: CANCELLATION + WAITLIST PROMOTION
# ==================================================
#
# Completes the CNF <- RAC <- WL cascade across the service boundary:
#
#   Inventory cancels the berths and promotes RAC -> CNF, because that is
#   physical berth state it owns, and reports how much RAC room opened up.
#
#   This service then fills that room from its own waiting list, oldest
#   first, by asking Inventory to allocate for those passengers - each under
#   its own allocation_ref, since Inventory replays a ref it has already seen.
#
# Refunds are deliberately not issued here. That is Phase 10 work, and the
# gap is real: cancelling a paid booking currently returns the berths without
# returning the money.


async def _promote_waitlisted(db, client, booking, rac_slots_available):
    """
    Offers newly freed capacity to the longest-waiting passengers on this
    train+class. Returns what actually changed.

    Each promotion is a separate allocate() call with its own reference, so a
    passenger upgraded later can still be cancelled or released on its own.
    """

    promoted = []

    if rac_slots_available <= 0:
        return promoted

    waiting = db.query(WaitlistQueue).filter(
        WaitlistQueue.train_id == booking.train_id,
        WaitlistQueue.class_type == booking.class_type,
        WaitlistQueue.status == "WAITING"
    ).order_by(WaitlistQueue.priority_number.asc()).all()

    for entry in waiting[:rac_slots_available]:

        passenger = db.query(Passenger).filter(
            Passenger.id == entry.passenger_id
        ).first()

        if passenger is None or passenger.status != "WL":
            continue

        promotion_ref = f"{booking.allocation_ref}-promo-{passenger.id}"

        try:
            response = await clients.allocate_seats(
                client=client,
                train_id=entry.train_id,
                class_type=entry.class_type,
                allocation_ref=promotion_ref,
                booking_id=entry.booking_id,
                passengers=[{
                    "passenger_ref": passenger.passenger_ref,
                    "age": passenger.age,
                    "gender": passenger.gender,
                    "berth_preference": passenger.berth_preference,
                }],
            )
        except httpx.RequestError as error:
            print(f"Waitlist promotion call failed for passenger {passenger.id}: {error}")
            break

        if response.status_code != 200:
            print(
                f"Waitlist promotion rejected for passenger "
                f"{passenger.id}: HTTP {response.status_code}"
            )
            break

        outcome = response.json()["passengers"][0]

        if outcome["outcome"] == "WL":
            # Nothing was actually free after all - leave them queued.
            break

        passenger.status = outcome["outcome"]
        passenger.coach_name = outcome.get("coach_name")
        passenger.seat_number = outcome.get("seat_number")
        passenger.berth_type = outcome.get("berth_type")
        passenger.allocation_ref = promotion_ref

        entry.status = "UPGRADED"

        promoted.append({
            "passenger_id": passenger.id,
            "booking_id": entry.booking_id,
            "name": passenger.name,
            "from_status": "WL",
            "to_status": passenger.status,
            "coach_name": passenger.coach_name,
            "seat_number": passenger.seat_number,
            "berth_type": passenger.berth_type,
        })

    return promoted


def _apply_inventory_promotions(db, promotions):
    """
    Mirrors Inventory's RAC -> CNF upgrades onto the local passenger rows.

    A promotion can belong to a completely different booking than the one
    being cancelled - whoever was longest on RAC gets the berth - so the
    owning booking is looked up by the allocation_ref Inventory reported.
    """

    applied = []

    for promotion in promotions:

        owner = db.query(Booking).filter(
            Booking.allocation_ref == promotion["allocation_ref"]
        ).first()

        passenger = None

        if owner is not None:
            passenger = db.query(Passenger).filter(
                Passenger.booking_id == owner.id,
                Passenger.passenger_ref == promotion["passenger_ref"]
            ).first()

        if passenger is None:
            # Also covers passengers promoted under their own -promo- ref.
            passenger = db.query(Passenger).filter(
                Passenger.allocation_ref == promotion["allocation_ref"],
                Passenger.passenger_ref == promotion["passenger_ref"]
            ).first()

        if passenger is None:
            continue

        passenger.status = promotion["to_outcome"]
        passenger.coach_name = promotion["to_seat"]["coach_name"]
        passenger.seat_number = promotion["to_seat"]["seat_number"]
        passenger.berth_type = promotion["to_seat"]["berth_type"]

        applied.append({
            "passenger_id": passenger.id,
            "booking_id": passenger.booking_id,
            "name": passenger.name,
            "from_status": promotion["from_outcome"],
            "to_status": passenger.status,
            "coach_name": passenger.coach_name,
            "seat_number": passenger.seat_number,
            "berth_type": passenger.berth_type,
        })

    return applied


@app.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: int, request: CancelBookingRequest):

    db = SessionLocal()

    try:

        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).with_for_update().first()

        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")

        if booking.allocation_ref is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This booking predates per-seat inventory and cannot be "
                    "cancelled through this endpoint."
                )
            )

        query = db.query(Passenger).filter(
            Passenger.booking_id == booking.id,
            Passenger.status != "CANCELLED"
        )

        if request.passenger_ids:
            query = query.filter(Passenger.id.in_(request.passenger_ids))

        targets = query.order_by(Passenger.id.asc()).all()

        if not targets:
            return {
                "booking_id": booking.id,
                "status": booking.status,
                "cancelled": [],
                "promotions": [],
                "note": "nothing left to cancel",
            }

        seated = [p for p in targets if p.status in ("CNF", "RAC")]
        waitlisted = [p for p in targets if p.status == "WL"]

        cancelled_rows = []
        inventory_promotions = []
        rac_slots_available = 0

        async with httpx.AsyncClient() as client:

            # Group by the reference that actually holds each berth: a
            # passenger promoted off the waiting list holds its own.
            by_ref = {}

            for passenger in seated:
                by_ref.setdefault(
                    passenger.allocation_ref or booking.allocation_ref, []
                ).append(passenger.passenger_ref)

            for allocation_ref, passenger_refs in by_ref.items():

                response = await clients.cancel_seats(
                    client=client,
                    train_id=booking.train_id,
                    class_type=booking.class_type,
                    allocation_ref=allocation_ref,
                    passenger_refs=passenger_refs,
                )

                if response.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Inventory cancellation failed: "
                            f"HTTP {response.status_code}"
                        )
                    )

                body = response.json()
                inventory_promotions.extend(body.get("promotions", []))
                rac_slots_available = max(
                    rac_slots_available, body.get("rac_slots_available", 0)
                )

            # Mark the cancelled passengers locally.
            for passenger in seated + waitlisted:

                cancelled_rows.append({
                    "passenger_id": passenger.id,
                    "name": passenger.name,
                    "previous_status": passenger.status,
                    "coach_name": passenger.coach_name,
                    "seat_number": passenger.seat_number,
                })

                passenger.status = "CANCELLED"
                passenger.coach_name = None
                passenger.seat_number = None
                passenger.berth_type = None
                passenger.allocation_ref = None

            # A waitlisted passenger who cancels simply leaves the queue.
            for passenger in waitlisted:
                entry = db.query(WaitlistQueue).filter(
                    WaitlistQueue.passenger_id == passenger.id,
                    WaitlistQueue.status == "WAITING"
                ).first()
                if entry is not None:
                    entry.status = "CANCELLED"

            db.flush()

            # RAC -> CNF upgrades Inventory already performed.
            promotions = _apply_inventory_promotions(db, inventory_promotions)

            # WL -> RAC/CNF upgrades this service performs into the room that
            # just opened up.
            promotions.extend(
                await _promote_waitlisted(
                    db, client, booking, rac_slots_available
                )
            )

            _resequence_waitlist(db, booking.train_id, booking.class_type)

            remaining = db.query(Passenger).filter(
                Passenger.booking_id == booking.id,
                Passenger.status != "CANCELLED"
            ).all()

            booking.status = "CANCELLED" if not remaining else _roll_up_status(remaining)

            db.commit()
            db.refresh(booking)

        return {
            "booking_id": booking.id,
            "status": booking.status,
            "cancelled": cancelled_rows,
            "promotions": promotions,
            "refund_status": "NOT_IMPLEMENTED_UNTIL_PHASE_10",
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception as error:

        db.rollback()

        print("Cancellation error:", error)

        raise HTTPException(
            status_code=500,
            detail=f"Cancellation failed: {error}"
        )

    finally:

        db.close()


@app.get("/bookings/{booking_id}/detail")
def get_booking_detail(booking_id: int):
    """
    Full per-seat view of a booking: every passenger, their berth, and any
    waiting-list position. The original GET /bookings/{id} is left alone so
    the Phase 1-5 response shape does not move under existing callers.
    """

    db = SessionLocal()

    try:

        booking = db.query(Booking).filter(Booking.id == booking_id).first()

        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")

        return _booking_payload(db, booking)

    finally:

        db.close()
