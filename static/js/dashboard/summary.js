/* static/js/dashboard/summary.js */
/* Front-end logic for the Call Summary page. */

let table = null;
let detailModal = null;
let currentResults = [];
let currentSystemName = "";
let currentDateFrom = "";
let currentDateTo = "";
let currentDetailCallId = null;

const els = {};

function esc(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function formatDateTime(epoch) {
    if (!epoch) return "—";
    return new Date(epoch * 1000).toLocaleString();
}

function formatSeconds(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${n.toFixed(1)}s`;
}

function getCsrf() {
    return document.getElementById("csrfToken")?.value || document.querySelector('meta[name="csrf-token"]')?.content || "";
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

function statusBadge(label, enabled, onClass = "success") {
    const cls = enabled ? `text-bg-${onClass}` : "text-bg-secondary";
    const icon = enabled ? "bi-check-circle" : "bi-dash-circle";
    return `<span class="badge ${cls}" title="${esc(label)}"><i class="bi ${icon}"></i> ${esc(label)}</span>`;
}

function renderStatusBadges(row) {
    return `
        <div class="status-badges">
            ${statusBadge("Transcript", row.has_transcript)}
            ${statusBadge("Address", row.has_address_extracted)}
            ${statusBadge("Geocoded", row.has_address_geocoded, "info")}
            ${statusBadge("Incident", row.has_incident, "warning")}
            ${statusBadge("Triggered", row.has_trigger, "danger")}
        </div>
    `;
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
            esc(r.township || "—"),
            esc(r.incident_category || "—"),
            renderStatusBadges(r),
            `<span class="transcript-cell" data-full="${esc(transcript)}">${esc(shortTranscript || "—")}</span>`,
            `<span class="call-action-cell"><button type="button" class="btn btn-sm btn-outline-info action-btn js-call-detail" data-call-id="${esc(callId)}"><i class="bi bi-search"></i> Detail</button></span>`,
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
                { targets: 1, width: "170px" },
                { targets: 2, width: "100px" },
                { targets: 3, width: "180px", orderable: false },
                { targets: 4, width: "auto" },
                { targets: 5, width: "80px", orderable: false, searchable: false },
                { targets: 6, visible: false }
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
        cell.textContent = short || "—";
        cell.classList.remove("transcript-expanded");
    } else {
        const full = cell.getAttribute("data-full") || "";
        cell.textContent = full || "—";
        cell.classList.add("transcript-expanded");
    }
}

function findSummaryRow(callId) {
    return currentResults.find(r => Number(r.call_id) === Number(callId)) || null;
}

function normalizeAudioUrl(url) {
    const raw = String(url || "").trim();
    if (!raw) return "";
    if (raw.startsWith("http://") || raw.startsWith("https://") || raw.startsWith("/")) return raw;
    if (raw.startsWith("static/audio/")) return `/${raw}`;
    return `/static/audio/${raw.replace(/^audio\//, "")}`;
}

function setDetailLoading(callId) {
    currentDetailCallId = callId;
    els.callDetailTitle.textContent = `Call #${callId}`;
    els.callDetailSubtitle.textContent = "Loading…";
    els.callDetailLoading.classList.remove("d-none");
    els.callDetailError.classList.add("d-none");
    els.callDetailBody.classList.add("d-none");
    els.callDetailBody.innerHTML = "";
    els.callReprocessBtn.disabled = true;
    els.callCorrectionLink.classList.add("d-none");
}

async function openCallDetail(callId) {
    setDetailLoading(callId);
    detailModal.show();

    try {
        const resp = await fetch(`/api/tone-finder/calls/${callId}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.message || "Unable to load call detail");
        renderCallDetail(data.result || {}, findSummaryRow(callId));
    } catch (err) {
        console.error(err);
        els.callDetailLoading.classList.add("d-none");
        els.callDetailError.textContent = err.message;
        els.callDetailError.classList.remove("d-none");
    }
}

function renderStat(label, value) {
    return `<div class="call-detail-stat"><div class="label">${esc(label)}</div><div class="value">${esc(value || "—")}</div></div>`;
}

function renderJsonValue(obj) {
    if (!obj) return '<div class="call-detail-empty">No data available.</div>';
    const rows = Object.entries(obj)
        .filter(([, value]) => value !== null && value !== undefined && value !== "")
        .map(([key, value]) => `<dt class="col-sm-4">${esc(key.replace(/_/g, " "))}</dt><dd class="col-sm-8">${esc(typeof value === "object" ? JSON.stringify(value) : value)}</dd>`)
        .join("");
    return rows ? `<dl class="row small mb-0">${rows}</dl>` : '<div class="call-detail-empty">No data available.</div>';
}

function renderTranscriptSegments(segments) {
    if (!segments || !segments.length) return '<div class="call-detail-empty">No transcript segments available.</div>';
    return `<div class="call-segment-list">${segments.map(seg => `
        <div class="call-segment-item">
            <div class="text-muted small">${formatSeconds(seg.start_s)}–${formatSeconds(seg.end_s)}</div>
            <div>${esc(seg.text || "—")}</div>
        </div>
    `).join("")}</div>`;
}

function renderToneList(tones) {
    if (!tones || !tones.length) return '<div class="call-detail-empty">No tones were stored for this call.</div>';
    return `<div class="tone-list">${tones.map(t => {
        const freq = [t.freq_a, t.freq_b].filter(v => v !== null && v !== undefined).map(v => `${Number(v).toFixed(1)}Hz`).join(" / ");
        const time = `${formatSeconds(t.start_s)}–${formatSeconds(t.end_s)}`;
        const matched = t.matches_trigger ? " text-bg-danger" : " text-bg-secondary";
        return `<span class="badge${matched}" title="${esc(time)}">${esc(t.tone_type)}${freq ? ` · ${esc(freq)}` : ""}</span>`;
    }).join("")}</div>`;
}

function renderTriggerList(triggers) {
    if (!triggers || !triggers.length) return '<div class="call-detail-empty">No triggers fired.</div>';
    return `<div class="trigger-list">${triggers.map(t => `<span class="badge text-bg-danger">${esc(t.alert_trigger_name || `Trigger ${t.alert_trigger_id}`)}</span>`).join("")}</div>`;
}

function renderVad(vad) {
    if (!vad || !vad.segments || !vad.segments.length) return '<div class="call-detail-empty">No VAD segments available.</div>';
    const ratio = Number(vad.voice_ratio || 0) * 100;
    return `
        <div class="stats-row mb-2">
            <span class="stat-item">Voice: <span class="stat-value">${formatSeconds(vad.voice_total_s)}</span></span>
            <span class="stat-item">Silence: <span class="stat-value">${formatSeconds(vad.silence_total_s)}</span></span>
            <span class="stat-item">Voice Ratio: <span class="stat-value">${ratio.toFixed(0)}%</span></span>
        </div>
    `;
}

function renderCallDetail(result, summaryRow) {
    const call = result.call || {};
    const transcript = result.transcript || {};
    const location = result.location || {};
    const incident = result.incident || null;
    const audioUrl = normalizeAudioUrl(call.audio_url);
    const callId = call.call_id || currentDetailCallId;

    els.callDetailTitle.textContent = `Call #${callId}`;
    els.callDetailSubtitle.textContent = `${summaryRow?.system_name || currentSystemName || "System"} · ${formatDateTime(call.start_epoch || summaryRow?.start_epoch)}`;
    els.callReprocessBtn.disabled = false;
    els.callCorrectionLink.href = `/dashboard/corrections?call_id=${encodeURIComponent(callId)}`;
    els.callCorrectionLink.classList.remove("d-none");

    els.callDetailBody.innerHTML = `
        <div class="call-detail-grid">
            ${renderStat("Date/Time", formatDateTime(call.start_epoch || summaryRow?.start_epoch))}
            ${renderStat("Duration", formatSeconds(call.duration_s))}
            ${renderStat("Talkgroup", call.talkgroup || "—")}
            ${renderStat("Merged", call.merged_from_stub ? "Yes" : "No")}
        </div>

        <div class="call-detail-section">
            <h6>Audio</h6>
            ${audioUrl ? `<audio controls preload="none" class="w-100" src="${esc(audioUrl)}"></audio><div class="small mt-2"><a href="${esc(audioUrl)}" target="_blank" rel="noopener">Open audio</a></div>` : '<div class="call-detail-empty">No audio URL available.</div>'}
        </div>

        <div class="call-detail-section">
            <h6>Transcript</h6>
            ${transcript.text_full ? `<div class="call-transcript-box">${esc(transcript.text_full)}</div>` : '<div class="call-detail-empty">No transcript available.</div>'}
            <div class="mt-3">${renderTranscriptSegments(result.transcript_segments)}</div>
        </div>

        <div class="call-detail-section">
            <h6>Incident</h6>
            ${incident ? renderJsonValue(incident) : renderJsonValue({ category: transcript.incident_category || summaryRow?.incident_category || "" })}
        </div>

        <div class="call-detail-section">
            <h6>Location</h6>
            <div class="row g-3">
                <div class="col-md-6"><div class="text-muted small mb-1">Extracted</div>${renderJsonValue(location.extracted)}</div>
                <div class="col-md-6"><div class="text-muted small mb-1">Geocoded</div>${renderJsonValue(location.geocoded)}</div>
            </div>
        </div>

        <div class="call-detail-section">
            <h6>Tones & Triggers</h6>
            <div class="mb-3">${renderToneList(result.tones)}</div>
            ${renderTriggerList(result.triggers)}
        </div>

        <div class="call-detail-section">
            <h6>Voice Activity</h6>
            ${renderVad(result.vad)}
        </div>
    `;

    els.callDetailLoading.classList.add("d-none");
    els.callDetailError.classList.add("d-none");
    els.callDetailBody.classList.remove("d-none");
}

async function reprocessCurrentCall() {
    if (!currentDetailCallId) return;
    const ok = await confirmAction({
        title: "Reprocess Call",
        body: `Re-run tone detection, AI enrichment, and trigger dispatch for call #${currentDetailCallId}?`,
        confirmText: "Reprocess",
    });
    if (!ok) return;

    const original = els.callReprocessBtn.innerHTML;
    els.callReprocessBtn.disabled = true;
    els.callReprocessBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Reprocessing…';

    try {
        const resp = await fetch(`/api/call-upload/reprocess/${currentDetailCallId}`, {
            method: "POST",
            headers: { "X-CSRFToken": getCsrf() }
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false || data.error) {
            throw new Error(data.message || data.error || `Reprocess failed (${resp.status})`);
        }
        showAlert(`Call #${currentDetailCallId} reprocessed`, "success");
        await openCallDetail(currentDetailCallId);
    } catch (err) {
        console.error(err);
        showAlert(err.message || "Reprocess failed", "danger");
        els.callReprocessBtn.disabled = false;
    } finally {
        els.callReprocessBtn.innerHTML = original;
    }
}

function resetSummaryView() {
    currentResults = [];
    if (table) table.clear().draw(false);
    els.tableWrapper.classList.add("d-none");
    els.noResults.classList.add("d-none");
    els.initialMessage.classList.remove("d-none");
    els.exportBtn.classList.add("d-none");
    els.statsRow.classList.add("d-none");
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
        initialMessage: document.getElementById("initialMessage"),
        callDetailTitle: document.getElementById("callDetailTitle"),
        callDetailSubtitle: document.getElementById("callDetailSubtitle"),
        callDetailLoading: document.getElementById("callDetailLoading"),
        callDetailError: document.getElementById("callDetailError"),
        callDetailBody: document.getElementById("callDetailBody"),
        callReprocessBtn: document.getElementById("callReprocessBtn"),
        callCorrectionLink: document.getElementById("callCorrectionLink"),
    });

    const modalEl = document.getElementById("callDetailModal");
    if (modalEl) detailModal = new bootstrap.Modal(modalEl);

    if (els.runQueryBtn) els.runQueryBtn.addEventListener("click", runQuery);
    if (els.clearFilters) {
        els.clearFilters.addEventListener("click", () => {
            els.systemSelect.value = "";
            els.dateFrom.value = "";
            els.dateTo.value = "";
            resetSummaryView();
        });
    }
    if (els.exportBtn) els.exportBtn.addEventListener("click", exportToText);
    if (els.callReprocessBtn) els.callReprocessBtn.addEventListener("click", reprocessCurrentCall);

    const tableEl = document.getElementById("summaryTable");
    if (tableEl) {
        tableEl.addEventListener("click", (e) => {
            const detailBtn = e.target.closest(".js-call-detail");
            if (detailBtn) {
                openCallDetail(detailBtn.dataset.callId);
                return;
            }
            toggleTranscript(e);
        });
    }

    [els.dateFrom, els.dateTo].forEach(el => {
        if (el) {
            el.addEventListener("keydown", (e) => {
                if (e.key === "Enter") runQuery();
            });
        }
    });
}

document.addEventListener("DOMContentLoaded", initSummaryPage);
