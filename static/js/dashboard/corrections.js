/* static/js/dashboard/corrections.js */
/* Admin call location correction tool */

let map = null;
let marker = null;
let callsData = [];
let selectedCallId = null;
let draggedLat = null;
let draggedLon = null;
let dirty = false; // becomes true only when the user actually changes the location

// Default map center. Can be overridden by a #mapDefaults element with
// data-lat / data-lng / data-zoom attributes (set per-deployment in the template).
function getDefaultCenter() {
    const el = document.getElementById("mapDefaults");
    const lat = parseFloat(el?.dataset.lat);
    const lng = parseFloat(el?.dataset.lng);
    if (!Number.isNaN(lat) && !Number.isNaN(lng)) return [lat, lng];
    return [45.4215, -75.6972]; // neutral fallback (Ottawa, ON)
}

function getDefaultZoom() {
    const el = document.getElementById("mapDefaults");
    const z = parseInt(el?.dataset.zoom, 10);
    return Number.isNaN(z) ? 8 : z;
}

const NOMINATIM_URL = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=";

function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function markDirty() {
    dirty = true;
    const btn = document.getElementById("saveBtn");
    if (btn) btn.disabled = false;
}

// Simple debounce helper.
function debounce(fn, wait) {
    let t;
    return function (...args) {
        clearTimeout(t);
        t = setTimeout(() => fn.apply(this, args), wait);
    };
}

function formatDateTime(epoch) {
    if (!epoch) return "—";
    return new Date(epoch * 1000).toLocaleString();
}

function getCsrf() {
    return document.getElementById("csrfToken")?.value || "";
}

async function loadCalls() {
    try {
        const resp = await fetch("/api/calls/needs-correction");
        const data = await resp.json();
        if (!data.success) throw new Error(data.message || "API error");
        callsData = data.result || [];
        renderCallsList();
    } catch (err) {
        console.error(err);
        document.getElementById("callsList").innerHTML = `<div class="text-danger text-center mt-4">Failed to load calls</div>`;
    }
}

function renderCallsList() {
    const filter = document.getElementById("filterSelect").value;
    const search = document.getElementById("searchInput").value.toLowerCase().trim();

    let filtered = callsData;

    if (filter === "no_location") {
        filtered = filtered.filter(c => !c.has_location);
    } else if (filter === "corrected") {
        filtered = filtered.filter(c => c.has_correction);
    }

    if (search) {
        filtered = filtered.filter(c =>
            (c.address || "").toLowerCase().includes(search) ||
            (c.transcript || "").toLowerCase().includes(search) ||
            (c.talkgroup_name || "").toLowerCase().includes(search) ||
            String(c.call_id).includes(search)
        );
    }

    const container = document.getElementById("callsList");
    const prevScroll = container.scrollTop;
    if (!filtered.length) {
        container.innerHTML = `<div class="text-muted text-center mt-4">No calls match the filter.</div>`;
        return;
    }

    container.innerHTML = filtered.map(c => {
        const badge = c.has_correction
            ? `<span class="badge-correction">Corrected</span>`
            : (!c.has_location ? `<span class="badge-no-loc">No Location</span>` : ``);
        const addressLine = c.address || (c.has_location ? "Has auto location" : "—");
        return `
            <div class="call-item ${c.call_id === selectedCallId ? 'active' : ''}" data-id="${c.call_id}">
                <div class="call-meta">
                    <span class="text-muted">#${c.call_id}</span>
                    <span>${formatDateTime(c.start_epoch)}</span>
                    <span class="badge bg-secondary" style="font-size:0.65rem">${esc(c.system_name || "—")}</span>
                    ${badge}
                </div>
                <div class="call-address">${esc(addressLine)}</div>
                <div class="text-muted" style="font-size:0.75rem; margin-top:0.15rem">${esc(c.incident_category || "—")} &bull; TG ${esc(c.talkgroup_name || c.talkgroup || "—")}</div>
            </div>
        `;
    }).join("");

    container.querySelectorAll(".call-item").forEach(el => {
        el.addEventListener("click", () => selectCall(Number(el.dataset.id)));
    });

    // Preserve scroll position across re-renders (e.g. after a save).
    container.scrollTop = prevScroll;
}

function selectCall(callId) {
    selectedCallId = callId;
    const call = callsData.find(c => c.call_id === callId);
    if (!call) return;

    // Highlight in list
    document.querySelectorAll(".call-item").forEach(el => {
        el.classList.toggle("active", Number(el.dataset.id) === callId);
    });

    // Set map view
    let lat = call.lat, lng = call.lng;
    if (lat != null && lng != null) {
        map.setView([lat, lng], 16);
        updateMarker(lat, lng);
    } else {
        map.setView(getDefaultCenter(), getDefaultZoom());
        if (marker) { map.removeLayer(marker); marker = null; }
    }

    draggedLat = lat;
    draggedLon = lng;
    updateCoordDisplay();

    document.getElementById("addressDisplay").value = call.address || "";
    document.getElementById("notesInput").value = call.notes || "";

    // No change made yet: Save stays disabled until the user moves the marker
    // or searches an address. Revert is only available for existing corrections.
    dirty = false;
    document.getElementById("saveBtn").disabled = true;
    document.getElementById("revertBtn").disabled = !call.has_correction;
}

