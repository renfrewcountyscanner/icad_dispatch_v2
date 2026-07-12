/* public_map/static/js/map.js */
/* Public Live Emergency Map Frontend */

// ── Config ─────────────────────────────────────────────────────────
const RENFREW_CENTER = [45.4748, -77.6972];
const DEFAULT_ZOOM = 10;
const REFRESH_INTERVAL = 30000; // 30s fallback poll
const AUTO_FIT_DELAY_MS = 120000; // 2 minutes


const INCIDENT_COLORS = {
    Fire:    { bg: '#dc3545', text: '#fff' },
    Medical: { bg: '#0d6efd', text: '#fff' },
    Traffic: { bg: '#ffc107', text: '#000' },
    Rescue:  { bg: '#20c997', text: '#000' },
    Utilities: { bg: '#adb5bd', text: '#000' },
    HazMat:  { bg: '#fd7e14', text: '#000' },
    Other:   { bg: '#6c757d', text: '#fff' }
};

const INCIDENT_SHORT = {
    Fire: 'F', Medical: 'M', Traffic: 'T', Rescue: 'R',
    Utilities: 'U', HazMat: 'H', Other: 'O'
};

// ── State ──────────────────────────────────────────────────────────
let map;
let markersLayer;
let heatLayer;
let currentCalls = [];
let visibleCalls = [];
let callMarkers = new Map();
let isHeatmap = false;
let isDarkTheme = true;
let socket;
let audioCtx;
let lastCallId = 0;
let selectedCallId = null;
let isMuted = false;
let isLiveFeed = false;
let autoFitTimer = null;
let desktopNotifEnabled = false;
let notifUnreadCount = 0;
let notifList = []; // { call_id, time, incident, address, system, read }
let testCallId = -1; // negative IDs for test calls
let allTriggers = []; // cached trigger list
let selectedTriggerIds = new Set();

// ── Init ───────────────────────────────────────────────────────────
function init() {
    syncMapLayout();
    initAudioContext();
    initMap();
    initSocket();
    initControls();
    initNotifications();
    initDesktopNotifications();
    loadCalls();

    window.addEventListener('resize', debounce(syncMapLayout, 100));
}

// The map header can wrap on phones and compact desktops. Keep the fixed map
// workspace below its measured height instead of relying on a hard-coded value.
function syncMapLayout() {
    const topBar = document.getElementById('topBar');
    if (!topBar) return;

    document.documentElement.style.setProperty('--topbar-h', `${topBar.offsetHeight}px`);
    if (map) map.invalidateSize(false);
}

function initAudioContext() {
    try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) {
        console.warn("Web Audio not supported");
    }
}

function playNotificationSound() {
    if (isMuted || !audioCtx) return;
    try {
        // Resume context if suspended (browser autoplay policy)
        if (audioCtx.state === 'suspended') audioCtx.resume();
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.frequency.setValueAtTime(880, audioCtx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.15);
        gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.3);
        osc.start();
        osc.stop(audioCtx.currentTime + 0.3);
    } catch (e) {
        // ignore
    }
}

// ── Map ────────────────────────────────────────────────────────────
function initMap() {
    map = L.map('map', { zoomControl: true }).setView(RENFREW_CENTER, DEFAULT_ZOOM);

    addDarkTiles();

    markersLayer = L.layerGroup().addTo(map);

    heatLayer = L.heatLayer([], {
        radius: 25,
        blur: 15,
        maxZoom: 14,
        gradient: { 0.4: 'blue', 0.6: 'cyan', 0.7: 'lime', 0.8: 'yellow', 1: 'red' }
    });
}

function addDarkTiles() {
    // Use OSM tiles with CSS filter for dark mode — preserves full detail
    // (buildings, footpaths, POIs) while keeping night-friendly brightness
    document.getElementById('map').classList.add('dark-tiles');
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 20
    }).addTo(map);
}

function addLightTiles() {
    document.getElementById('map').classList.remove('dark-tiles');
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 20
    }).addTo(map);
}

/**
 * Compute today at 00:00 in America/Toronto (Eastern Time) as a UTC epoch.
 * Uses Intl.DateTimeFormat to get the calendar date in Toronto, then
 * measures the timezone offset to convert Toronto-local 00:00 to UTC.
 * Works correctly regardless of the user's browser timezone and handles
 * DST transitions (EST↔EDT) automatically.
 */
function torontoMidnightEpoch() {
    // Get the calendar date as it is right now in America/Toronto
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/Toronto',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    }).formatToParts(new Date());
    const y = parts.find(p => p.type === 'year').value;
    const m = parts.find(p => p.type === 'month').value;
    const d = parts.find(p => p.type === 'day').value;

    // Build a Date that we *pretend* is UTC for the same calendar date,
    // then measure the offset between that moment's UTC wall-clock and its
    // Toronto wall-clock. The difference is the Toronto timezone offset,
    // which we apply to get the epoch for Toronto-local 00:00.
    const tempDate = new Date(`${y}-${m}-${d}T00:00:00Z`);
    const torontoStr = tempDate.toLocaleString('en-US', { timeZone: 'America/Toronto' });
    const utcStr = tempDate.toLocaleString('en-US', { timeZone: 'UTC' });
    const offsetMs = new Date(utcStr).getTime() - new Date(torontoStr).getTime();
    return Math.floor((tempDate.getTime() + offsetMs) / 1000);
}

