/* static/js/dashboard/summary.js */
/* Front-end logic for the Call Summary page. */

let table = null;
let currentResults = [];
let currentSystemName = "";
let currentDateFrom = "";
let currentDateTo = "";

const els = {};

function esc(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function formatDateTime(epoch) {
    if (!epoch) return "—";
    return new Date(epoch * 1000).toLocaleString();
}

function showAlert(message, type) {
    const container = document.getElementById("toastContainer");
    if (!container) return;
    const id = "toast-" + Math.random().toString(36).slice(2);
    const html = `
        <div id="${id}" class="toast align-items-center text-bg-${type} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">${esc(message)}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;
    container.insertAdjacentHTML("beforeend", html);
    const el = document.getElementById(id);
    const toast = new bootstrap.Toast(el, { delay: 5000 });
    toast.show();
    el.addEventListener("hidden.bs.toast", () => el.remove());
}

async function runQuery() {
    const systemId = els.systemSelect.value;
    const dateFrom = els.dateFrom.value;
    const dateTo = els.dateTo.value;

    if (!systemId) {
        showAlert("Please select a radio system.", "warning");
        return;
    }
    if (!dateFrom || !dateTo) {
        showAlert("Please select both From and To dates.", "warning");
        return;
    }
    if (dateFrom > dateTo) {
        showAlert("'From' date cannot be after 'To' date.", "warning");
        return;
    }

    currentSystemName = els.systemSelect.options[els.systemSelect.selectedIndex].text;
    currentDateFrom = dateFrom;
    currentDateTo = dateTo;

    els.loader.style.display = "flex";
    try {
        const params = new URLSearchParams({
            radio_system_id: systemId,
            date_from: dateFrom,
            date_to: dateTo
        });
        const resp = await fetch(`/api/summary/calls?${params}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.message || "API error");

        currentResults = data.result || [];
        renderTable();
    } catch (err) {
        console.error(err);
        showAlert("Failed to load summary: " + err.message, "danger");
    } finally {
        els.loader.style.display = "none";
    }
}

function renderTable() {
    els.initialMessage.classList.add("d-none");

    if (!currentResults.length) {
        els.tableWrapper.classList.add("d-none");
        els.noResults.classList.remove("d-none");
        els.exportBtn.classList.add("d-none");
        els.statsRow.classList.add("d-none");
        return;
    }

    els.noResults.classList.add("d-none");
    els.tableWrapper.classList.remove("d-none");
    els.exportBtn.classList.remove("d-none");
    els.statsRow.classList.remove("d-none");

    els.resultCount.textContent = currentResults.length;
    els.systemLabel.textContent = currentSystemName;
    els.rangeLabel.textContent = `${currentDateFrom} → ${currentDateTo}`;

    const rows = currentResults.map(r => {
        const callId = r.call_id;
        const transcript = r.transcript || "";
        const shortTranscript = transcript.length > 120 ? transcript.substring(0, 120) + "…" : transcript;

        return [
            formatDateTime(r.start_epoch),
            esc(r.township),
            esc(r.incident_category || "—"),
            `<span class="transcript-cell" data-full="${esc(transcript)}">${esc(shortTranscript)}</span>`,
            callId
        ];
    });

    if (table) {
        table.clear().rows.add(rows).draw(false);
    } else {
        table = new DataTable("#summaryTable", {
            responsive: { details: false },
            autoWidth: false,
            order: [[0, "desc"]],
            pageLength: 100,
            lengthMenu: [25, 50, 100, 250, 500, 1000],
            columnDefs: [
                { targets: 0, width: "150px" },
                { targets: 1, width: "180px" },
                { targets: 2, width: "100px" },
                { targets: 3, width: "auto" },
                { targets: 4, visible: false }
            ]
        });
        table.clear().rows.add(rows).draw(false);
    }
}

function exportToText() {
    if (!currentResults.length) return;

    const now = new Date().toLocaleString();
    const lines = [
        "Call Summary Report",
        "=".repeat(50),
        `System: ${currentSystemName}`,
        `Date Range: ${currentDateFrom} to ${currentDateTo}`,
        `Total Calls: ${currentResults.length}`,
        `Generated: ${now}`,
        "=".repeat(50),
        ""
    ];

    currentResults.forEach((r, idx) => {
        lines.push(`--- Call ${idx + 1} ---`);
        lines.push(`Call ID: ${r.call_id}`);
        lines.push(`Date/Time: ${formatDateTime(r.start_epoch)}`);
        lines.push(`Township: ${r.township}`);
        lines.push(`Incident Category: ${r.incident_category || "—"}`);
        lines.push("Transcript:");
        lines.push(r.transcript || "—");
        lines.push("");
    });

    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const safeSystem = currentSystemName.replace(/[^a-zA-Z0-9]/g, "_");
    a.download = `call_summary_${safeSystem}_${currentDateFrom}_to_${currentDateTo}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function toggleTranscript(e) {
    const cell = e.target.closest(".transcript-cell");
    if (!cell) return;
    const isExpanded = cell.classList.contains("transcript-expanded");
    if (isExpanded) {
        const full = cell.getAttribute("data-full") || "";
        const short = full.length > 120 ? full.substring(0, 120) + "…" : full;
        cell.textContent = short;
        cell.classList.remove("transcript-expanded");
    } else {
        const full = cell.getAttribute("data-full") || "";
        cell.textContent = full;
        cell.classList.add("transcript-expanded");
    }
}

function initSummaryPage() {
    Object.assign(els, {
        loader: document.querySelector(".page-loader"),
        systemSelect: document.getElementById("systemSelect"),
        dateFrom: document.getElementById("dateFrom"),
        dateTo: document.getElementById("dateTo"),
        runQueryBtn: document.getElementById("runQueryBtn"),
        clearFilters: document.getElementById("clearFilters"),
        exportBtn: document.getElementById("exportBtn"),
        statsRow: document.getElementById("statsRow"),
        resultCount: document.getElementById("resultCount"),
        systemLabel: document.getElementById("systemLabel"),
        rangeLabel: document.getElementById("rangeLabel"),
        tableWrapper: document.getElementById("summaryTable"),
        noResults: document.getElementById("noResults"),
        initialMessage: document.getElementById("initialMessage")
    });

    if (els.runQueryBtn) {
        els.runQueryBtn.addEventListener("click", runQuery);
    }

    if (els.clearFilters) {
        els.clearFilters.addEventListener("click", () => {
            els.systemSelect.value = "";
            els.dateFrom.value = "";
            els.dateTo.value = "";
            currentResults = [];
            if (table) {
                table.clear().draw(false);
            }
            els.tableWrapper.classList.add("d-none");
            els.noResults.classList.add("d-none");
            els.initialMessage.classList.remove("d-none");
            els.exportBtn.classList.add("d-none");
            els.statsRow.classList.add("d-none");
        });
    }

    if (els.exportBtn) {
        els.exportBtn.addEventListener("click", exportToText);
    }

    // Click transcript cells to expand/collapse
    const tableEl = document.getElementById("summaryTable");
    if (tableEl) {
        tableEl.addEventListener("click", toggleTranscript);
    }

    // Allow Enter key on date inputs to trigger query
    [els.dateFrom, els.dateTo].forEach(el => {
        if (el) {
            el.addEventListener("keydown", (e) => {
                if (e.key === "Enter") runQuery();
            });
        }
    });
}

document.addEventListener("DOMContentLoaded", initSummaryPage);
