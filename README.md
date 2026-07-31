# GTFS Enhancer

A high-performance Python pipeline and web visualizer that utilizes local OSRM (Open Source Routing Machine) map-matching APIs and OpenStreetMap (OSM) data to clean up, reconstruct, and optimize GTFS bus route shapes.

---

## Features

- **Bus-Only GTFS Filtering:** Filters GTFS feeds down to bus routes (`route_type == 3`) and outputs route-to-shape mapping.
- **Out-and-Back Stub Detection:** Detects true multi-point spur tails (junction → tip → return) while leaving legitimate sharp turns, terminal loops, and GPS corners intact. Diagnostic-only by default; opt in with `--enable-stub-filter`.
- **Stop-Aware Stop-Tail Removal:** Joins `stops.txt`/`stop_times.txt` onto each shape and removes short poke-out excursions whose sole purpose is reaching a stop. Single-point tails are handled; terminal endpoints, normal turns, loops, and branches serving multiple stops are preserved.
- **Directional Bearing/Heading Matching:** Calculates compass bearings ($0^\circ-360^\circ$) for trace points so OSRM never snaps lower/underpass traces onto elevated overpasses or diagonal interstates.
- **Gap-Split Matching:** Requests `gaps=split` so OSRM matchings are never force-stitched; disconnected segments are repaired only via routed candidates, never artificial straight connectors.
- **Continuity-Constrained Repair:** Re-matches low-confidence spans and source gaps over overlapping windows, preferring candidates that share OSM nodes with the surrounding accepted roads (the "same-road prior") to prevent parallel-street snaps.
- **Match Quality Validation:** Flags each shape as `clean` / `suspect` / `untrusted` using confidence, endpoint error, lateral deviation, and length-ratio metrics; centerline geometry is still produced so raw GPS jitter is never reintroduced.
- **Straightaway Resampling & Downsampling:** Uses RDP simplification for points exceeding OSRM budgets while guaranteeing maximum gap pinning (default `300m`) on long corridors.
- **Multithreaded Parallel Processing:** ThreadPoolExecutor support for fast parallel matching across multi-core CPUs.
- **Diagnostic Logging:** Logs OSRM confidence scores, matched distances, segment counts, repair counts, OSM node trajectory IDs, and match quality to `execution_debug.log`.
- **Interactive Web Viewer:** Hardware-accelerated (Canvas) Leaflet visualizer with route selection, match-quality filtering, original vs. cleaned toggles, and hoverable trace point metadata tooltips.

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
| `--snap-radius` | `15.0` | Search radius in meters for snapping GPS points to road segments. Tighten (e.g. `8.0-10.0`) near parallel corridors. |
| `--simplify-tolerance` | `15.0` | RDP simplification tolerance in meters applied to the trace before matching. Strips GPS jitter (which otherwise collapses OSRM confidence to ~0); real turns deviate far more than this and are preserved. Set `0` to disable. |
| `--bearing-range` | `45` | Allowed directional heading variance in degrees ($\pm 45^\circ$). |
| `--no-bearings` | `False` | Disable compass heading/bearing matching in OSRM requests. |
| `--enable-stub-filter` | `False` | Remove detected multi-point out-and-back stubs before matching (diagnostic-only by default). |
| `--max-stub-meters` | `75.0` | Maximum excursion in meters to classify a pre-matching out-and-back stub. |
| `--no-stop-tails` | `False` | Disable stop-aware stop-tail removal (enabled by default). |
| `--max-stop-tail-meters` | `125.0` | Maximum path length in meters of a stop tail eligible for removal. |
| `--stop-radius` | `25.0` | Max distance from the poke-out tip to the stop for tail detection. |
| `--return-corridor` | `20.0` | Max chord length for a tail to count as returning to the corridor. |
| `--min-confidence` | `0.75` | Mean confidence below which a match is flagged `suspect`. |
| `--max-endpoint-error` | `40.0` | Max mean start/end displacement in meters before a match is flagged. |
| `--max-lateral-deviation` | `50.0` | Max perpendicular deviation in meters before a match is flagged. |
| `--routes` | `None` | Comma-separated list of route IDs or short names to process (e.g. `--routes "79,J14"`). |
| `--limit-shapes` | `None` | Limit processing to the first $N$ shapes (debug mode). |
| `--workers` | `4` | Number of parallel worker threads for multi-core matching. |
| `--log-file` | `execution_debug.log` | Log file path for race condition diagnostics and node trajectory tracing. |

---

## Example Commands

```bash
# Process specific route with tight 10m snap radius
python src/main.py gtfs.zip output.geojson --routes "79" --snap-radius 10.0

# Tune jitter stripping (larger tolerance = smoother trace, fewer points)
python src/main.py gtfs.zip output.geojson --simplify-tolerance 20.0

# Flag matches whose mean confidence drops below 0.5 or that drift > 30m laterally
python src/main.py gtfs.zip output.geojson --min-confidence 0.5 --max-lateral-deviation 30.0

# Enable pre-matching out-and-back stub removal
python src/main.py gtfs.zip output.geojson --enable-stub-filter

# Tune stop-tail removal (larger max = more aggressive removal)
python src/main.py gtfs.zip output.geojson --max-stop-tail-meters 150.0 --return-corridor 25.0

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
3. Use the **Match Quality Filter** to highlight `clean`, `suspect`, `untrusted`, or failed shapes. Cleaned lines are color-coded: blue = clean, amber = suspect, red = untrusted, grey = failed/fallback.
4. Toggle **Original Shapes**, **Cleaned (Map Matched)**, or **Trace Points**.
5. Click a cleaned shape to view its diagnostics: match quality, confidence, endpoint error, length ratio, max lateral deviation, repair count, and stop-tail removal info.
6. Hover over individual point markers to inspect point sequence, lat/lon, status, and shape IDs.

