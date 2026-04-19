let map;
let markersLayer;
let callsData = [];
let refreshTimer = null;

function initMap() {
    // Initialize Leaflet map
    map = L.map('map').setView([43.0, -71.5], 10);
    
    // Add OpenStreetMap tile layer
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19
    }).addTo(map);
    
    // Initialize marker cluster group
    markersLayer = L.markerClusterGroup({
        maxClusterRadius: 50,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false
    });
    map.addLayer(markersLayer);
    
    // Set default dates
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('startDate').value = today;
    document.getElementById('endDate').value = today;
    
    // Add event listeners
    document.getElementById('startDate').addEventListener('change', loadCalls);
    document.getElementById('endDate').addEventListener('change', loadCalls);
    document.getElementById('categoryFilter').addEventListener('change', filterCalls);
    document.getElementById('liveMode').addEventListener('change', toggleLiveMode);
    document.getElementById('refreshInterval').addEventListener('change', updateRefreshInterval);
    
    // Initial load
    loadCalls();
}

async function loadCalls() {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value || startDate;
    const category = document.getElementById('categoryFilter').value;
    
    let url = `/api/map/calls?start_date=${startDate}&end_date=${endDate}`;
    if (category) {
        url += `&category=${category}`;
    }
    
    try {
        const resp = await fetch(url);
        const data = await resp.json();
        
        if (data.success) {
            callsData = data.result || [];
            renderMarkers();
            renderCallList();
            updateStats();
        }
    } catch (err) {
        console.error('Error loading calls:', err);
    }
}

function renderMarkers() {
    markersLayer.clearLayers();
    
    const bounds = [];
    
    callsData.forEach(call => {
        if (call.lat && call.lng) {
            const color = getCategoryColor(call.category);
            
            const icon = L.divIcon({
                className: 'custom-marker',
                html: `<div style="
                    background: ${color};
                    width: 24px;
                    height: 24px;
                    border-radius: 50%;
                    border: 2px solid #fff;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.3);
                "></div>`,
                iconSize: [24, 24],
                iconAnchor: [12, 12]
            });
            
            const marker = L.marker([call.lat, call.lng], { icon })
                .bindPopup(`
                    <strong>${call.category}</strong><br>
                    ${call.address || 'Unknown'}<br>
                    ${call.time} - ${call.talkgroup}<br>
                    System: ${call.system}
                `);
            
            marker.on('click', () => {
                document.querySelectorAll('.call-item').forEach(el => el.classList.remove('active'));
                const item = document.querySelector(`[data-call-id="${call.call_id}"]`);
                if (item) item.classList.add('active');
            });
            
            markersLayer.addLayer(marker);
            bounds.push([call.lat, call.lng]);
        }
    });
    
    // Auto-fit map to show all markers
    if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [50, 50], maxZoom: 14 });
    }
}

function renderCallList() {
    const list = document.getElementById('callList');
    list.innerHTML = '';
    
    callsData.forEach(call => {
        const li = document.createElement('li');
        li.className = `call-item ${getCategoryClass(call.category)}`;
        li.dataset.callId = call.call_id;
        li.innerHTML = `
            <div class="call-time">${call.time}</div>
            <div class="call-category">${call.category}</div>
            <div class="call-address">${call.address || 'No address'}</div>
            <div class="call-system">${call.system} - ${call.talkgroup}</div>
        `;
        
        li.addEventListener('click', () => {
            // Highlight in list
            document.querySelectorAll('.call-item').forEach(el => el.classList.remove('active'));
            li.classList.add('active');
            
            // Pan to marker on map
            if (call.lat && call.lng) {
                map.setView([call.lat, call.lng], 14);
            }
        });
        
        list.appendChild(li);
    });
}

function filterCalls() {
    const category = document.getElementById('categoryFilter').value;
    
    if (!category) {
        renderMarkers();
        renderCallList();
    } else {
        const filtered = callsData.filter(c => 
            c.category.toLowerCase().includes(category.toLowerCase()) ||
            (category === 'medical' && c.category.toLowerCase().includes('ems'))
        );
        const tempData = callsData;
        callsData = filtered;
        renderMarkers();
        renderCallList();
        callsData = tempData;
    }
    updateStats();
}

function updateStats() {
    document.getElementById('callCount').textContent = `${callsData.length} calls`;
}

function getCategoryColor(category) {
    const cat = (category || '').toLowerCase();
    if (cat.includes('fire') || cat.includes('fire')) return '#dc3545';
    if (cat.includes('medical') || cat.includes('ems')) return '#0d6efd';
    return '#6c757d';
}

function getCategoryClass(category) {
    const cat = (category || '').toLowerCase();
    if (cat.includes('fire')) return 'fire';
    if (cat.includes('medical') || cat.includes('ems')) return 'ems';
    return 'other';
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
