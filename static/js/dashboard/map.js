// CAD Map JavaScript
// Dynamic call map with auto-fit, live refresh, and collapsible sidebar

let map;
let markersLayer;
let callsData = [];
let refreshTimer = null;
let sidebarCollapsed = false;

// Renfrew County, Ontario coordinates
const RENFREW_COUNTY_CENTER = [45.45, -77.15];
const DEFAULT_ZOOM = 10;

function initMap() {
    // Check saved sidebar state
    sidebarCollapsed = localStorage.getItem('mapSidebarCollapsed') === 'true';
    if (sidebarCollapsed) {
        document.getElementById('mapSidebar').classList.add('collapsed');
    }
    
    // Initialize Leaflet map centered on Renfrew County
    map = L.map('map', {
        center: RENFREW_COUNTY_CENTER,
        zoom: DEFAULT_ZOOM,
        zoomControl: true
    });
    
    // Add OpenStreetMap tile layer
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
        subdomains: ['a', 'b', 'c']
    }).addTo(map);
    
    // Add zoom control to top-right
    L.control.zoom({
        position: 'topright'
    }).addTo(map);
    
    // Initialize marker cluster group
    markersLayer = L.markerClusterGroup({
        maxClusterRadius: 60,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
        zoomToBoundsOnClick: true
    });
    map.addLayer(markersLayer);
    
    // Set default dates to today
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('startDate').value = today;
    document.getElementById('endDate').value = today;
    
    // Add event listeners
    document.getElementById('startDate').addEventListener('change', loadCalls);
    document.getElementById('endDate').addEventListener('change', loadCalls);
    document.getElementById('categoryFilter').addEventListener('change', filterAndRender);
    document.getElementById('liveMode').addEventListener('change', toggleLiveMode);
    document.getElementById('refreshInterval').addEventListener('change', updateRefreshInterval);
    
    // Handle window resize
    window.addEventListener('resize', () => {
        map.invalidateSize();
    });
    
    // Initial load
    loadCalls();
    
    console.log('CAD Map initialized - Centered on Renfrew County');
}

function toggleSidebar() {
    const sidebar = document.getElementById('mapSidebar');
    sidebarCollapsed = !sidebarCollapsed;
    
    if (sidebarCollapsed) {
        sidebar.classList.add('collapsed');
    } else {
        sidebar.classList.remove('collapsed');
    }
    
    // Save state
    localStorage.setItem('mapSidebarCollapsed', sidebarCollapsed);
    
    // Resize map after transition
    setTimeout(() => {
        map.invalidateSize();
    }, 350);
}

async function loadCalls() {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value || startDate;
    
    if (!startDate) return;
    
    // Show loading state
    document.getElementById('callList').innerHTML = '<li class="empty-state"><i class="bi bi-hourglass-split"></i><div>Loading calls...</div></li>';
    
    try {
        const url = `/api/map/calls?start_date=${startDate}&end_date=${endDate}`;
        const resp = await fetch(url);
        const data = await resp.json();
        
        if (data.success) {
            callsData = data.result || [];
            filterAndRender();
        } else {
            console.error('API error:', data.message);
            document.getElementById('callList').innerHTML = `<li class="empty-state"><i class="bi bi-exclamation-triangle"></i><div>Error: ${data.message}</div></li>`;
        }
    } catch (err) {
        console.error('Error loading calls:', err);
        document.getElementById('callList').innerHTML = `<li class="empty-state"><i class="bi bi-exclamation-triangle"></i><div>Failed to load calls</div></li>`;
    }
}

function filterAndRender() {
    const category = document.getElementById('categoryFilter').value;
    
    let filtered = callsData;
    if (category) {
        filtered = callsData.filter(c => {
            const cat = (c.category || '').toLowerCase();
            if (category === 'fire') return cat.includes('fire');
            if (category === 'medical') return cat.includes('medical') || cat.includes('ems');
            return true;
        });
    }
    
    renderMarkers(filtered);
    renderCallList(filtered);
    updateStats(filtered.length);
}

