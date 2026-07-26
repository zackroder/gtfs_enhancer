// Initialize Map with a dark theme (CartoDB Dark Matter)
const map = L.map('map', {
    zoomControl: false // Custom position if needed, or hide
}).setView([37.7749, -122.4194], 12); // Default center, will fitBounds on load

L.control.zoom({ position: 'bottomright' }).addTo(map);

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 20
}).addTo(map);

// Layer Groups
const originalLayer = L.layerGroup().addTo(map);
const cleanedLayer = L.layerGroup().addTo(map);

// DOM Elements
const fileInput = document.getElementById('geojson-upload');
const toggleOriginal = document.getElementById('toggle-original');
const toggleCleaned = document.getElementById('toggle-cleaned');
const statTotal = document.getElementById('stat-total-shapes');
const routeSelector = document.getElementById('route-selector');

// Global State
let currentFeatures = [];
let currentRouteMapping = {};

// Handle File Upload
fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
        try {
            const geojson = JSON.parse(event.target.result);
            processGeoJSON(geojson);
        } catch (err) {
            alert('Failed to parse GeoJSON file. Ensure it is valid JSON.');
            console.error(err);
        }
    };
    reader.readAsText(file);
});

// Toggle Handlers
toggleOriginal.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(originalLayer);
    else map.removeLayer(originalLayer);
});

toggleCleaned.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(cleanedLayer);
    else map.removeLayer(cleanedLayer);
});

// Route Selector Handler
routeSelector.addEventListener('change', (e) => {
    const selectedRoute = e.target.value;
    if (selectedRoute === 'all') {
        renderFeatures(null);
    } else {
        const shapeIds = currentRouteMapping[selectedRoute] || [];
        renderFeatures(shapeIds);
    }
});

function processGeoJSON(data) {
    if (!data.features || data.features.length === 0) {
        alert("No features found in the GeoJSON.");
        return;
    }

    currentFeatures = data.features;
    currentRouteMapping = data.route_mapping || {};

    // Populate Route Selector
    routeSelector.innerHTML = '<option value="all">All Routes</option>';
    const routeNames = Object.keys(currentRouteMapping).sort();
    routeNames.forEach(route => {
        const option = document.createElement('option');
        option.value = route;
        option.textContent = route;
        routeSelector.appendChild(option);
    });

    // Render all initially
    renderFeatures(null);
}

function renderFeatures(allowedShapeIds) {
    // Clear existing layers
    originalLayer.clearLayers();
    cleanedLayer.clearLayers();

    let originalCount = 0;
    const bounds = L.latLngBounds();

    currentFeatures.forEach(feature => {
        const shapeId = String(feature.properties?.shape_id);
        
        // Skip if we have a filter and this shape isn't in it
        if (allowedShapeIds && !allowedShapeIds.includes(shapeId)) {
            return;
        }

        const status = feature.properties?.status || 'cleaned';
        
        let style = {};
        let targetLayer = null;

        if (status === 'original') {
            style = {
                color: '#ef4444',
                weight: 4,
                opacity: 0.6,
                dashArray: '5, 5'
            };
            targetLayer = originalLayer;
            originalCount++;
        } else {
            style = {
                color: '#10b981',
                weight: 5,
                opacity: 0.9
            };
            targetLayer = cleanedLayer;
        }

        const layer = L.geoJSON(feature, {
            style: style,
            onEachFeature: (f, l) => {
                const props = f.properties || {};
                let popupContent = `<strong>Shape ID:</strong> ${props.shape_id || 'Unknown'}<br>`;
                popupContent += `<strong>Status:</strong> ${props.status || 'Cleaned'}`;
                l.bindPopup(popupContent);
            }
        });

        layer.addTo(targetLayer);
        
        // Extend bounds
        const layerBounds = layer.getBounds();
        if (layerBounds.isValid()) {
            bounds.extend(layerBounds);
        }
    });

    // Update Stats
    statTotal.textContent = originalCount > 0 ? originalCount : (currentFeatures.length / 2);

    // Fit map to data
    if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [50, 50] });
    }
}
