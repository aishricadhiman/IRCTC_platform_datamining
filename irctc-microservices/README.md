# IRCTC Core Microservices System

A Dockerized IRCTC-style backend consisting of six independent FastAPI microservices. Each service has its own PostgreSQL database and communicates with other services through HTTP APIs.

## Project Scope

This project implements the core backend microservices for an IRCTC system:

- User management
- Train management
- Seat inventory management
- Booking management
- Payment processing simulation
- Notification management

Frontend/UI, forecasting, advanced reservation quotas, Redis synchronization, and real payment-gateway integration are outside the current core-microservices scope.

## Architecture

```text
User Service          :8001  -> User PostgreSQL
Train Service         :8002  -> Train PostgreSQL
Inventory Service     :8003  -> Inventory PostgreSQL
Booking Service       :8004  -> Booking PostgreSQL
Payment Service       :8005  -> Payment PostgreSQL
Notification Service  :8006  -> Notification PostgreSQL
```

### Booking Workflow

```text
Create Booking
      |
      v
Reserve Seat in Inventory Service
      |
      v
Process Payment with Idempotency Key
      |
      v
Confirm Booking
      |
      v
Create Notification
```

If payment fails, the Booking Service attempts to release the reserved seat and marks the booking as failed.

## Technology Stack

- Python
- FastAPI
- Uvicorn
- SQLAlchemy
- PostgreSQL 16
- Docker
- Docker Compose
- HTTPX
- Swagger/OpenAPI
- PowerShell or Bash

## Project Structure

```text
irctc-microservices/
├── docker-compose.yml
├── README.md
├── user-service/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── train-service/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── inventory-service/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── booking-service/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── payment-service/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
└── notification-service/
    ├── app.py
    ├── requirements.txt
    └── Dockerfile
```

## Service URLs and Swagger Links

| Service | API Base URL | Swagger UI |
|---|---|---|
| User Service | http://localhost:8001 | http://localhost:8001/docs |
| Train Service | http://localhost:8002 | http://localhost:8002/docs |
| Inventory Service | http://localhost:8003 | http://localhost:8003/docs |
| Booking Service | http://localhost:8004 | http://localhost:8004/docs |
| Payment Service | http://localhost:8005 | http://localhost:8005/docs |
| Notification Service | http://localhost:8006 | http://localhost:8006/docs |

## PostgreSQL Databases

| Database Container | Host Port | Database Name |
|---|---:|---|
| `irctc-user-db` | 5432 | `irctc_users` |
| `irctc-train-db` | 5433 | `irctc_trains` |
| `irctc-inventory-db` | 5434 | `irctc_inventory` |
| `irctc-booking-db` | 5435 | `irctc_bookings` |
| `irctc-payment-db` | 5436 | `irctc_payments` |
| `irctc-notification-db` | 5437 | `irctc_notifications` |

Default local-development credentials:

```text
Username: postgres
Password: postgres
```

## Prerequisites

Install:

- Docker Desktop
- Docker Compose
- Git, if cloning the repository

Verify the installation:

### Bash

```bash
docker --version
docker compose version
```

### PowerShell

```powershell
docker --version
docker compose version
```

## Start the System

Run these commands from the directory containing `docker-compose.yml`:

### Bash

```bash
docker compose up -d
```

### PowerShell

```powershell
docker compose up -d
```

Check the containers:

```bash
docker compose ps
```

All PostgreSQL containers should be `healthy`, and all application containers should be `Up`.

## Stop the System

Stop containers without removing them:

```bash
docker compose stop
```

Stop and remove containers and network:

```bash
docker compose down
```

Stop and remove containers, network, and database volumes:

```bash
docker compose down -v
```

> Warning: `docker compose down -v` deletes database volumes and permanently removes stored PostgreSQL data.

## Build and Rebuild Services

Build one service:

```bash
docker compose build booking-service
```

Restart one service:

```bash
docker compose up -d booking-service
```

Build all services:

```bash
docker compose build
docker compose up -d
```

Build without cache:

```bash
docker compose build --no-cache
docker compose up -d
```

## Logs

```bash
docker compose logs booking-service
docker compose logs --tail=100 booking-service
docker compose logs -f booking-service
```

Other services:

```bash
docker compose logs --tail=100 user-service
docker compose logs --tail=100 train-service
docker compose logs --tail=100 inventory-service
docker compose logs --tail=100 payment-service
docker compose logs --tail=100 notification-service
```

View all logs:

```bash
docker compose logs -f
```

Use Compose service names instead of assuming container names:

```bash
docker compose logs booking-service
```

## API Verification

FastAPI Swagger documentation is available at each `/docs` URL listed above.

### User Service

Get all users:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8001/users" `
    -Method Get
```

Get a user:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8001/users/1" `
    -Method Get
