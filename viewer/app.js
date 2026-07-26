// Initialize Map with Canvas renderer for high-performance point rendering
const map = L.map('map', {
    preferCanvas: true,
    zoomControl: false
}).setView([37.7749, -122.4194], 12);

L.control.zoom({ position: 'bottomright' }).addTo(map);

// Switch to CartoDB Voyager for clear street labels without file:// referer restrictions
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 20
}).addTo(map);

// Layer Groups
const originalLayer = L.layerGroup().addTo(map);
const cleanedLayer = L.layerGroup().addTo(map);
const pointsLayer = L.layerGroup().addTo(map);

// DOM Elements
const fileInput = document.getElementById('geojson-upload');
const toggleOriginal = document.getElementById('toggle-original');
const toggleCleaned = document.getElementById('toggle-cleaned');
const togglePoints = document.getElementById('toggle-points');
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

togglePoints.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(pointsLayer);
    else map.removeLayer(pointsLayer);
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
    pointsLayer.clearLayers();

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
        let pointColor = '#2563eb';

        if (status === 'original') {
            style = {
                color: '#ef4444',
                weight: 5,
                opacity: 0.4,
                dashArray: '5, 5'
            };
            targetLayer = originalLayer;
            pointColor = '#ef4444';
            originalCount++;
        } else {
            style = {
                color: '#2563eb',
                weight: 6,
                opacity: 0.5
            };
            targetLayer = cleanedLayer;
            pointColor = '#2563eb';
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
        
        // Render Individual Interactive Trace Points ONLY when a single route is selected and toggle is checked
        const shouldRenderPoints = allowedShapeIds !== null && togglePoints.checked;
        if (shouldRenderPoints && feature.geometry && feature.geometry.coordinates) {
            const coordsList = feature.geometry.type === 'LineString' ? feature.geometry.coordinates : [];
            
            coordsList.forEach((coord, idx) => {
                const lon = coord[0];
                const lat = coord[1];
                
                const marker = L.circleMarker([lat, lon], {
                    radius: 4,
                    fillColor: pointColor,
                    color: '#ffffff',
                    weight: 1.5,
                    opacity: 0.9,
                    fillOpacity: 0.85
                });
                
                const tooltipContent = `
                    <div style="font-family: Inter, sans-serif; font-size: 12px; line-height: 1.4;">
                        <strong>Point #${idx + 1}</strong> of ${coordsList.length}<br>
                        <strong>Shape ID:</strong> ${shapeId}<br>
                        <strong>Status:</strong> <span style="color:${pointColor}; font-weight:600;">${status}</span><br>
                        <strong>Lat:</strong> ${lat.toFixed(5)}<br>
                        <strong>Lon:</strong> ${lon.toFixed(5)}
                    </div>
                `;
                
                marker.bindTooltip(tooltipContent, {
                    permanent: false,
                    direction: 'top',
                    offset: [0, -4]
                });
                
                // Hover effect: enlarge circle marker
                marker.on('mouseover', function () {
                    this.setRadius(7);
                    this.setStyle({ weight: 2.5, fillOpacity: 1.0 });
                });
                
                marker.on('mouseout', function () {
                    this.setRadius(4);
                    this.setStyle({ weight: 1.5, fillOpacity: 0.85 });
                });
                
                marker.addTo(pointsLayer);
            });
        }
        
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
