// Wizard Flow State
let currentStep = 1;
let currentTrain = "12002";
let currentClass = "SL";
let passengerCount = 1;
let pendingBookingPayload = null;

// Dom Elements
const trainSelect = document.getElementById("train-select");
const classSelect = document.getElementById("class-select");
const seatsContainer = document.getElementById("seats-grid-container");
const displayCoachName = document.getElementById("display-coach-name");

// Stats elements
const statCnf = document.getElementById("stat-cnf");
const statRac = document.getElementById("stat-rac");
const statWl = document.getElementById("stat-wl");
const statVacant = document.getElementById("stat-vacant");

// Form & list elements
const bookingForm = document.getElementById("booking-form");
const passengerRows = document.getElementById("passenger-rows");
const btnAddPassenger = document.getElementById("btn-add-passenger");
const activeBookingsList = document.getElementById("active-bookings-list");
const waitlistTableBody = document.getElementById("waitlist-table-body");

// Modal elements
const ticketModal = document.getElementById("ticket-modal");
const ticketModalBody = document.getElementById("ticket-modal-body");
const btnCloseModal = document.getElementById("btn-close-modal");

// Initialization
function init() {
    setupEventListeners();
    loadTrainState();
    logConsole("SYSTEM", "Dashboard initialized in interactive wizard mode. Step 1 active.", "system");
}

function setupEventListeners() {
    // Selectors
    trainSelect.addEventListener("change", (e) => {
        currentTrain = e.target.value;
        logConsole("SELECTOR", `Active train updated: ${currentTrain}`, "system");
        loadTrainState();
    });
    
    classSelect.addEventListener("change", (e) => {
        currentClass = e.target.value;
        logConsole("SELECTOR", `Active class updated: ${currentClass}`, "system");
        loadTrainState();
    });

    // Passenger Rows add/remove
    btnAddPassenger.addEventListener("click", addPassengerRow);
    
    // Form submission (Step 3 Booking Form)
    bookingForm.addEventListener("submit", handleBookingSubmit);
    
    // Close Modal
    btnCloseModal.addEventListener("click", () => {
        ticketModal.classList.remove("active");
    });
}

// Wizard Navigation Engine
window.navigateToStep = function(stepNumber) {
    currentStep = stepNumber;
    
    // Manage active layout panel containers
    for (let s = 1; s <= 5; s++) {
        const contentPanel = document.getElementById(`step-${s}-content`);
        if (contentPanel) {
            contentPanel.classList.remove("active");
            if (s === stepNumber) {
                contentPanel.classList.add("active");
            }
        }
    }
    
    // Update Header Titles and Selectors depending on styling high-fi/low-fi shift
    const titles = {
        1: ["User Authentication", "Step 1: Low-fidelity unstyled browser mock for Group 2 Authentication"],
        2: ["Search Trains & Schedules", "Step 2: Low-fidelity unstyled browser mock for Group 2/3 Search Database"],
        3: ["Coach Seating", " "],
        4: ["Payment Gateway Checkout", "Step 4: Low-fidelity unstyled sandbox panel representing Group 6 checkout lifecycle"],
        5: ["Booking Confirmation Receipts", "Step 5: High-fidelity ticket review & GNWL Waitlist queue board (Group 4 Finished Product)"]
    };
    
    document.getElementById("current-tab-title").innerText = titles[stepNumber][0];
    document.getElementById("current-tab-subtitle").innerText = titles[stepNumber][1];
    
    const headerControl = document.getElementById("header-control-panel");
    if (stepNumber === 3 || stepNumber === 5) {
        headerControl.style.display = "flex";
    } else {
        headerControl.style.display = "none";
    }
    
    logConsole("WIZARD", `Transitioned to Step ${stepNumber}: ${titles[stepNumber][0]}`, "system");
};