```

Create a user. Use the exact fields displayed by Swagger if your schema differs:

```powershell
$userBody = @{
    name = "Shubham"
    email = "shubham@example.com"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8001/users" `
    -Method Post `
    -ContentType "application/json" `
    -Body $userBody
```

### Train Service

Get all trains:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8002/trains" `
    -Method Get
```

Get a train:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8002/trains/1" `
    -Method Get
```

Create a train. Use the exact fields displayed by Swagger if your schema differs:

```powershell
$trainBody = @{
    train_number = "12345"
    train_name = "IRCTC Express"
    source = "Delhi"
    destination = "Lucknow"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8002/trains" `
    -Method Post `
    -ContentType "application/json" `
    -Body $trainBody
```

### Inventory Service

Check availability:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8003/availability/1" `
    -Method Get
```

Create inventory:

```powershell
$inventoryBody = @{
    train_id = 1
    total_seats = 100
    available_seats = 100
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8003/inventory" `
    -Method Post `
    -ContentType "application/json" `
    -Body $inventoryBody
```

Reserve one seat:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8003/reserve/1" `
    -Method Post
```

Release one seat:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8003/release/1" `
    -Method Post
```

### Booking Service

Get all bookings:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8004/bookings" `
    -Method Get
```

Get a booking:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8004/bookings/1" `
    -Method Get
```

Create a booking:

```powershell
$bookingBody = @{
    user_id = 1
    train_id = 1
    passenger_name = "Shubham"
    amount = 750
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8004/bookings" `
    -Method Post `
    -ContentType "application/json" `
    -Body $bookingBody
```

### Payment Service

Create a payment with an idempotency key:

```powershell
$paymentBody = @{
    booking_id = 1
    amount = 750
    idempotency_key = "manual-test-payment-1"
} | ConvertTo-Json

$paymentResponse = Invoke-RestMethod `
    -Uri "http://localhost:8005/payments" `
    -Method Post `
    -ContentType "application/json" `
    -Body $paymentBody

$paymentResponse
```

Send the same request again to test idempotency:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8005/payments" `
    -Method Post `
    -ContentType "application/json" `
    -Body $paymentBody
```

The second request should return the previously processed payment rather than creating a duplicate payment.

Get all payments:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8005/payments" `
    -Method Get
```

Get a payment by ID:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8005/payments/<payment_id>" `
    -Method Get
```

### Notification Service

Create a notification:

```powershell
$notificationBody = @{
    user_id = 1
    booking_id = 1
    message = "Your booking has been confirmed."
    notification_type = "BOOKING_CONFIRMED"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8006/notifications" `
    -Method Post `
    -ContentType "application/json" `
    -Body $notificationBody
```

Get all notifications:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8006/notifications" `
    -Method Get
```

Get notifications for a user:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8006/notifications/user/1" `
    -Method Get
```

Get a notification by ID:

```powershell
Invoke-RestMethod `
    -Uri "http://localhost:8006/notifications/1" `
    -Method Get
```

## Complete Booking Flow Test

Make sure user ID `1`, train ID `1`, and inventory for train ID `1` exist.

### 1. Check initial availability

```powershell
Invoke-RestMethod http://localhost:8003/availability/1
```

### 2. Create a booking

```powershell
$bookingBody = @{
    user_id = 1
    train_id = 1
    passenger_name = "Shubham"
    amount = 750
} | ConvertTo-Json

$bookingResponse = Invoke-RestMethod `
    -Uri "http://localhost:8004/bookings" `
    -Method Post `
    -ContentType "application/json" `
    -Body $bookingBody

$bookingResponse
```

### 3. Verify the booking

```powershell
Invoke-RestMethod http://localhost:8004/bookings
```

### 4. Verify payment

```powershell
Invoke-RestMethod http://localhost:8005/payments
```

### 5. Verify notification

```powershell
Invoke-RestMethod http://localhost:8006/notifications
```

### 6. Verify inventory again

```powershell
Invoke-RestMethod http://localhost:8003/availability/1
```

Expected result:

```text
Booking status    = CONFIRMED
Payment status    = SUCCESS
Notification      = BOOKING_CONFIRMED
Available seats   = Initial seats - 1
```

## Single PowerShell API Check Script

Create a file named `check-apis.ps1`:

```powershell
$tests = @(
    @{ Name = "User Service";         Url = "http://localhost:8001/users" },
    @{ Name = "Train Service";        Url = "http://localhost:8002/trains" },
    @{ Name = "Inventory Service";    Url = "http://localhost:8003/availability/1" },
    @{ Name = "Booking Service";      Url = "http://localhost:8004/bookings" },
    @{ Name = "Payment Service";      Url = "http://localhost:8005/payments" },
    @{ Name = "Notification Service"; Url = "http://localhost:8006/notifications" }
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " IRCTC Microservices API Health Check" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$passed = 0
$failed = 0

foreach ($test in $tests) {
    Write-Host "Checking $($test.Name)..." -ForegroundColor Yellow

    try {
        Invoke-RestMethod `
            -Uri $test.Url `
            -Method Get `
            -TimeoutSec 10 | Out-Null

        Write-Host "PASS  $($test.Name)" -ForegroundColor Green
        $passed++
    }
    catch {
        Write-Host "FAIL  $($test.Name)" -ForegroundColor Red
        Write-Host "      $($_.Exception.Message)" -ForegroundColor Red
        $failed++
    }
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Passed: $passed" -ForegroundColor Green
Write-Host "Failed: $failed" -ForegroundColor Red

if ($failed -eq 0) {
    Write-Host "All API checks passed successfully." -ForegroundColor Green
}
else {
    Write-Host "Some checks failed. Review Docker logs." -ForegroundColor Yellow
}
```

Run it in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\check-apis.ps1
```

## Database Verification

Each service uses a separate PostgreSQL database.

### User Database

```bash
docker exec -it irctc-user-db psql -U postgres -d irctc_users
```

```sql
\dt
SELECT * FROM users;
\q
```

### Train Database

```bash
docker exec -it irctc-train-db psql -U postgres -d irctc_trains
```

```sql
\dt
SELECT * FROM trains;
\q
```

### Inventory Database

```bash
docker exec -it irctc-inventory-db psql -U postgres -d irctc_inventory
```

```sql
\dt
SELECT * FROM inventory;
\q
```

### Booking Database

```bash
docker exec -it irctc-booking-db psql -U postgres -d irctc_bookings
```

```sql
\dt
SELECT * FROM bookings;
\q
```

### Payment Database

```bash
docker exec -it irctc-payment-db psql -U postgres -d irctc_payments
```

```sql
\dt
SELECT * FROM payments;
\q
```

### Notification Database

```bash
docker exec -it irctc-notification-db psql -U postgres -d irctc_notifications
```

```sql
\dt
SELECT * FROM notifications;
\q
```

> The `notifications` table exists in `irctc_notifications`, not in `irctc_users`.

## Persistence Test

Check records:

```powershell
Invoke-RestMethod http://localhost:8004/bookings
Invoke-RestMethod http://localhost:8005/payments
Invoke-RestMethod http://localhost:8006/notifications
```

Restart the system:

```bash
docker compose restart
```

Check the records again. They should remain because PostgreSQL data is stored in Docker volumes.

## Troubleshooting

### `404 Not Found` on `/`

A service may not define a root route. A 404 on `/` does not mean that the service is down. Test the actual endpoint or open `/docs`.

Examples:

```powershell
Invoke-RestMethod http://localhost:8001/users
Invoke-RestMethod http://localhost:8002/trains
Invoke-RestMethod http://localhost:8004/bookings
```

### `No such container`

Use Compose service names:

```bash
docker compose logs booking-service
```

List actual container names:

```bash
docker ps
```

### `422 Unprocessable Entity`

The request body does not match the API schema. Check Swagger for required fields, field names, and data types. In PowerShell, convert the body to JSON using `ConvertTo-Json`.

### `Payment failed`

Check:

```bash
docker compose logs --tail=100 booking-service
docker compose logs --tail=100 payment-service
```

Confirm that the payment request contains `booking_id`, `amount`, and `idempotency_key`.

### Database connection errors

```bash
docker compose ps
docker compose logs --tail=100 payment-db
docker compose logs --tail=100 notification-db
```

Restart the affected service:

```bash
docker compose restart payment-service
docker compose restart notification-service
```

## Implemented Features

- Six independent FastAPI microservices
- Docker Compose orchestration
- Independent PostgreSQL database for each service
- User management
- Train management
- Seat availability management
- Seat reservation and release
- Booking creation and retrieval
- Payment processing simulation
- Payment idempotency
- Notification creation and history
- HTTP-based service-to-service communication
- SQLAlchemy database access
- PostgreSQL health checks
- Swagger API documentation

## Current Limitations and Future Enhancements

The following features can be implemented by other teams or in future iterations:

- Frontend/UI
- JWT authentication and authorization
- API Gateway
- Redis caching and distributed locking
- Kafka-based asynchronous communication
- Advanced quotas such as GNWL, RLWL, PQWL, and RAC
- Detailed coach and seat allocation
- Passenger-level booking
- Train runs and station management
- Cancellation and refund services
- Real payment gateway integration
- Forecasting model
- Monitoring and centralized logging
- CI/CD
- Kubernetes deployment

## Team Handoff Summary

The core microservices team implemented and containerized six IRCTC backend services: User, Train, Inventory, Booking, Payment, and Notification. Each service uses an independent PostgreSQL database. The services communicate through HTTP, and the main booking workflow integrates inventory reservation, payment processing, booking confirmation, and notification creation. Payment idempotency is implemented to prevent duplicate payment processing.

The system is ready for integration with frontend, Redis, forecasting, ER-model enhancement, payment gateway, and other project teams.

## License

This project is developed for educational and academic project purposes.
