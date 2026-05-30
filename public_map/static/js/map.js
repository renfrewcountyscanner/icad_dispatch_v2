/* public_map/static/js/map.js */
/* Public Live Emergency Map Frontend */

// ── Config ─────────────────────────────────────────────────────────
const RENFREW_CENTER = [45.4748, -77.6972];
const DEFAULT_ZOOM = 10;
const REFRESH_INTERVAL = 30000; // 30s fallback poll

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
let callMarkers = new Map(); // call_id -> marker
let isHeatmap = false;
let isDarkTheme = true;
let socket;
let audioCtx;
let lastCallId = 0;
let selectedCallId = null;

// ── Init ───────────────────────────────────────────────────────────
function init() {
    initAudioContext();
    initMap();
    initSocket();
    initControls();
    loadCalls();
}

function initAudioContext() {
    try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) {
        console.warn("Web Audio not supported");
    }
}

function playNotificationSound() {
    if (!audioCtx) return;
    try {
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

    // Heat layer (empty initially)
    heatLayer = L.heatLayer([], {
        radius: 25,
        blur: 15,
        maxZoom: 14,
        gradient: { 0.4: 'blue', 0.6: 'cyan', 0.7: 'lime', 0.8: 'yellow', 1: 'red' }
    });
}

function addDarkTiles() {
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OSM &copy; CARTO',
        subdomains: 'abcd',
        maxZoom: 20
    }).addTo(map);
}

function addLightTiles() {
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 20
    }).addTo(map);
}