// STEP 1 Form Actions (Login Auth)
window.submitStep1 = function(event) {
    event.preventDefault();
    logConsole("API_IN", "POST /auth/login - Processing credentials...", "api-in");
    logConsole("API_OUT", "Auth validation successful. Authorized JWT Token generated.", "api-out");
    navigateToStep(2);
};

// STEP 2 Form Actions (Search)
window.submitStep2 = function(event) {
    event.preventDefault();
    const src = document.getElementById("raw-src").value;
    const dest = document.getElementById("raw-dest").value;
    const classVal = document.getElementById("raw-class").value;
    
    logConsole("API_IN", `GET /search?src=${src}&dest=${dest}&class=${classVal} - Parsing cache...`, "api-in");
    logConsole("API_OUT", `Schedule found. Cache query execution latency: 2ms.`, "api-out");
    
    // Show unstyled table
    document.getElementById("raw-search-results").style.display = "block";
};

window.selectTrainAndProceed = function(trainNum) {
    currentTrain = trainNum;
    trainSelect.value = trainNum;
    
    const rawClass = document.getElementById("raw-class").value;
    currentClass = rawClass;
    classSelect.value = rawClass;
    
    logConsole("WIZARD", `Selected train ${trainNum} / Class ${rawClass}. Processing layout...`, "system");
    loadTrainState();
    
    navigateToStep(3);
};

// STEP 3 Form Actions (Reserve & Lock)
async function handleBookingSubmit(event) {
    event.preventDefault();
    
    const pCards = passengerRows.querySelectorAll(".passenger-row-card");
    const passengers = [];
    
    pCards.forEach(card => {
        passengers.push({
            name: card.querySelector(".p-name").value,
            age: parseInt(card.querySelector(".p-age").value),
            gender: card.querySelector(".p-gender").value,
            berth_preference: card.querySelector(".p-preference").value
        });
    });
    
    // Save pending payload before payment executes
    pendingBookingPayload = {
        train_number: currentTrain,
        class_type: currentClass,
        passengers: passengers
    };
    
    logConsole("REDIS_LOCK", `Temporary locking ${passengers.length} seat slots in Redis (TTL: 600s)...`, "db-log");
    
    // Populate payment summary text placeholders in Step 4 Raw UI
    document.getElementById("payment-summary-train").innerText = currentTrain;
    document.getElementById("payment-summary-class").innerText = currentClass;
    document.getElementById("payment-summary-count").innerText = passengers.length;
    
    navigateToStep(4);
}

// STEP 4 Webhook simulation actions
window.triggerMockPaymentWebhook = async function(status) {
    if (!pendingBookingPayload) {
        alert("Error: No pending booking payload active. Start from Step 3.");
        return;
    }
    
    logConsole("API_IN", `Incoming Webhook Callback [Stripe Event]: payment.status = ${status}`, "api-in");
    
    if (status === "FAILED") {
        logConsole("REDIS_LOCK", "Releasing temp locks in Redis. Database transaction discarded.", "db-log");
        alert("Simulated Webhook Payment FAILED. Redis locks released. Navigate back to Step 3.");
        navigateToStep(3);
        return;
    }
    
    logConsole("MOCK_GATEWAY", `Validating payment token... Upgrading Redis locks to permanent status.`, "api-out");
    
    // Call FastAPI Booking Seat Allocation Engine
    try {
        logConsole("API_IN", `POST /bookings/allocate - Data: ${JSON.stringify(pendingBookingPayload)}`, "api-in");
        
        const response = await fetch("/bookings/allocate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(pendingBookingPayload)
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Allocation failed");
        }
        
        const ticket = await response.json();
        logConsole("API_OUT", `Booking success! PNR generated: ${ticket.pnr}. Seat allocation matrix committed.`, "api-out");
        
        // Show Ticket Modal
        showTicketModal(ticket);
        
        // Reset Reservation forms
        bookingForm.reset();
        passengerRows.innerHTML = "";
        passengerCount = 0;
        addPassengerRow();
        
        // Reload layouts
        loadTrainState();
        
        navigateToStep(5);
        
    } catch (error) {
        logConsole("ERROR", `Allocation failed: ${error.message}`, "db-log");
        alert(`Error: ${error.message}`);
    }
};

