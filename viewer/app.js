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
const simplifiedLayer = L.layerGroup().addTo(map);
const spikeLayer = L.layerGroup().addTo(map);
const cleanedLayer = L.layerGroup().addTo(map);
const pointsLayer = L.layerGroup().addTo(map);

// DOM Elements
const fileInput = document.getElementById('geojson-upload');
const toggleOriginal = document.getElementById('toggle-original');
const toggleSimplified = document.getElementById('toggle-simplified');
const toggleSpikeRemoved = document.getElementById('toggle-spike-removed');
const toggleCleaned = document.getElementById('toggle-cleaned');
const togglePoints = document.getElementById('toggle-points');
const statTotal = document.getElementById('stat-total-shapes');
const statClean = document.getElementById('stat-clean');
const statSuspect = document.getElementById('stat-suspect');
const statReview = document.getElementById('stat-review');
const routeSelector = document.getElementById('route-selector');
const shapeSelector = document.getElementById('shape-selector');
const qualitySelector = document.getElementById('quality-selector');

// Global State
let currentFeatures = [];
let currentRouteMapping = {};

// Match status -> line color and review bucket
const STATUS_COLORS = {
    clean: '#2563eb',
    suspect: '#f59e0b',
    untrusted: '#dc2626',
    failed: '#6b7280'
};

function bucketForStatus(status) {
    if (status === 'clean') return 'clean';
    if (status === 'suspect') return 'suspect';
    return 'review';
}

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

toggleSimplified.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(simplifiedLayer);
    else map.removeLayer(simplifiedLayer);
});

toggleSpikeRemoved.addEventListener('change', (e) => {
    if (e.target.checked) map.addLayer(spikeLayer);
    else map.removeLayer(spikeLayer);
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
    populateShapeSelector();
    renderFeatures();
});

// Match Quality Selector Handler
qualitySelector.addEventListener('change', () => renderFeatures());
shapeSelector.addEventListener('change', () => renderFeatures());

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

    populateShapeSelector();

    // Render all initially
    renderFeatures();
}

function shapeIdsForSelectedRoute() {
    const selectedRoute = routeSelector.value;
    if (selectedRoute === 'all') {
        return [...new Set(currentFeatures.map(f => String(f.properties?.shape_id)))];
    }
    return currentRouteMapping[selectedRoute] || [];
}

function populateShapeSelector() {
    const routeShapeIds = new Set(shapeIdsForSelectedRoute());
    const currentShape = shapeSelector.value;
    const shapeIds = [...new Set(currentFeatures
        .map(f => String(f.properties?.shape_id))
        .filter(id => routeShapeIds.has(id)))].sort();

    shapeSelector.innerHTML = '<option value="all">All Shapes</option>';
    shapeIds.forEach(shapeId => {
        const option = document.createElement('option');
        option.value = shapeId;
        option.textContent = shapeId;
        shapeSelector.appendChild(option);
    });

    if (shapeIds.includes(currentShape)) {
        shapeSelector.value = currentShape;
    }
}