function initMap() {
    map = L.map("correctionMap", { zoomControl: true }).setView(getDefaultCenter(), getDefaultZoom());

    // CartoDB Dark Matter tiles (match dashboard dark theme)
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: "abcd",
        maxZoom: 20
    }).addTo(map);

    map.on("click", (e) => {
        if (!selectedCallId) return;
        updateMarker(e.latlng.lat, e.latlng.lng);
        draggedLat = e.latlng.lat;
        draggedLon = e.latlng.lng;
        updateCoordDisplay();
        markDirty();
    });

    document.getElementById("mapLoader").classList.add("d-none");
}

function updateMarker(lat, lng) {
    if (marker) {
        marker.setLatLng([lat, lng]);
    } else {
        marker = L.marker([lat, lng], { draggable: true }).addTo(map);
        marker.on("dragend", (e) => {
            draggedLat = e.target.getLatLng().lat;
            draggedLon = e.target.getLatLng().lng;
            updateCoordDisplay();
            markDirty();
        });
    }
}

function updateCoordDisplay() {
    const el = document.getElementById("coordDisplay");
    if (draggedLat != null && draggedLon != null) {
        el.textContent = `Lat: ${draggedLat.toFixed(6)} | Lon: ${draggedLon.toFixed(6)}`;
    } else {
        el.textContent = "Lat: — | Lon: —";
    }
}

async function searchAddress() {
    const input = document.getElementById("addressSearch");
    const q = input.value.trim();
    if (!q) return;

    try {
        const resp = await fetch(NOMINATIM_URL + encodeURIComponent(q), {
            headers: { "Accept-Language": "en" }
        });
        const results = await resp.json();
        if (results && results.length) {
            const r = results[0];
            const lat = parseFloat(r.lat);
            const lon = parseFloat(r.lon);
            map.setView([lat, lon], 16);
            updateMarker(lat, lon);
            draggedLat = lat;
            draggedLon = lon;
            updateCoordDisplay();
            document.getElementById("addressDisplay").value = r.display_name || q;
            if (selectedCallId) markDirty();
        } else {
            showAlert("Address not found", "warning");
        }
    } catch (err) {
        console.error(err);
        showAlert("Search failed", "danger");
    }
}

async function saveCorrection() {
    if (!selectedCallId || draggedLat == null || draggedLon == null) return;
    if (!dirty) {
        showAlert("No changes to save", "info");
        return;
    }

    const saveBtn = document.getElementById("saveBtn");
    const original = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving…';

    const body = {
        lat: draggedLat,
        lng: draggedLon,
        address: document.getElementById("addressDisplay").value,
        notes: document.getElementById("notesInput").value,
        _csrf_token: getCsrf()
    };

    try {
        const resp = await fetch(`/api/calls/${selectedCallId}/correct-location`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCsrf()
            },
            body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.message || "Save failed");
        showAlert("Location corrected", "success");
        // Refresh data
        await loadCalls();
        selectCall(selectedCallId);
    } catch (err) {
        console.error(err);
        showAlert("Failed to save correction", "danger");
    } finally {
        saveBtn.innerHTML = original;
    }
}

async function revertCorrection() {
    if (!selectedCallId) return;

    const ok = await confirmAction({
        title: "Revert Correction",
        body: "Remove this manual correction and revert to automatic geocoding?",
        confirmText: "Revert",
    });
    if (!ok) return;

    const revertBtn = document.getElementById("revertBtn");
    const original = revertBtn.innerHTML;
    revertBtn.disabled = true;
    revertBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Reverting…';

    try {
        const resp = await fetch(`/api/calls/${selectedCallId}/correct-location`, {
            method: "DELETE",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCsrf()
            }
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.message || "Revert failed");
        showAlert("Correction reverted", "success");
        await loadCalls();
        selectCall(selectedCallId);
    } catch (err) {
        console.error(err);
        showAlert("Failed to revert", "danger");
    } finally {
        revertBtn.innerHTML = original;
    }
}

function initCorrectionsPage() {
    initMap();
    loadCalls();

    document.getElementById("filterSelect").addEventListener("change", renderCallsList);
    document.getElementById("searchInput").addEventListener("input", debounce(renderCallsList, 200));
    document.getElementById("addressSearch").addEventListener("keydown", (e) => {
        if (e.key === "Enter") searchAddress();
    });
    document.getElementById("addressDisplay").addEventListener("input", () => {
        if (selectedCallId) markDirty();
    });
    document.getElementById("notesInput").addEventListener("input", () => {
        if (selectedCallId) markDirty();
    });
    document.getElementById("saveBtn").addEventListener("click", saveCorrection);
    document.getElementById("revertBtn").addEventListener("click", revertCorrection);
}

document.addEventListener("DOMContentLoaded", initCorrectionsPage);