window.restartBookingWizard = function() {
    logConsole("WIZARD", "Resetting transaction session context. Routing back to Step 2 search...", "system");
    navigateToStep(2);
};

// STEP 5: Ticket cancellation handler (Upgrades Waitlist Cascade)
window.handleCancelPNR = async function(bookingId, passengerIds) {
    if (!confirm("Are you sure you want to cancel these tickets? This will trigger Waitlist & RAC promotions.")) return;
    
    const payload = {
        booking_id: bookingId,
        passenger_ids: passengerIds
    };
    
    try {
        logConsole("API_IN", `POST /bookings/cancel - Data: ${JSON.stringify(payload)}`, "api-in");
        
        const response = await fetch("/bookings/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Cancellation failed");
        }
        
        const result = await response.json();
        logConsole("API_OUT", `Cancellation processed. PNR State: ${result.status}`, "api-out");
        
        // Log Cascading Promotions specifically in the dev console
        logConsole("PROMOTION", `Cascading waitlist upgrade trigger: GNWL ➔ RAC ➔ CNF state updated. Check Layout Map!`, "promotion");
        
        loadTrainState();
    } catch (error) {
        logConsole("ERROR", `Cancellation failed: ${error.message}`, "db-log");
        alert(`Error: ${error.message}`);
    }
}

// Fetch and Render State
async function loadTrainState() {
    try {
        const response = await fetch(`/bookings/state?train_number=${currentTrain}&class_type=${currentClass}`);
        const data = await response.json();
        
        if (displayCoachName) displayCoachName.innerText = `COACH ${data.coach_name || 'N/A'}`;
        
        // Stats Calculation
        let cnf = 0, rac = 0, vacant = 0;
        data.seats.forEach(s => {
            if (s.status === "BOOKED") cnf++;
            else if (s.status === "RAC_ONE") { rac++; vacant++; }
            else if (s.status === "RAC_FULL") { rac += 2; }
            else vacant++;
        });
        
        // Step 3 stats update
        if (statCnf) statCnf.innerText = cnf;
        if (statRac) statRac.innerText = rac;
        if (statWl) statWl.innerText = data.waitlist.length;
        if (statVacant) statVacant.innerText = vacant;
        
        // Step 5 stats update
        const sc5 = document.getElementById("stat-cnf-step5");
        const sr5 = document.getElementById("stat-rac-step5");
        const sw5 = document.getElementById("stat-wl-step5");
        const sv5 = document.getElementById("stat-vacant-step5");
        
        if (sc5) sc5.innerText = cnf;
        if (sr5) sr5.innerText = rac;
        if (sw5) sw5.innerText = data.waitlist.length;
        if (sv5) sv5.innerText = vacant;
        
        renderSeatMap(data.seats);
        renderBookingsList(data.active_bookings);
        renderWaitlistTable(data.waitlist);
        
    } catch (error) {
        logConsole("ERROR", `Failed to load state: ${error.message}`, "db-log");
    }
}