function renderMarkers(calls) {
    markersLayer.clearLayers();
    
    const bounds = [];
    let hasMarkers = false;
    
    calls.forEach(call => {
        if (call.lat && call.lng) {
            const color = getCategoryColor(call.category);
            
            // Create custom icon
            const icon = L.divIcon({
                className: 'custom-marker',
                html: `<div style="
                    background: ${color};
                    width: 28px;
                    height: 28px;
                    border-radius: 50%;
                    border: 3px solid #fff;
                    box-shadow: 0 3px 6px rgba(0,0,0,0.4);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-weight: bold;
                    font-size: 12px;
                ">${getCategoryShort(call.category)}</div>`,
                iconSize: [28, 28],
                iconAnchor: [14, 14],
                popupAnchor: [0, -14]
            });
            
            const marker = L.marker([call.lat, call.lng], { icon })
                .bindPopup(createPopupContent(call));
            
            marker.on('click', () => {
                highlightCall(call.call_id);
            });
            
            markersLayer.addLayer(marker);
            bounds.push([call.lat, call.lng]);
            hasMarkers = true;
        }
    });
    
    // Auto-fit map to show all markers
    if (hasMarkers) {
        if (bounds.length === 1) {
            map.setView(bounds[0], 15);
        } else {
            map.fitBounds(bounds, { 
                padding: [50, 50], 
                maxZoom: 16,
                animate: true,
                duration: 0.5
            });
        }
    } else {
        // No calls - center on Renfrew County
        map.setView(RENFREW_COUNTY_CENTER, DEFAULT_ZOOM);
    }
}

function createPopupContent(call) {
    const colorClass = getCategoryClass(call.category);
    return `
        <div style="min-width: 200px;">
            <div style="font-weight: bold; margin-bottom: 5px;">
                <span class="badge-${colorClass}">${call.category}</span>
            </div>
            <div style="font-size: 0.9rem; margin-bottom: 3px;">${call.address || 'Unknown address'}</div>
            <div style="font-size: 0.8rem; color: #666;">
                ${call.time} | ${call.talkgroup}<br>
                ${call.system}
            </div>
        </div>
    `;
}

function renderCallList(calls) {
    const list = document.getElementById('callList');
    
    if (calls.length === 0) {
        list.innerHTML = `
            <li class="empty-state">
                <i class="bi bi-map"></i>
                <div>No calls with location data</div>
            </li>
        `;
        return;
    }
    
    list.innerHTML = calls.map(call => `
        <li class="call-item ${getCategoryClass(call.category)}" 
            data-call-id="${call.call_id}"
            onclick="focusOnCall(${call.call_id}, ${call.lat}, ${call.lng})">
            <div class="call-time">${call.time}</div>
            <div class="call-category">
                <span class="badge badge-${getCategoryClass(call.category)}">${call.category}</span>
            </div>
            <div class="call-address">${call.address || 'No address'}</div>
            <div class="call-system">${call.system} - ${call.talkgroup}</div>
        </li>
    `).join('');
}

function focusOnCall(callId, lat, lng) {
    if (lat && lng) {
        map.setView([lat, lng], 16);
        highlightCall(callId);
        
        // Find and open marker popup
        markersLayer.eachLayer(layer => {
            const markerLat = layer.getLatLng().lat;
            const markerLng = layer.getLatLng().lng;
            if (Math.abs(markerLat - lat) < 0.0001 && Math.abs(markerLng - lng) < 0.0001) {
                layer.openPopup();
            }
        });
    }
}

function highlightCall(callId) {
    document.querySelectorAll('.call-item').forEach(el => {
        el.classList.remove('active');
        if (parseInt(el.dataset.callId) === callId) {
            el.classList.add('active');
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    });
}

function updateStats(count) {
    document.getElementById('callCount').textContent = `${count} call${count !== 1 ? 's' : ''}`;
}

function getCategoryColor(category) {
    const cat = (category || '').toLowerCase();
    if (cat.includes('fire')) return '#dc3545';
    if (cat.includes('medical') || cat.includes('ems')) return '#0d6efd';
    return '#6c757d';
}

function getCategoryClass(category) {
    const cat = (category || '').toLowerCase();
    if (cat.includes('fire')) return 'fire';
    if (cat.includes('medical') || cat.includes('ems')) return 'ems';
    return 'other';
}

function getCategoryShort(category) {
    const cat = (category || '').toLowerCase();
    if (cat.includes('fire')) return 'F';
    if (cat.includes('medical') || cat.includes('ems')) return 'E';
    return 'C';
}

function toggleLiveMode() {
    const liveMode = document.getElementById('liveMode').checked;
    const indicator = document.getElementById('liveIndicator');
    
    if (liveMode) {
        indicator.classList.add('active');
        loadCalls();
        updateRefreshInterval();
    } else {
        indicator.classList.remove('active');
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }
}

function updateRefreshInterval() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
    }
    
    const interval = parseInt(document.getElementById('refreshInterval').value) * 1000;
    const liveMode = document.getElementById('liveMode').checked;
    
    if (liveMode && interval > 0) {
        refreshTimer = setInterval(loadCalls, interval);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', initMap);
