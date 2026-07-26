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

function processGeoJSON(data) {
    // Clear existing layers
    originalLayer.clearLayers();
    cleanedLayer.clearLayers();

    if (!data.features || data.features.length === 0) {
        alert("No features found in the GeoJSON.");
        return;
    }

    let originalCount = 0;
    const bounds = L.latLngBounds();

    data.features.forEach(feature => {
        const status = feature.properties?.status || 'cleaned'; // Default to cleaned if missing
        
        let style = {};
        let targetLayer = null;

        if (status === 'original') {
            style = {
                color: '#ef4444',
                weight: 4,
                opacity: 0.6,
                dashArray: '5, 5' // Dashed line to differentiate when overlapping
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

    // Update Stats (Count of unique shape_ids = count of original shapes)
    statTotal.textContent = originalCount > 0 ? originalCount : data.features.length;

    // Fit map to data
    if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [50, 50] });
    }
}