// Render Seat Grid Layout dynamically
function renderSeatMap(seats) {
    seatsContainer.innerHTML = "";
    const totalBays = Math.ceil(seats.length / 8);
    seatsContainer.style.gridTemplateColumns = `repeat(${totalBays * 3}, minmax(36px, 1fr))`;
    
    seats.forEach(s => {
        const bay = Math.floor((s.seat_number - 1) / 8);
        const remainder = (s.seat_number - 1) % 8;
        
        let row = 1;
        let col = 1;
        
        if (remainder === 2 || remainder === 5) row = 1; // Upper
        else if (remainder === 1 || remainder === 4) row = 2; // Middle
        else if (remainder === 0 || remainder === 3) row = 3; // Lower
        else if (remainder === 7) row = 1; // Side Upper
        else if (remainder === 6) row = 5; // Side Lower (RAC)
        
        const colOffset = bay * 3;
        if (remainder >= 0 && remainder <= 2) col = colOffset + 1;
        else if (remainder >= 3 && remainder <= 5) col = colOffset + 2;
        else if (remainder === 6 || remainder === 7) col = colOffset + 3;
        
        const seatDiv = document.createElement("div");
        seatDiv.className = `seat ${s.status.toLowerCase()}`;
        seatDiv.style.gridRow = row;
        seatDiv.style.gridColumn = col;
        
        if (s.status === "RAC_ONE") seatDiv.className = "seat rac";
        if (s.status === "RAC_FULL") seatDiv.className = "seat booked";
        
        seatDiv.innerHTML = `
            <span class="seat-num">${s.seat_number}</span>
            <span class="seat-type-label">${s.berth_type.slice(0, 3)}</span>
        `;
        
        seatDiv.addEventListener("click", () => {
            logConsole("SEAT_INSPECT", `Seat ${s.seat_number} (${s.berth_type}): Status: ${s.status}, Booking PNR ID: ${s.booking_id || 'None'}`, "db-log");
        });
        
        seatsContainer.appendChild(seatDiv);
    });
    
    for (let c = 1; c <= totalBays * 3; c++) {
        const gapDiv = document.createElement("div");
        gapDiv.className = "seats-grid-gap";
        gapDiv.style.gridRow = 4;
        gapDiv.style.gridColumn = c;
        seatsContainer.appendChild(gapDiv);
    }
}

// Render Reservation Form Rows
function addPassengerRow() {
    passengerCount++;
    const rowCard = document.createElement("div");
    rowCard.className = "passenger-row-card glass-card";
    rowCard.id = `passenger-row-${passengerCount}`;
    
    rowCard.innerHTML = `
        <div class="passenger-row-header">
            <h4>Passenger #${passengerCount}</h4>
            <button type="button" class="btn-remove-passenger" onclick="removePassengerRow(${passengerCount})"><i class="fa-solid fa-trash"></i></button>
        </div>
        <div class="form-row">
            <div class="form-group flex-2">
                <label>Name</label>
                <input type="text" class="p-name" placeholder="Full Name" required>
            </div>
            <div class="form-group">
                <label>Age</label>
                <input type="number" class="p-age" min="1" max="120" placeholder="Age" required>
            </div>
            <div class="form-group">
                <label>Gender</label>
                <select class="p-gender">
                    <option value="M">Male</option>
                    <option value="F">Female</option>
                    <option value="O">Other</option>
                </select>
            </div>
            <div class="form-group flex-2">
                <label>Preference</label>
                <select class="p-preference">
                    <option value="NONE">No Preference</option>
                    <option value="LOWER">Lower Berth</option>
                    <option value="MIDDLE">Middle Berth</option>
                    <option value="UPPER">Upper Berth</option>
                    <option value="SIDE_LOWER">Side Lower</option>
                    <option value="SIDE_UPPER">Side Upper</option>
                </select>
            </div>
        </div>
    `;
    
    passengerRows.appendChild(rowCard);
    document.querySelectorAll(".btn-remove-passenger").forEach(btn => btn.style.display = "block");
}

window.removePassengerRow = function(id) {
    const row = document.getElementById(`passenger-row-${id}`);
    if (row) {
        row.remove();
        passengerCount--;
        reorderPassengerLabels();
    }
}

function reorderPassengerLabels() {
    const cards = passengerRows.querySelectorAll(".passenger-row-card");
    cards.forEach((card, index) => {
        const num = index + 1;
        card.id = `passenger-row-${num}`;
        card.querySelector("h4").innerText = `Passenger #${num}`;
        const removeBtn = card.querySelector(".btn-remove-passenger");
        if (removeBtn) {
            removeBtn.setAttribute("onclick", `removePassengerRow(${num})`);
            removeBtn.style.display = cards.length > 1 ? "block" : "none";
        }
    });
}