// ── SocketIO ─────────────────────────────────────────────────────
function initSocket() {
    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
        setLiveStatus('ONLINE');
        loadCalls(); // catch up after any disconnect / reconnect gap
        const timeRange = document.getElementById('timeRange');
        if (timeRange.value === 'custom') {
            const dateFrom = document.getElementById('dateFrom').value;
            const dateTo = document.getElementById('dateTo').value;
            const sub = {};
            if (dateFrom) sub.from = Math.floor(new Date(dateFrom + 'T00:00').getTime() / 1000);
            if (dateTo) sub.to = Math.floor(new Date(dateTo + 'T23:59:59').getTime() / 1000);
            if (!sub.from && !sub.to) sub.hours = 24;
            socket.emit('subscribe', sub);
        } else if (timeRange.value === 'midnight') {
            socket.emit('subscribe', { from: torontoMidnightEpoch() });
        } else {
            socket.emit('subscribe', { hours: parseFloat(timeRange.value) });
        }
    });

    socket.on('disconnect', () => {
        setLiveStatus('OFFLINE');
    });

    socket.on('reconnect', (attempt) => {
        console.log(`Socket reconnected after ${attempt} attempt(s)`);
    });

    socket.on('connect_error', (err) => {
        console.warn('Socket connect error:', err.message);
    });

    socket.on('new_calls', (payload) => {
        if (payload.calls && payload.calls.length) {
            handleNewCalls(payload.calls);
        }
    });

    socket.on('call_updated', (payload) => {
        if (payload.call_id) {
            refreshCallMarker(payload.call_id);
        }
    });
}

function setLiveStatus(status) {
    const badge = document.getElementById('liveToggle');
    const text = document.getElementById('liveText');
    text.textContent = status;
    badge.classList.remove('online', 'offline');
    if (status === 'ONLINE') badge.classList.add('online');
    else badge.classList.add('offline');
}

function updateMapHeaderSummary() {
    const scopeBadge = document.getElementById('mapScopeBadge');
    const countBadge = document.getElementById('mapCountBadge');
    const systemSelect = document.getElementById('systemFilter');
    const scopeLabel = systemSelect?.selectedOptions?.[0]?.textContent || 'All systems';

    if (scopeBadge) scopeBadge.textContent = scopeLabel;
    if (countBadge) countBadge.textContent = `${visibleCalls.length} calls`;
}

