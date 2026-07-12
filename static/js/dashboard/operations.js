const metricLabels = {
    calls_received: "Calls received", calls_transcribed: "Transcribed", addresses_extracted: "Addresses extracted",
    addresses_geocoded: "Geocoded", geocode_pending: "Need geocoding", corrections_applied: "Manual corrections"
};

function operationsCsrf() { return document.getElementById("csrfToken")?.value || ""; }
function operationsEscape(value) { const el = document.createElement("span"); el.textContent = String(value || ""); return el.innerHTML; }
function operationsTime(epoch) { return epoch ? new Date(epoch * 1000).toLocaleString() : "-"; }

async function loadOperations() {
    const hours = document.getElementById("windowHours").value;
    const response = await fetch(`/api/operations/status?hours=${encodeURIComponent(hours)}`);
    const data = await response.json();
    if (!data.success) throw new Error(data.message || "Could not load operations status");
    const payload = data.result;
    document.getElementById("operationsMetrics").innerHTML = Object.entries(metricLabels).map(([key, label]) => `
        <div class="col-6 col-md-4 col-xl-2"><div class="ap-page-stat"><span>${label}</span><strong>${payload.metrics[key] || 0}</strong></div></div>`).join("");
    document.getElementById("retryCount").textContent = `${payload.retry_candidates.length} actionable calls`;
    document.getElementById("retryRows").innerHTML = payload.retry_candidates.length ? payload.retry_candidates.map(call => `
        <tr><td>${operationsTime(call.start_epoch)}</td><td>${operationsEscape(call.system_name || "-")}</td><td>${operationsEscape(call.talkgroup_name || "-")}</td><td class="text-truncate" style="max-width:28rem">${operationsEscape(call.transcript || "-")}</td><td class="text-end"><button class="btn btn-sm btn-outline-warning retry-geocode" data-call-id="${call.call_id}">Retry</button> <a class="btn btn-sm btn-outline-secondary" href="/dashboard/corrections?call_id=${call.call_id}">Review</a></td></tr>`).join("") : '<tr><td colspan="5" class="text-muted">No address retry candidates in this period.</td></tr>';
    document.querySelectorAll(".retry-geocode").forEach(button => button.addEventListener("click", retryGeocoding));
}

async function retryGeocoding(event) {
    const button = event.currentTarget;
    button.disabled = true;
    const response = await fetch(`/api/operations/calls/${button.dataset.callId}/retry-geocoding`, { method: "POST", headers: { "X-CSRFToken": operationsCsrf() }, body: JSON.stringify({ _csrf_token: operationsCsrf() }) });
    const data = await response.json();
    if (!data.success) alert(data.message || "Retry failed");
    await loadOperations();
}

document.addEventListener("DOMContentLoaded", () => {
    loadOperations().catch(error => { document.getElementById("retryRows").innerHTML = `<tr><td colspan="5" class="text-danger">${operationsEscape(error.message)}</td></tr>`; });
    document.getElementById("windowHours").addEventListener("change", () => loadOperations().catch(console.error));
    document.getElementById("refreshOperations").addEventListener("click", () => loadOperations().catch(console.error));
});