// Show Ticket modal dialog details
function showTicketModal(ticket) {
    ticketModalBody.innerHTML = `
        <div class="ticket-pnr-info">
            <span class="stat-label">PNR NUMBER</span>
            <h2>${ticket.pnr}</h2>
            <span class="stat-label" style="margin-top: 10px; display:block;">Booking ID: ${ticket.booking_id}</span>
        </div>
        <div class="ticket-passengers-list">
            ${ticket.passengers.map(p => `
                <div class="ticket-passenger-card">
                    <div>
                        <h4>${p.name}</h4>
                        <p>Status: <span class="booking-status-tag ${p.status.toLowerCase()}">${p.status}</span></p>
                    </div>
                    <div style="text-align: right;">
                        <span class="p-seat-details">${p.coach_name ? `${p.coach_name} / Seat ${p.seat_number}` : 'GNWL Queue'}</span>
                        <p>${p.berth_allocated || 'Waitlisted'}</p>
                    </div>
                </div>
            `).join('')}
        </div>
    `;
    ticketModal.classList.add("active");
}

// Render active bookings board on sidebar panel
function renderBookingsList(bookings) {
    activeBookingsList.innerHTML = "";
    if (bookings.length === 0) {
        activeBookingsList.innerHTML = `
            <div class="empty-state">
                <i class="fa-solid fa-folder-open"></i>
                <p>No active bookings found on this train/class.</p>
            </div>
        `;
        return;
    }
    
    bookings.forEach(b => {
        const bCard = document.createElement("div");
        bCard.className = "booking-card";
        
        bCard.innerHTML = `
            <div class="booking-card-header">
                <span class="pnr-tag"><i class="fa-solid fa-ticket"></i> PNR: ${b.pnr}</span>
                <span class="booking-status-tag ${b.status.toLowerCase()}">${b.status}</span>
            </div>
            <div class="booking-card-passengers">
                ${b.passengers.map(p => `
                    <div class="passenger-seat-item">
                        <span>${p.name} (${p.status})</span>
                        <span class="p-seat-details">${p.coach_name ? `${p.coach_name} / Seat ${p.seat_number}` : 'Waitlist'}</span>
                    </div>
                `).join('')}
            </div>
            <div class="booking-card-actions">
                <button class="btn-cancel-pnr" onclick="handleCancelPNR('${b.booking_id}', [${b.passengers.map(p=>p.id).join(',')}])">
                    <i class="fa-solid fa-ban"></i> Cancel Ticket
                </button>
            </div>
        `;
        activeBookingsList.appendChild(bCard);
    });
}

// Render Waitlist Queue Board
function renderWaitlistTable(waitlist) {
    waitlistTableBody.innerHTML = "";
    if (waitlist.length === 0) {
        waitlistTableBody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center py-4 text-muted" style="text-align: center;">
                    No passengers currently in the waiting queue.
                </td>
            </tr>
        `;
        return;
    }
    
    waitlist.forEach(w => {
        const tr = document.createElement("tr");
        
        tr.innerHTML = `
            <td><div class="priority-badge">${w.priority}</div></td>
            <td><strong>${w.passenger_name}</strong></td>
            <td>Age: ${w.age} / ${w.gender}</td>
            <td><span class="booking-status-tag wl">WL</span></td>
            <td>${w.queue_type}</td>
            <td>
                <button class="btn-cancel-pnr" onclick="handleCancelPNR('', [${w.passenger_id}])">Cancel</button>
            </td>
        `;
        waitlistTableBody.appendChild(tr);
    });
}

// Log line wrapper for state engine logs
function logConsole(module, message, level) {
    const timestamp = new Date().toLocaleTimeString();
    console.log(`[${timestamp}] [${module}] [${level.toUpperCase()}] ${message}`);
}

// Run Startup
document.addEventListener("DOMContentLoaded", init);
