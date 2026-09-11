# ==========================================
# IRCTC Microservices API Health Check
# ==========================================

$tests = @(
    @{
        Name = "User Service"
        Url  = "http://localhost:8001/users"
    },
    @{
        Name = "Train Service"
        Url  = "http://localhost:8002/trains"
    },
    @{
        Name = "Inventory Service"
        Url  = "http://localhost:8003/availability/1"
    },
    @{
        Name = "Booking Service"
        Url  = "http://localhost:8004/bookings"
    },
    @{
        Name = "Payment Service"
        Url  = "http://localhost:8005/payments"
    },
    @{
        Name = "Notification Service"
        Url  = "http://localhost:8006/notifications"
    }
)

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " IRCTC Microservices API Health Check" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$passed = 0
$failed = 0

foreach ($test in $tests) {

    Write-Host "Checking $($test.Name)..." -ForegroundColor Yellow

    try {
        $response = Invoke-RestMethod `
            -Uri $test.Url `
            -Method Get `
            -TimeoutSec 10

        Write-Host "PASS  $($test.Name)" -ForegroundColor Green
        Write-Host "      URL: $($test.Url)" -ForegroundColor Gray
        Write-Host "      Response received successfully" -ForegroundColor Gray
        Write-Host ""

        $passed++
    }
    catch {
        Write-Host "FAIL  $($test.Name)" -ForegroundColor Red
        Write-Host "      URL: $($test.Url)" -ForegroundColor Gray
        Write-Host "      Error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""

        $failed++
    }
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Summary" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

Write-Host "Passed: $passed" -ForegroundColor Green
Write-Host "Failed: $failed" -ForegroundColor Red

if ($failed -eq 0) {
    Write-Host ""
    Write-Host "All API checks passed successfully." -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "Some API checks failed. Check the service logs." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Useful command:" -ForegroundColor Cyan
    Write-Host "docker compose logs --tail=100 <service-name>" -ForegroundColor Gray
}