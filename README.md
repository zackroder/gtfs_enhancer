# GTFS Enhancer

A high-performance Python pipeline and web visualizer that utilizes local OSRM (Open Source Routing Machine) map-matching APIs and OpenStreetMap (OSM) data to clean up, reconstruct, and optimize GTFS bus route shapes.

---

## Features

- **Bus-Only GTFS Filtering:** Filters GTFS feeds down to bus routes (`route_type == 3`) and outputs route-to-shape mapping.
- **Pre-Matching Side-Stub Pruning:** Detects and removes off-corridor "stop-tail" side-stubs ($<110^\circ$ acute turn angles) before map matching.
- **Directional Bearing/Heading Matching:** Calculates compass bearings ($0^\circ-360^\circ$) for trace points so OSRM never snaps lower/underpass traces onto elevated overpasses or diagonal interstates.
- **Straightaway Resampling & Downsampling:** Uses RDP simplification for points exceeding OSRM budgets while guaranteeing maximum gap pinning (default `300m`) on long corridors.
- **Continuous Multi-Matching Segment Stitching:** Stitches disjointed matchings into single continuous `LineString` geometries so long routes are never cut off.
- **Multithreaded Parallel Processing:** ThreadPoolExecutor support for fast parallel matching across multi-core CPUs.
- **Diagnostic Logging:** Logs OSRM confidence scores, matched distances, segment counts, and OSM node trajectory IDs to `execution_debug.log`.
- **Interactive Web Viewer:** Hardware-accelerated (Canvas) Leaflet visualizer with route selection, original vs. cleaned toggles, and hoverable trace point metadata tooltips.

---

## Quick Start

### 1. Setup Local OSRM Server
Start the local OSRM bus routing server using the included setup scripts:

**Windows (PowerShell):**
```powershell
.\osrm\setup_osrm.ps1 -OsmPbfPath "Chicago.osm.pbf"
```

**Linux / macOS:**
```bash
./osrm/setup_osrm.sh
```
*The OSRM server runs at `http://localhost:5000` using our custom `osrm/bus.lua` profile (`properties.max_height = 3.2m`).*

---

### 2. Install Python Dependencies
```powershell
.\venv\Scripts\activate
pip install -r requirements.txt
```

---

### 3. Run Map Matching

```powershell
python src/main.py https://url-to-your-project.com/gtfs.zip output_shapes.geojson
```

---

## CLI Options & Flags

```text
usage: main.py [-h] [--osrm-url OSRM_URL] [--profile PROFILE]
               [--max-points MAX_POINTS] [--snap-radius SNAP_RADIUS]
               [--bearing-range BEARING_RANGE] [--no-bearings]
               [--max-stub-meters MAX_STUB_METERS] [--routes ROUTES]
               [--limit-shapes LIMIT_SHAPES] [--workers WORKERS]
               [--log-file LOG_FILE]
               gtfs_path output_path
```

### Positional Arguments
| Argument | Description |
| :--- | :--- |
| `gtfs_path` | Path to GTFS zip file, extracted GTFS directory, or HTTP(S) URL. |
| `output_path` | Output path to save the generated GeoJSON file. |

### Optional Arguments & Tuning Flags
| Flag | Default | Description |
| :--- | :--- | :--- |
| `--osrm-url` | `http://localhost:5000` | Base URL of local OSRM instance. |
| `--profile` | `bus` | OSRM routing profile to use (`bus`, `driving`). |
| `--max-points` | `500` | Maximum trace points per OSRM request before downsampling. |
| `--snap-radius` | `15.0` | Search radius in meters for snapping GPS points to road segments. Tighten (e.g. `10.0`) to avoid side-street traps. |
| `--bearing-range` | `45` | Allowed directional heading variance in degrees ($\pm 45^\circ$). |
| `--no-bearings` | `False` | Disable compass heading/bearing matching in OSRM requests. |
| `--max-stub-meters` | `75.0` | Maximum distance in meters to classify pre-matching acute side-stubs for removal. |
| `--routes` | `None` | Comma-separated list of route IDs or short names to process (e.g. `--routes "79,J14"`). |
| `--limit-shapes` | `None` | Limit processing to the first $N$ shapes (debug mode). |
| `--workers` | `4` | Number of parallel worker threads for multi-core matching. |
| `--log-file` | `execution_debug.log` | Log file path for race condition diagnostics and node trajectory tracing. |

---

## Example Commands

```bash
# Process specific route with tight 10m snap radius
python src/main.py gtfs.zip output.geojson --routes "79" --snap-radius 10.0

# Process first 5 shapes with 8 parallel worker threads
python src/main.py gtfs.zip output.geojson --limit-shapes 5 --workers 8

# Run automated test suite
pytest tests/
```

---

## Web Visualizer

Open `viewer/index.html` in your browser:
1. Click **Select GeoJSON File** and select your generated `output_shapes.geojson`.
2. Use the **Route Filter** dropdown to inspect specific bus routes.
3. Toggle **Original Shapes**, **Cleaned (Map Matched)**, or **Trace Points**.
4. Hover over individual point markers to inspect point sequence, lat/lon, status, and shape IDs.