// ── Data Loading ───────────────────────────────────────────────────
async function loadCalls() {
    const timeRange = document.getElementById('timeRange');
    const systemId = document.getElementById('systemFilter').value;
    const params = new URLSearchParams();

    if (timeRange.value === 'custom') {
        const dateFrom = document.getElementById('dateFrom').value;
        const dateTo = document.getElementById('dateTo').value;
        if (dateFrom) {
            params.append('from', Math.floor(new Date(dateFrom + 'T00:00').getTime() / 1000));
        }
        if (dateTo) {
            params.append('to', Math.floor(new Date(dateTo + 'T23:59:59').getTime() / 1000));
        }
        if (!dateFrom && !dateTo) {
            params.append('hours', '24');
        }
    } else if (timeRange.value === 'midnight') {
        params.append('from', torontoMidnightEpoch());
    } else {
        params.append('hours', timeRange.value);
    }

    if (systemId) params.append('system_id', systemId);
    if (selectedTriggerIds.size > 0) {
        params.append('alert_trigger_ids', Array.from(selectedTriggerIds).join(','));
    }

    try {
        const resp = await fetch(`/api/calls?${params}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.message);

        currentCalls = data.result || [];
        updateLastCallId();
        applyFilters();

        // Warn user if the result set was truncated by the dynamic LIMIT
        if (data.meta && data.meta.truncated) {
            const limit = data.meta.limit || '?';
            showToastMsg(`Showing ${limit} most recent calls — widen your filters to see more.`, 'warning');
        }
    } catch (err) {
        console.error('Failed to load calls:', err);
    }
}

function updateLastCallId() {
    if (currentCalls.length) {
        lastCallId = Math.max(lastCallId, ...currentCalls.map(c => c.call_id));
    }
}

// ── Filtering ──────────────────────────────────────────────────────
function applyFilters(skipFitBounds = false) {
    const activeIncidents = new Set(
        Array.from(document.querySelectorAll('.inc-filter:checked')).map(cb => cb.value)
    );

    // Custom date range client-side filtering (so socket calls outside range are hidden)
    let fromEpoch = null;
    let toEpoch = null;
    const timeRange = document.getElementById('timeRange');
    if (!isLiveFeed && timeRange.value === 'custom') {
        const dateFrom = document.getElementById('dateFrom').value;
        const dateTo = document.getElementById('dateTo').value;
        if (dateFrom) fromEpoch = Math.floor(new Date(dateFrom + 'T00:00').getTime() / 1000);
        if (dateTo) toEpoch = Math.floor(new Date(dateTo + 'T23:59:59').getTime() / 1000);
    }

    function passesFilters(c) {
        const inc = c.incident_category || 'Other';
        if (!activeIncidents.has(inc)) return false;
        if (fromEpoch && c.timestamp < fromEpoch) return false;
        if (toEpoch && c.timestamp > toEpoch) return false;
        return true;
    }

    // In Live Feed mode, we only show the single most recent call
    if (isLiveFeed) {
        const newCalls = currentCalls.filter(passesFilters);
        visibleCalls = newCalls.length > 0 ? [newCalls[0]] : [];
    } else {
        visibleCalls = currentCalls.filter(passesFilters);
    }

    renderMarkers();
    updateStats();
    updateTicker();
    if (!isLiveFeed && !skipFitBounds) fitBounds();
}

function renderMarkers() {
    markersLayer.clearLayers();
    callMarkers.clear();

    const heatPoints = [];

    // Find the most recent call among visible calls
    let latestCall = null;
    let latestTimestamp = 0;
    visibleCalls.forEach(call => {
        if (call.timestamp > latestTimestamp) {
            latestTimestamp = call.timestamp;
            latestCall = call;
        }
    });

    visibleCalls.forEach(call => {
        if (!call.lat || !call.lng) return;

        const color = INCIDENT_COLORS[call.incident_category] || INCIDENT_COLORS.Other;
        const short = INCIDENT_SHORT[call.incident_category] || '?';

        // Age-based opacity (disabled for custom date ranges — all solid)
        const timeRange = document.getElementById('timeRange');
        let opacity;
        if (timeRange && timeRange.value === 'custom') {
            opacity = 1.0;
        } else {
            const ageHours = (Date.now() / 1000 - call.timestamp) / 3600;
            opacity = Math.max(0.3, 1 - (ageHours / 72));
        }

        // Test call uses special marker
        const isTest = call.call_id < 0;
        const isLatest = latestCall && call.call_id === latestCall.call_id;
        let markerHtml;
        if (isTest) {
            markerHtml = `<div class="test-marker"><span>TEST</span></div>`;
        } else {
            const latestClass = isLatest ? ' latest-marker' : '';
            markerHtml = `<div class="custom-marker${latestClass}" style="background:${color.bg};opacity:${opacity};border-color:${color.bg}"><span>${short}</span></div>`;
        }

        const icon = L.divIcon({
            className: '',
            html: markerHtml,
            iconSize: isTest ? [32, 32] : [28, 28],
            iconAnchor: isTest ? [16, 32] : [14, 28]
        });

        const marker = L.marker([call.lat, call.lng], { icon }).addTo(markersLayer);

        const popupContent = `
            <div style="min-width:180px">
                <div style="font-weight:600;margin-bottom:0.3rem">${esc(call.incident_category || 'Call')}</div>
                <div style="font-size:0.8rem;color:#aaa;margin-bottom:0.3rem">${esc(call.address || '—')}</div>
                <div style="font-size:0.75rem;color:#888">${formatTime(call.timestamp)}</div>
                <div style="font-size:0.75rem;color:#888">${esc(call.system_name || '')} • TG ${esc(call.talkgroup_name || call.talkgroup || '—')}</div>
            </div>
        `;
        marker.bindPopup(popupContent);

        marker.on('click', () => {
            updateUrlHash(call.call_id);
            showCallDetail(call);
        });

        callMarkers.set(call.call_id, marker);
        heatPoints.push([call.lat, call.lng, 0.6]);
    });

    heatLayer.setLatLngs(heatPoints);
}

function refreshCallMarker(callId) {
    fetch(`/api/calls/${callId}`)
        .then(r => r.json())
        .then(data => {
            if (data.success && data.result) {
                const call = data.result;
                const idx = currentCalls.findIndex(c => c.call_id === callId);
                if (idx >= 0) currentCalls[idx] = call;
                else currentCalls.push(call);
                applyFilters();
                if (selectedCallId === callId) showCallDetail(call);
            }
        })
        .catch(console.error);
}

// ── Auto-Pan & Auto-Fit ────────────────────────────────────────────
function panToCall(call) {
    if (!call.lat || !call.lng) return;

    map.flyTo([call.lat, call.lng], 17, { duration: 1.5 });

    // In Live Feed mode, never auto-fit — always stay focused on the latest call
    if (isLiveFeed) {
        cancelAutoFit();
        return;
    }

    // Show countdown UI
    showAutoFitCountdown();

    // Start or reset 2-minute timer
    if (autoFitTimer) {
        clearTimeout(autoFitTimer);
    }
    autoFitTimer = setTimeout(() => {
        hideAutoFitCountdown();
        closeSidebar();
        fitBounds();
        autoFitTimer = null;
    }, AUTO_FIT_DELAY_MS);
}

function showAutoFitCountdown() {
    const el = document.getElementById('autoFitCountdown');
    const text = document.getElementById('countdownText');
    el.classList.remove('d-none');

    let remaining = AUTO_FIT_DELAY_MS / 1000;
    const updateText = () => {
        const min = Math.floor(remaining / 60);
        const sec = String(remaining % 60).padStart(2, '0');
        text.textContent = `Auto-fit in ${min}:${sec}`;
    };
    updateText();

    // Store interval on the element so we can clear it
    if (el._interval) clearInterval(el._interval);
    el._interval = setInterval(() => {
        remaining--;
        updateText();
        if (remaining <= 0) {
            clearInterval(el._interval);
            el._interval = null;
        }
    }, 1000);
}

function hideAutoFitCountdown() {
    const el = document.getElementById('autoFitCountdown');
    el.classList.add('d-none');
    if (el._interval) {
        clearInterval(el._interval);
        el._interval = null;
    }
}

function cancelAutoFit() {
    if (autoFitTimer) {
        clearTimeout(autoFitTimer);
        autoFitTimer = null;
    }
    hideAutoFitCountdown();
}

// ── New Call Handling ─────────────────────────────────────────────
function handleNewCalls(calls) {
    let added = 0;
    let updated = 0;
    let locationAdded = 0;
    const newArrivals = [];

    calls.forEach(call => {
        const idx = currentCalls.findIndex(c => c.call_id === call.call_id);
        if (idx >= 0) {
            // Update existing call (iCAD push with complete data arrived after poller's incomplete version)
            const hadLocation = !!(currentCalls[idx].lat && currentCalls[idx].lng);
            const hadTranscript = currentCalls[idx].transcript && currentCalls[idx].transcript.trim();
            currentCalls[idx] = call;
            updated++;
            // If this update adds coordinates for the first time, treat as a
            // "new arrival" for map zoom purposes (but not for sound/notifications).
            if (!hadLocation && call.lat && call.lng) {
                locationAdded++;
            }
            // If this update gave us a transcript/address where there was none before,
            // add to notification panel
            if (!hadTranscript && call.transcript && call.transcript.trim()) {
                newArrivals.push(call);
            }
        } else if (call.call_id > lastCallId) {
            currentCalls.unshift(call);
            lastCallId = Math.max(lastCallId, call.call_id);
            added++;
            newArrivals.push(call);
        }
    });

    if ((added > 0 || locationAdded > 0) && currentCalls.length > 2000) {
        currentCalls = currentCalls.slice(0, 2000);
        // Recalculate lastCallId after truncation to avoid rejecting future calls
        lastCallId = currentCalls.reduce((max, c) => Math.max(max, c.call_id), 0);
    }

    if (added > 0 || updated > 0) {
        // Add notifications for truly new calls (and updated calls that just got transcripts)
        newArrivals.forEach(c => addNotification(c));

        // Trigger fitBounds for truly new calls OR when an existing call gains
        // coordinates for the first time. Update-only batches (e.g. poller
        // duplicates) should not zoom out while the 2-min auto-fit timer is running.
        if (added > 0 || locationAdded > 0) {
            applyFilters();
        } else {
            applyFilters(true); // skip fitBounds
        }
        updateStats();
        updateTicker();

        // In Live Feed mode, focus on newest
        if (isLiveFeed) {
            const newest = visibleCalls[0];
            if (newest) {
                panToCall(newest);
                showCallDetail(newest);
            }
        } else if (added > 0 || locationAdded > 0) {
            const newest = calls[0];
            if (newest) {
                panToCall(newest);
                showCallDetail(newest);
            }
        }

        if (added > 0) {
            playNotificationSound();
            sendDesktopNotifications(newArrivals);
        }
    }
}

// ── Notifications Panel ────────────────────────────────────────────
function initNotifications() {
    document.getElementById('notifToggle').addEventListener('click', toggleNotifPanel);
    document.getElementById('closeNotifPanel').addEventListener('click', () => {
        document.getElementById('notifPanel').classList.remove('open');
    });
    document.getElementById('clearNotifs').addEventListener('click', clearAllNotifications);
}

function toggleNotifPanel() {
    document.getElementById('notifPanel').classList.toggle('open');
    notifUnreadCount = 0;
    updateNotifBadge();
    // Mark all as read
    notifList.forEach(n => n.read = true);
    renderNotifications();
}

function addNotification(call) {
    notifList.unshift({
        call_id: call.call_id,
        timestamp: call.timestamp,
        incident: call.incident_category || 'Other',
        address: call.address || '—',
        system: call.system_name || '',
        read: false,
    });
    if (notifList.length > 50) notifList = notifList.slice(0, 50);
    notifUnreadCount++;
    updateNotifBadge();
    renderNotifications();
}

function updateNotifBadge() {
    const badge = document.getElementById('notifBadge');
    if (notifUnreadCount > 0) {
        badge.textContent = notifUnreadCount > 99 ? '99+' : notifUnreadCount;
        badge.classList.remove('d-none');
    } else {
        badge.classList.add('d-none');
    }
}

function renderNotifications() {
    const container = document.getElementById('notifList');
    if (!notifList.length) {
        container.innerHTML = '<div class="notif-empty">No new calls yet</div>';
        return;
    }
    container.innerHTML = notifList.map(n => {
        const color = INCIDENT_COLORS[n.incident] || INCIDENT_COLORS.Other;
        const unreadClass = n.read ? '' : 'unread';
        return `
            <div class="notif-item ${unreadClass}" data-id="${n.call_id}">
                <div class="notif-meta">
                    <span class="notif-time">${formatTime(n.timestamp)}</span>
                    <span class="notif-inc" style="background:${color.bg};color:${color.text}">${esc(n.incident)}</span>
                </div>
                <div class="notif-addr">${esc(n.address)}</div>
                <div class="notif-sys">${esc(n.system)}</div>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.notif-item').forEach(el => {
        el.addEventListener('click', () => {
            const callId = Number(el.dataset.id);
            const call = currentCalls.find(c => c.call_id === callId);
            if (call) {
                updateUrlHash(call.call_id);
                showCallDetail(call);
                if (call.lat && call.lng) map.flyTo([call.lat, call.lng], 16);
            }
        });
    });
}

