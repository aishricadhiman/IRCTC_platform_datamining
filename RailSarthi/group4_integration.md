# Group 4 Integration Contract: Inputs & Outputs

This document defines the interface boundaries for **Group 4 (Seat Allocation & Waitlist Management)**. It lists what Group 4 needs from preceding groups (inputs) and what Group 4 delivers to succeeding groups (outputs).

---

## 📥 Inputs Needed from Previous Groups

For development, Group 4 will mock these models and schemas. In the final integrated pipeline, these must be provided by the respective groups.

### 1. Database Schema & Data Models (From Group 1)
We require the following tables/relationships to be defined in PostgreSQL:
*   **`Train`:** General route and availability schedule.
*   **`Coach`:** Mappings of coaches to trains (e.g., S1, S2, B1, B2) and class categories (SL, 3A, 2A, 1A).
*   **`Seat`:** Individual berth status matrix.
    *   *Required Fields:* `id`, `coach_id`, `seat_number`, `berth_type` (`LOWER`, `MIDDLE`, `UPPER`, `SIDE_LOWER`, `SIDE_UPPER`), `status` (`VACANT`, `LOCKED`, `BOOKED`).
*   **`Passenger`:** Individual passenger information.
    *   *Required Fields:* `id`, `name`, `age`, `gender`, `berth_preference`.

### 2. Lock Verification & Concurrency Control (From Group 5 & Group 3)
Before we assign a seat permanently, we must confirm it is temporarily reserved for the current transaction:
*   **Redis Temporary Lock Interface:** Group 3/5 must provide a Redis-based lock check.
    *   *Key Pattern:* `lock:seat:{train_id}:{date}:{seat_id}`
    *   *TTL:* 10 minutes (held during the payment cycle).
*   **Concurrency Wrapper:** A thread-safe transactional wrapper or pessimistic locking utility to prevent race conditions during queries (`SELECT ... FOR UPDATE` or `Redlock`).

### 3. Payment Status Events (From Group 6)
We need a triggering event to finalize the booking:
*   **`payment.success` Event Payload:**
    ```json
    {
      "booking_id": "string",
      "payment_id": "string",
      "status": "PAID"
    }
    ```

---

## 📤 Outputs Given to Next Groups

These are the APIs, data structures, and events that Group 4 delivers to the rest of the application.

### 1. Seat Allocation Details (To Group 6 / Payment & Frontend)
Upon calling `/bookings/allocate` after a successful payment, Group 4 returns:
*   **Seat Allocation Payload:**
    ```json
    {
      "booking_id": "string",
      "status": "SUCCESS",
      "passengers": [
        {
          "passenger_id": "string",
          "status": "CNF",
          "coach": "S1",
          "seat_number": 24,
          "berth_type": "LOWER"
        },
        {
          "passenger_id": "string",
          "status": "RAC",
          "coach": "S1",
          "seat_number": 7,
          "berth_type": "SIDE_LOWER"
        }
      ]
    }
    ```

### 2. Waitlist Assignment Details (To Group 6 / Frontend)
If no berths are vacant and the passenger joins the queue, we return the waitlist state:
*   **Waitlist Payload:**
    ```json
    {
      "booking_id": "string",
      "status": "WAITLISTED",
      "waitlist_details": {
        "waitlist_type": "GNWL",
        "current_position": 14,
        "booking_position": 25
      }
    }
    ```

### 3. Asynchronous Events for Notification (To Group 3 / Messaging)
When ticket states change (especially during cancellations), Group 4 publishes events to RabbitMQ:
*   **`booking.confirmed`:** Dispatched when a seat is successfully allocated.
*   **`booking.waitlisted`:** Dispatched when a passenger joins the waitlist queue.
*   **`waitlist.upgraded`:** Dispatched during cancellation when a waitlisted passenger gets upgraded to RAC or CNF.
    *   *Payload:*
        ```json
        {
          "booking_id": "string",
          "passenger_id": "string",
          "old_status": "WL",
          "new_status": "CNF",
          "coach": "S2",
          "seat_number": 12,
          "berth_type": "MIDDLE"
        }
        ```

### 4. Release Inventory Event (To Group 5 / Inventory)
*   **`seat.released`:** Fired on ticket cancellation to notify Group 5 to increment the vacant seat count.