function renderFeatures() {
    // Clear existing layers
    originalLayer.clearLayers();
    simplifiedLayer.clearLayers();
    spikeLayer.clearLayers();
    cleanedLayer.clearLayers();
    pointsLayer.clearLayers();

    const qualityFilter = qualitySelector.value;
    const routeShapeIds = new Set(shapeIdsForSelectedRoute());
    const selectedShape = shapeSelector.value;

    // Quality applies to a complete shape, so intermediate layers remain visible
    // when a cleaned shape passes the quality filter.
    const qualityShapeIds = new Set(currentFeatures
        .filter(f => f.properties?.status === 'cleaned' || f.properties?.status === 'cleaned_fallback')
        .filter(f => qualityFilter === 'all' || (f.properties?.match_status || 'failed') === qualityFilter)
        .map(f => String(f.properties?.shape_id)));

    const allowedShapeIds = new Set([...routeShapeIds]
        .filter(id => selectedShape === 'all' || id === selectedShape)
        .filter(id => qualityFilter === 'all' || qualityShapeIds.has(id)));
    let originalCount = 0;
    let cleanCount = 0;
    let suspectCount = 0;
    let reviewCount = 0;
    const bounds = L.latLngBounds();

    currentFeatures.forEach(feature => {
        const shapeId = String(feature.properties?.shape_id);
        
        if (!allowedShapeIds.has(shapeId)) {
            return;
        }

        const status = feature.properties?.status || 'cleaned';
        const matchStatus = feature.properties?.match_status || (status === 'cleaned' ? 'clean' : 'failed');

        let style = {};
        let targetLayer = null;
        let pointColor = '#2563eb';

        if (status === 'original') {
            style = { color: '#ef4444', weight: 5, opacity: 0.4, dashArray: '5, 5' };
            targetLayer = originalLayer;
            pointColor = '#ef4444';
            originalCount++;
        } else if (status === 'simplified') {
            style = { color: '#f59e0b', weight: 4, opacity: 0.7, dashArray: '3, 3' };
            targetLayer = simplifiedLayer;
            pointColor = '#f59e0b';
        } else if (status === 'stop_removed') {
            style = { color: '#a855f7', weight: 4, opacity: 0.7 };
            targetLayer = spikeLayer;
            pointColor = '#a855f7';
        } else {
            style = {
                color: STATUS_COLORS[matchStatus] || STATUS_COLORS.clean,
                weight: 6,
                opacity: 0.5
            };
            targetLayer = cleanedLayer;
            pointColor = STATUS_COLORS[matchStatus] || STATUS_COLORS.clean;

            // Bucket for stats
            const bucket = bucketForStatus(matchStatus);
            if (bucket === 'clean') cleanCount++;
            else if (bucket === 'suspect') suspectCount++;
            else reviewCount++;
        }

        const layer = L.geoJSON(feature, {
            style: style,
            onEachFeature: (f, l) => {
                const props = f.properties || {};
                let popupContent = `<strong>Shape ID:</strong> ${props.shape_id || 'Unknown'}<br>`;
                popupContent += `<strong>Status:</strong> ${props.status || 'Cleaned'}<br>`;
                if (props.points !== undefined && props.points !== null) {
                    popupContent += `<strong>Points:</strong> ${props.points}<br>`;
                }
                if (props.match_status) {
                    popupContent += `<strong>Match Quality:</strong> <span style="color:${STATUS_COLORS[props.match_status] || '#2563eb'}; font-weight:600;">${props.match_status}</span><br>`;
                }
                if (props.rejection_reason) {
                    popupContent += `<strong>Reasons:</strong> ${props.rejection_reason}<br>`;
                }
                if (props.confidence) {
                    const conf = Array.isArray(props.confidence) ? props.confidence.join(', ') : props.confidence;
                    popupContent += `<strong>Confidence:</strong> ${conf}<br>`;
                }
                if (props.endpoint_error !== undefined && props.endpoint_error !== null) {
                    popupContent += `<strong>Endpoint Error:</strong> ${props.endpoint_error}m<br>`;
                }
                if (props.length_ratio !== undefined && props.length_ratio !== null) {
                    popupContent += `<strong>Length Ratio:</strong> ${props.length_ratio}<br>`;
                }
                if (props.max_lateral_deviation !== undefined && props.max_lateral_deviation !== null) {
                    popupContent += `<strong>Max Lateral Deviation:</strong> ${props.max_lateral_deviation}m<br>`;
                }
                if (props.stop_excursions_removed !== undefined && props.stop_excursions_removed !== null) {
                    popupContent += `<strong>Stop Excursions Removed:</strong> ${props.stop_excursions_removed}<br>`;
                }
                if (props.simplified_points !== undefined && props.simplified_points !== null) {
                    popupContent += `<strong>Point Reduction:</strong> ${props.original_points} → ${props.simplified_points} → ${props.stop_removed_points} → ${props.matched_points}<br>`;
                }
                l.bindPopup(popupContent);
            }
        });

        layer.addTo(targetLayer);
        
        // Render Individual Interactive Trace Points ONLY when a single route is selected and toggle is checked
        const shouldRenderPoints = selectedShape !== 'all' && togglePoints.checked;
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
    statTotal.textContent = originalCount;
    statClean.textContent = cleanCount;
    statSuspect.textContent = suspectCount;
    statReview.textContent = reviewCount;

    // Fit map to data
    if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [50, 50] });
    }
}