function clearAllNotifications() {
    notifList = [];
    notifUnreadCount = 0;
    updateNotifBadge();
    renderNotifications();
}

// ── Desktop Notifications ─────────────────────────────────────────
function initDesktopNotifications() {
    if (!('Notification' in window)) return;

    // Request permission on first user interaction
    const request = () => {
        if (Notification.permission === 'default') {
            Notification.requestPermission().then(perm => {
                if (perm === 'granted') desktopNotifEnabled = true;
            });
        } else if (Notification.permission === 'granted') {
            desktopNotifEnabled = true;
        }
        document.removeEventListener('click', request);
    };
    document.addEventListener('click', request, { once: true });
}

function sendDesktopNotifications(calls) {
    if (!desktopNotifEnabled || Notification.permission !== 'granted') return;
    calls.forEach(call => {
        try {
            const inc = call.incident_category || 'Call';
            new Notification(`${inc} Call`, {
                body: `${call.address || '—'} — ${call.system_name || ''}`,
                icon: '/static/icons/marker-fire.svg',
                tag: String(call.call_id),
            });
        } catch (e) {
            // ignore
        }
    });
}

// ── Sidebar Detail ────────────────────────────────────────────────
function showCallDetail(call) {
    selectedCallId = call.call_id;
    const sidebar = document.getElementById('sidebar');
    const content = document.getElementById('sidebarContent');

    // Test calls don't exist in DB — render immediately from cached data
    if (call.call_id < 0) {
        _renderSidebarContent(call);
        return;
    }

    // Show loading state, then fetch fresh data from API
    content.innerHTML = '<div class="sidebar-placeholder"><i class="bi bi-arrow-repeat" style="display:block;font-size:1.5rem;margin-bottom:0.5rem;animation:spin 1s linear infinite"></i> Loading call details…</div>';
    sidebar.classList.add('open');

    fetch(`/api/calls/${call.call_id}`)
        .then(r => r.json())
        .then(data => {
            if (data.success && data.result) {
                _renderSidebarContent(data.result);
                // Update cached copy in currentCalls so subsequent clicks are faster
                const idx = currentCalls.findIndex(c => c.call_id === call.call_id);
                if (idx >= 0) currentCalls[idx] = data.result;
            } else {
                _renderSidebarContent(call);
            }
        })
        .catch(err => {
            console.error('Failed to fetch call detail:', err);
            _renderSidebarContent(call);
        });
}