// ── SocketIO ─────────────────────────────────────────────────────
function initSocket() {
    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
        setLiveStatus('ONLINE');
        const hours = document.getElementById('timeRange').value;
        socket.emit('subscribe', { hours: parseFloat(hours) });
    });

    socket.on('disconnect', () => {
        setLiveStatus('OFFLINE');
    });

    socket.on('new_calls', (payload) => {
        if (payload.calls && payload.calls.length) {
            handleNewCalls(payload.calls);
        }
    });

    socket.on('call_updated', (payload) => {
        // Admin correction updated a call
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

// ── Data Loading ───────────────────────────────────────────────────
async function loadCalls() {
    const hours = document.getElementById('timeRange').value;
    const systemId = document.getElementById('systemFilter').value;
    const talkgroup = document.getElementById('tgFilter').value.trim();

    const params = new URLSearchParams({ hours });
    if (systemId) params.append('system_id', systemId);
    if (talkgroup) params.append('talkgroup', talkgroup);

    try {
        const resp = await fetch(`/api/calls?${params}`);
        const data = await resp.json();
        if (!data.success) throw new Error(data.message);

        currentCalls = data.result || [];
        updateLastCallId();
        applyFilters();
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
function applyFilters() {
    const activeIncidents = new Set(
        Array.from(document.querySelectorAll('.inc-filter:checked')).map(cb => cb.value)
    );

    visibleCalls = currentCalls.filter(c => {
        const inc = c.incident_category || 'Other';
        return activeIncidents.has(inc);
    });

    renderMarkers();
    updateStats();
    updateTicker();
    fitBounds();
}

function renderMarkers() {
    markersLayer.clearLayers();
    callMarkers.clear();

    const heatPoints = [];

    visibleCalls.forEach(call => {
        if (!call.lat || !call.lng) return;

        const color = INCIDENT_COLORS[call.incident_category] || INCIDENT_COLORS.Other;
        const short = INCIDENT_SHORT[call.incident_category] || '?';

        // Age-based opacity (0-24h scale)
        const ageHours = (Date.now() / 1000 - call.timestamp) / 3600;
        const opacity = Math.max(0.3, 1 - (ageHours / 72));

        const markerHtml = `<div class="custom-marker" style="background:${color.bg};opacity:${opacity};border-color:${color.bg}"><span>${short}</span></div>`;
        const icon = L.divIcon({
            className: '',
            html: markerHtml,
            iconSize: [28, 28],
            iconAnchor: [14, 28]
        });

        const marker = L.marker([call.lat, call.lng], { icon }).addTo(markersLayer);

        // Popup
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
            showCallDetail(call);
        });

        callMarkers.set(call.call_id, marker);
        heatPoints.push([call.lat, call.lng, 0.6]);
    });

    // Update heatmap
    heatLayer.setLatLngs(heatPoints);
}

function refreshCallMarker(callId) {
    // Re-fetch a single call and update its marker
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

// ── New Call Handling ─────────────────────────────────────────────
function handleNewCalls(calls) {
    let added = 0;
    calls.forEach(call => {
        if (call.call_id > lastCallId) {
            currentCalls.unshift(call);
            lastCallId = Math.max(lastCallId, call.call_id);
            added++;
        }
    });

    if (added > 0) {
        // Keep max 2000 calls
        if (currentCalls.length > 2000) {
            currentCalls = currentCalls.slice(0, 2000);
        }
        applyFilters();
        showToastForNewCalls(calls.slice(0, added));
        playNotificationSound();
    }
}

function showToastForNewCalls(calls) {
    calls.forEach(call => {
        const inc = call.incident_category || 'Other';
        const color = INCIDENT_COLORS[inc] || INCIDENT_COLORS.Other;
        const toast = document.createElement('div');
        toast.className = `toast-item toast-${inc.toLowerCase()}`;
        toast.innerHTML = `
            <div style="font-weight:600;margin-bottom:0.2rem">${esc(inc)} Call</div>
            <div style="font-size:0.8rem;color:#ccc">${esc(call.address || '—')}</div>
            <div style="font-size:0.75rem;color:#888;margin-top:0.2rem">${esc(call.system_name || '')} • ${formatTime(call.timestamp)}</div>
        `;
        document.getElementById('toastContainer').appendChild(toast);
        setTimeout(() => toast.remove(), 6000);
    });
}

// ── Sidebar Detail ────────────────────────────────────────────────
function showCallDetail(call) {
    selectedCallId = call.call_id;
    const sidebar = document.getElementById('sidebar');
    const content = document.getElementById('sidebarContent');

    const inc = call.incident_category || 'Other';
    const color = INCIDENT_COLORS[inc] || INCIDENT_COLORS.Other;

    content.innerHTML = `
        <div class="call-detail">
            <div class="detail-header">
                <div class="detail-id">Call #${call.call_id}</div>
                <div class="detail-time">${formatTime(call.timestamp)} <span style="color:#888">(${timeAgo(call.timestamp)})</span></div>
                <div>
                    <span class="detail-badge badge-${inc.toLowerCase()}">${esc(inc)}</span>
                    ${call.is_corrected ? '<span class="detail-badge" style="background:rgba(255,193,7,0.2);color:#fcd34d">Corrected</span>' : ''}
                </div>
            </div>

            <div class="detail-section">
                <div class="section-label">Address</div>
                <div class="section-value">${esc(call.address || '—')}</div>
            </div>

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
    updateUrlHash(call.call_id);
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
        showToast('Link copied to clipboard', 'success');
    }).catch(() => {
        showToast('Failed to copy link', 'danger');
    });
}

function showToast(message, type) {
    const toast = document.createElement('div');
    toast.className = 'toast-item';
    if (type === 'success') toast.style.borderLeftColor = '#198754';
    if (type === 'danger') toast.style.borderLeftColor = '#dc3545';
    toast.innerHTML = `<div>${esc(message)}</div>`;
    document.getElementById('toastContainer').appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
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
        map.setView(points[0], 15);
        return;
    }
    const bounds = L.latLngBounds(points);
    map.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
}

// ── Controls ─────────────────────────────────────────────────────
function initControls() {
    document.getElementById('timeRange').addEventListener('change', () => {
        loadCalls();
        if (socket && socket.connected) {
            socket.emit('subscribe', { hours: parseFloat(document.getElementById('timeRange').value) });
        }
    });

    document.getElementById('systemFilter').addEventListener('change', loadCalls);
    document.getElementById('tgFilter').addEventListener('input', debounce(loadCalls, 400));

    document.querySelectorAll('.inc-filter').forEach(cb => {
        cb.addEventListener('change', applyFilters);
    });

    document.getElementById('closeSidebar').addEventListener('click', closeSidebar);

    document.getElementById('fitBoundsBtn').addEventListener('click', fitBounds);

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

    document.getElementById('searchAddressBtn').addEventListener('click', searchAddress);
    document.getElementById('myLocationBtn').addEventListener('click', () => {
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                pos => map.setView([pos.coords.latitude, pos.coords.longitude], 14),
                () => showToast('Location access denied', 'danger')
            );
        }
    });

    document.getElementById('tickerToggle').addEventListener('click', () => {
        document.getElementById('tickerBar').classList.toggle('hidden');
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
        })
        .catch(console.error);

    // Fallback polling every 30s
    setInterval(() => {
        if (!socket || !socket.connected) {
            loadCalls();
        }
    }, REFRESH_INTERVAL);
}

function toggleTheme() {
    isDarkTheme = !isDarkTheme;
    // Remove existing tiles
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

function searchAddress() {
    const q = prompt('Search for an address or location:');
    if (!q) return;
    fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`)
        .then(r => r.json())
        .then(results => {
            if (results && results.length) {
                const r = results[0];
                map.setView([parseFloat(r.lat), parseFloat(r.lon)], 16);
            } else {
                showToast('Address not found', 'warning');
            }
        })
        .catch(() => showToast('Search failed', 'danger'));
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

// ── Boot ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