function _renderSidebarContent(call) {
    const sidebar = document.getElementById('sidebar');
    const content = document.getElementById('sidebarContent');

    const inc = call.incident_category || 'Other';

    content.innerHTML = `
        <div class="call-detail">
            <div class="detail-header">
                <div class="detail-id">Call #${call.call_id}</div>
                <div class="detail-time">${formatTime(call.timestamp)} <span style="color:#888">(${timeAgo(call.timestamp)})</span></div>
                <div>
                    <span class="detail-badge badge-${inc.toLowerCase()}">${esc(inc)}</span>
                    ${call.is_corrected ? '<span class="detail-badge" style="background:rgba(255,193,7,0.2);color:#fcd34d">Corrected</span>' : ''}
                    ${call.call_id < 0 ? '<span class="detail-badge" style="background:rgba(255,193,7,0.2);color:#ffc107">TEST</span>' : ''}
                </div>
            </div>

            <div class="detail-section">
                <div class="section-label">Address</div>
                <div class="section-value">${esc(call.address || '—')}</div>
                <div class="section-value" style="font-size:.72rem;color:#888;margin-top:.2rem">Source: ${esc(call.location_source || 'not available')}</div>
            </div>

            ${call.address_extracted || call.address_geocoded ? `
            <div class="detail-section">
                <div class="section-label">Location Evidence</div>
                ${call.address_extracted ? `<div class="section-value" style="font-size:.78rem">Extracted: ${esc(call.address_extracted.raw_text || call.address_extracted.street || 'address detected')}</div>` : ''}
                ${call.address_geocoded ? `<div class="section-value" style="font-size:.78rem;color:#aaa">Geocoder: ${esc(call.address_geocoded.formatted_address || 'coordinates resolved')}</div>` : ''}
            </div>
            ` : ''}

            <div class="detail-section">
                <div class="section-label">System</div>
                <div class="section-value">${esc(call.system_name || '—')}</div>
            </div>

            <div class="detail-section">
                <div class="section-label">Talkgroup</div>
                <div class="section-value">${esc(call.talkgroup_name || call.talkgroup || '—')}</div>
            </div>

            <div class="detail-section">
                <div class="section-label">Duration</div>
                <div class="section-value">${call.duration_s != null ? call.duration_s.toFixed(1) + 's' : '—'}</div>
            </div>

            ${call.correction_notes ? `
            <div class="detail-section">
                <div class="section-label">Correction Notes</div>
                <div class="section-value" style="font-style:italic;color:#aaa">${esc(call.correction_notes)}</div>
            </div>
            ` : ''}

            <div class="detail-section">
                <div class="section-label">Transcript</div>
                <div class="transcript-box">${esc(call.transcript || 'No transcript available.')}</div>
            </div>

            ${call.audio_url ? `
            <div class="detail-section">
                <div class="section-label">Audio</div>
                <div class="audio-player">
                    <audio controls preload="none" src="${esc(call.audio_url)}"></audio>
                </div>
            </div>
            ` : ''}

            <div class="share-row">
                <button onclick="copyPermalink(${call.call_id})"><i class="bi bi-link-45deg"></i> Copy Link</button>
            </div>
        </div>
    `;

    sidebar.classList.add('open');
}

function closeSidebar() {
    document.getElementById('sidebar').classList.remove('open');
    selectedCallId = null;
    updateUrlHash(null);
}

function updateUrlHash(callId) {
    if (callId) {
        history.replaceState(null, '', `?call=${callId}`);
    } else {
        history.replaceState(null, '', window.location.pathname);
    }
}

function copyPermalink(callId) {
    const url = `${window.location.origin}?call=${callId}`;
    navigator.clipboard.writeText(url).then(() => {
        showToastMsg('Link copied to clipboard', 'success');
    }).catch(() => {
        showToastMsg('Failed to copy link', 'danger');
    });
}

function showToastMsg(message, type) {
    const toast = document.createElement('div');
    toast.className = 'toast-item';
    if (type === 'success') toast.style.borderLeftColor = '#198754';
    if (type === 'danger') toast.style.borderLeftColor = '#dc3545';
    if (type === 'warning') toast.style.borderLeftColor = '#ffc107';
    toast.innerHTML = `<div>${esc(message)}</div>`;
    document.getElementById('toastContainer').appendChild(toast);
    setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 5000);
}

// ── Stats & Ticker ────────────────────────────────────────────────
function updateStats() {
    const total = visibleCalls.length;
    const byType = {};
    const byTG = {};

    visibleCalls.forEach(c => {
        const inc = c.incident_category || 'Other';
        byType[inc] = (byType[inc] || 0) + 1;
        const tg = c.talkgroup_name || c.talkgroup || 'Unknown';
        byTG[tg] = (byTG[tg] || 0) + 1;
    });

    document.getElementById('statTotal').textContent = total;
    document.getElementById('statFire').textContent = byType.Fire || 0;
    document.getElementById('statMedical').textContent = byType.Medical || 0;
    document.getElementById('statTraffic').textContent = byType.Traffic || 0;

    const topTG = Object.entries(byTG).sort((a, b) => b[1] - a[1])[0];
    document.getElementById('statTopTG').textContent = topTG ? topTG[0] : '—';

    updateMapHeaderSummary();
}

function updateTicker() {
    const container = document.getElementById('tickerContent');
    const recent = visibleCalls.slice(0, 15);
    if (!recent.length) {
        container.innerHTML = '<span class="ticker-item">No recent calls</span>';
        return;
    }
    container.innerHTML = recent.map(c => {
        const inc = c.incident_category || 'Other';
        const color = INCIDENT_COLORS[inc] || INCIDENT_COLORS.Other;
        return `
            <span class="ticker-item">
                <span class="t-time">${formatTime(c.timestamp).split(' ')[1]}</span>
                <span class="t-type" style="color:${color.bg}">${esc(inc)}</span>
                <span>${esc((c.address || '—').substring(0, 30))}</span>
            </span>
        `;
    }).join('');
}

// ── Bounds & Zoom ──────────────────────────────────────────────────
function fitBounds() {
    const points = visibleCalls.filter(c => c.lat && c.lng).map(c => [c.lat, c.lng]);
    if (!points.length) {
        map.setView(RENFREW_CENTER, DEFAULT_ZOOM);
        return;
    }
    if (points.length === 1) {
        // Show some context around a single call
        map.setView(points[0], 12);
        return;
    }
    const bounds = L.latLngBounds(points);
    map.fitBounds(bounds, { padding: [50, 50], maxZoom: 14, minZoom: 9 });
}

// ── Trigger Filter ───────────────────────────────────────────────
async function loadTriggers(systemId) {
    const params = new URLSearchParams();
    if (systemId) params.append('system_id', systemId);

    try {
        const resp = await fetch(`/api/triggers?${params}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.message);
        allTriggers = data.result || [];
        renderTriggerOptions();
        updateTriggerLabel();
    } catch (err) {
        console.error('Failed to load triggers:', err);
    }
}

function renderTriggerOptions() {
    const container = document.getElementById('triggerFilterOptions');
    container.innerHTML = '';

    if (allTriggers.length === 0) {
        container.innerHTML = '<div class="empty-msg">No triggers available</div>';
        return;
    }

    allTriggers.forEach(trig => {
        const label = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = trig.alert_trigger_id;
        cb.checked = selectedTriggerIds.has(String(trig.alert_trigger_id)) || selectedTriggerIds.has(trig.alert_trigger_id);
        cb.addEventListener('change', (e) => {
            const id = parseInt(e.target.value, 10);
            if (e.target.checked) {
                selectedTriggerIds.add(id);
            } else {
                selectedTriggerIds.delete(id);
            }
            updateTriggerLabel();
            loadCalls();
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(trig.alert_trigger_name));
        container.appendChild(label);
    });
}

function updateTriggerLabel() {
    const btn = document.getElementById('triggerFilterBtn');
    const label = document.getElementById('triggerFilterLabel');
    if (selectedTriggerIds.size === 0) {
        label.textContent = 'All Triggers';
        btn.classList.remove('active');
    } else if (selectedTriggerIds.size === 1) {
        const id = Array.from(selectedTriggerIds)[0];
        const trig = allTriggers.find(t => t.alert_trigger_id === id || t.alert_trigger_id === parseInt(id, 10));
        label.textContent = trig ? trig.alert_trigger_name : `${selectedTriggerIds.size} selected`;
        btn.classList.add('active');
    } else {
        label.textContent = `${selectedTriggerIds.size} selected`;
        btn.classList.add('active');
    }
}

function toggleTriggerDropdown() {
    document.getElementById('triggerFilter').classList.toggle('open');
}

function closeTriggerDropdown() {
    document.getElementById('triggerFilter').classList.remove('open');
}

function selectAllTriggers() {
    allTriggers.forEach(t => selectedTriggerIds.add(t.alert_trigger_id));
    renderTriggerOptions();
    updateTriggerLabel();
    loadCalls();
}

function clearAllTriggers() {
    selectedTriggerIds.clear();
    renderTriggerOptions();
    updateTriggerLabel();
    loadCalls();
}

// ── Controls ─────────────────────────────────────────────────────
function initControls() {
    document.getElementById('timeRange').addEventListener('change', () => {
        const timeRange = document.getElementById('timeRange');
        const customWrap = document.getElementById('customDateWrap');
        if (timeRange.value === 'custom') {
            customWrap.classList.remove('d-none');
        } else {
            customWrap.classList.add('d-none');
            loadCalls();
            if (socket && socket.connected) {
                if (timeRange.value === 'midnight') {
                    socket.emit('subscribe', { from: torontoMidnightEpoch() });
                } else {
                    socket.emit('subscribe', { hours: parseFloat(timeRange.value) });
                }
            }
        }
    });

    document.getElementById('customDateApply').addEventListener('click', () => {
        loadCalls();
        if (socket && socket.connected) {
            const dateFrom = document.getElementById('dateFrom').value;
            const dateTo = document.getElementById('dateTo').value;
            const sub = {};
            if (dateFrom) sub.from = Math.floor(new Date(dateFrom + 'T00:00').getTime() / 1000);
            if (dateTo) sub.to = Math.floor(new Date(dateTo + 'T23:59:59').getTime() / 1000);
            if (!sub.from && !sub.to) sub.hours = 24;
            socket.emit('subscribe', sub);
        }
    });

    document.getElementById('dateFrom').addEventListener('change', () => {
        if (document.getElementById('timeRange').value === 'custom') {
            document.getElementById('customDateApply').click();
        }
    });

    document.getElementById('dateTo').addEventListener('change', () => {
        if (document.getElementById('timeRange').value === 'custom') {
            document.getElementById('customDateApply').click();
        }
    });

    document.getElementById('systemFilter').addEventListener('change', () => {
        selectedTriggerIds.clear();
        loadTriggers(document.getElementById('systemFilter').value);
        loadCalls();
    });

    // Trigger filter dropdown
    document.getElementById('triggerFilterBtn').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleTriggerDropdown();
    });
    document.getElementById('triggerFilterDropdown').addEventListener('click', (e) => {
        e.stopPropagation();
    });
    document.getElementById('triggerSelectAll').addEventListener('click', selectAllTriggers);
    document.getElementById('triggerClearAll').addEventListener('click', clearAllTriggers);
    document.addEventListener('click', () => {
        closeTriggerDropdown();
    });

    document.querySelectorAll('.inc-filter').forEach(cb => {
        cb.addEventListener('change', applyFilters);
    });

    document.getElementById('closeSidebar').addEventListener('click', closeSidebar);
    document.getElementById('fitBoundsBtn').addEventListener('click', fitBounds);
    document.getElementById('cancelAutoFit').addEventListener('click', cancelAutoFit);

    document.getElementById('fullscreenBtn').addEventListener('click', () => {
        const el = document.documentElement;
        if (!document.fullscreenElement) {
            el.requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    });

    document.getElementById('themeToggle').addEventListener('click', toggleTheme);
    document.getElementById('heatmapToggle').addEventListener('click', toggleHeatmap);
    document.getElementById('muteToggle').addEventListener('click', toggleMute);
    document.getElementById('liveFeedToggle').addEventListener('click', toggleLiveFeed);
    document.getElementById('testBtn').addEventListener('click', injectTestCall);
    document.getElementById('helpBtn').addEventListener('click', toggleHelpModal);

    document.getElementById('searchAddressBtn').addEventListener('click', searchAddress);
    document.getElementById('closeAddressSearch').addEventListener('click', closeAddressSearch);
    document.getElementById('addressSearchForm').addEventListener('submit', submitAddressSearch);
    document.getElementById('myLocationBtn').addEventListener('click', () => {
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                pos => map.setView([pos.coords.latitude, pos.coords.longitude], 14),
                () => showToastMsg('Location access denied', 'danger')
            );
        }
    });

    document.getElementById('tickerToggle').addEventListener('click', () => {
        document.getElementById('tickerBar').classList.toggle('hidden');
    });
    document.getElementById('filterDrawerToggle').addEventListener('click', () => {
        document.getElementById('incidentBar').classList.toggle('filters-collapsed');
    });

    // Check URL for permalink
    const params = new URLSearchParams(window.location.search);
    const callId = params.get('call');
    if (callId) {
        fetch(`/api/calls/${callId}`)
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    showCallDetail(data.result);
                    if (data.result.lat && data.result.lng) {
                        map.setView([data.result.lat, data.result.lng], 16);
                    }
                }
            })
            .catch(console.error);
    }

    // Load systems for filter
    fetch('/api/calls?hours=72')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const systems = new Map();
                data.result.forEach(c => {
                    if (c.system_id && c.system_name) {
                        systems.set(c.system_id, c.system_name);
                    }
                });
                const sel = document.getElementById('systemFilter');
                systems.forEach((name, id) => {
                    const opt = document.createElement('option');
                    opt.value = id;
                    opt.textContent = name;
                    sel.appendChild(opt);
                });
            }
            // Load all triggers initially (no system filter)
            loadTriggers();
        })
        .catch(console.error);

    // Keyboard shortcuts
    document.addEventListener('keydown', handleKeydown);

    // Refresh data when user returns to tab (browser may have suspended socket)
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && socket && socket.connected) {
            loadCalls();
        }
    });

    // Fallback polling every 30s
    setInterval(() => {
        if (!socket || !socket.connected) {
            loadCalls();
        }
    }, REFRESH_INTERVAL);
}

function handleKeydown(e) {
    // Ignore if typing in an input
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    switch (e.key) {
        case '?':
            e.preventDefault();
            toggleHelpModal();
            break;
        case 'Escape':
            closeSidebar();
            closeHelpModal();
            document.getElementById('notifPanel').classList.remove('open');
            break;
        case 'f':
        case 'F':
            e.preventDefault();
            fitBounds();
            cancelAutoFit();
            break;
        case 'm':
        case 'M':
            e.preventDefault();
            toggleMute();
            break;
        case 'l':
        case 'L':
            e.preventDefault();
            toggleLiveFeed();
            break;
        case 'n':
        case 'N':
            e.preventDefault();
            toggleNotifPanel();
            break;
        case 't':
        case 'T':
            e.preventDefault();
            injectTestCall();
            break;
    }
}

function toggleTheme() {
    isDarkTheme = !isDarkTheme;
    map.eachLayer(layer => {
        if (layer instanceof L.TileLayer) map.removeLayer(layer);
    });
    if (isDarkTheme) {
        addDarkTiles();
    } else {
        addLightTiles();
    }
    localStorage.setItem('mapTheme', isDarkTheme ? 'dark' : 'light');
}

function toggleHeatmap() {
    isHeatmap = !isHeatmap;
    const btn = document.getElementById('heatmapToggle');
    const container = document.getElementById('mapContainer');

    if (isHeatmap) {
        btn.classList.add('active');
        container.classList.add('heatmap-active');
        map.removeLayer(markersLayer);
        heatLayer.addTo(map);
    } else {
        btn.classList.remove('active');
        container.classList.remove('heatmap-active');
        map.removeLayer(heatLayer);
        markersLayer.addTo(map);
    }
}

function toggleMute() {
    isMuted = !isMuted;
    const btn = document.getElementById('muteToggle');
    if (isMuted) {
        btn.innerHTML = '<i class="bi bi-volume-mute-fill"></i>';
        btn.title = 'Unmute';
    } else {
        btn.innerHTML = '<i class="bi bi-volume-up-fill"></i>';
        btn.title = 'Mute';
        // Resume audio context if needed
        if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
    }
    localStorage.setItem('mapMuted', isMuted ? '1' : '0');
}

function toggleLiveFeed() {
    isLiveFeed = !isLiveFeed;
    const btn = document.getElementById('liveFeedToggle');
    const label = document.getElementById('liveFeedLabel');
    const badge = document.getElementById('liveFeedBadge');

    if (isLiveFeed) {
        btn.classList.add('active-danger');
        label.textContent = 'Live Feed';
        badge.classList.remove('d-none');
        document.getElementById('timeRange').disabled = true;
        // In Live Feed, disable incident filters visually but keep them
    } else {
        btn.classList.remove('active-danger');
        label.textContent = 'All Calls';
        badge.classList.add('d-none');
        document.getElementById('timeRange').disabled = false;
    }

    applyFilters();
    if (!isLiveFeed) fitBounds();
}

function injectTestCall() {
    // Resume audio context on user interaction
    if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();

    testCallId--;
    const lat = RENFREW_CENTER[0] + (Math.random() - 0.5) * 0.5;
    const lng = RENFREW_CENTER[1] + (Math.random() - 0.5) * 0.8;
    const now = Math.floor(Date.now() / 1000);

    const testCall = {
        call_id: testCallId,
        timestamp: now,
        datetime: new Date(now * 1000).toLocaleString(),
        duration_s: 45.0,
        talkgroup: 410837,
        talkgroup_name: 'PAGING',
        system_id: 1,
        system_name: 'Renfrew County Fire',
        transcript: 'This is a TEST call. Station 1, respond to 123 Test Street for a structure fire. This is only a test of the notification system.',
        incident_category: 'Fire',
        lat: lat,
        lng: lng,
        address: '123 Test Street, Testville, ON',
        audio_url: '',
        has_location: true,
        is_corrected: false,
        correction_notes: '',
    };

    // Insert and trigger full alert chain
    currentCalls.unshift(testCall);
    if (currentCalls.length > 2000) currentCalls = currentCalls.slice(0, 2000);

    addNotification(testCall);

    if (isLiveFeed) {
        applyFilters();
        panToCall(testCall);
        updateUrlHash(testCall.call_id);
        showCallDetail(testCall);
    } else {
        applyFilters();
        panToCall(testCall);
    }

    showToastsForNewCalls([testCall]);
    playNotificationSound();

    // Auto-remove test call after 30 seconds
    setTimeout(() => {
        const idx = currentCalls.findIndex(c => c.call_id === testCallId);
        if (idx >= 0) {
            currentCalls.splice(idx, 1);
            applyFilters();
            if (selectedCallId === testCallId) closeSidebar();
        }
    }, 30000);
}

function searchAddress() {
    document.getElementById('addressSearchPanel').classList.remove('d-none');
    document.getElementById('addressSearchInput').focus();
}

function closeAddressSearch() {
    document.getElementById('addressSearchPanel').classList.add('d-none');
}

async function submitAddressSearch(event) {
    event.preventDefault();
    const query = document.getElementById('addressSearchInput').value.trim();
    if (query.length < 3) return;
    const resultsEl = document.getElementById('addressSearchResults');
    resultsEl.innerHTML = '<div class="address-search-result">Searching...</div>';
    try {
        const response = await fetch(`/api/geocode/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();
        if (!data.success) throw new Error(data.message || 'Search failed');
        resultsEl.innerHTML = data.result.length ? data.result.map(result => `<button class="address-search-result" type="button" data-lat="${Number(result.lat)}" data-lng="${Number(result.lng)}">${esc(result.display_name)}</button>`).join('') : '<div class="address-search-result">No matching address found.</div>';
        resultsEl.querySelectorAll('[data-lat]').forEach(button => button.addEventListener('click', () => {
            map.setView([Number(button.dataset.lat), Number(button.dataset.lng)], 16);
            closeAddressSearch();
        }));
    } catch (error) {
        resultsEl.innerHTML = `<div class="address-search-result">${esc(error.message)}</div>`;
    }
}

// ── Utilities ─────────────────────────────────────────────────────
function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatTime(epoch) {
    if (!epoch) return '—';
    const d = new Date(epoch * 1000);
    return d.toLocaleString();
}

function timeAgo(epoch) {
    const seconds = Math.floor(Date.now() / 1000 - epoch);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

function debounce(fn, ms) {
    let t;
    return (...args) => {
        clearTimeout(t);
        t = setTimeout(() => fn(...args), ms);
    };
}

// ── Restore preferences on load ────────────────────────────────────
function restorePreferences() {
    const savedTheme = localStorage.getItem('mapTheme');
    if (savedTheme === 'light') {
        isDarkTheme = true; // toggleTheme will flip it
        toggleTheme();
    }

    const savedMute = localStorage.getItem('mapMuted');
    if (savedMute === '1') {
        isMuted = false; // toggleMute will flip it
        toggleMute();
    }
}

// ── Boot ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    init();
    restorePreferences();
});
