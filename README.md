# GTFS Enhancer

A high-performance Python pipeline and web visualizer that utilizes local OSRM (Open Source Routing Machine) map-matching APIs and OpenStreetMap (OSM) data to clean up, reconstruct, and optimize GTFS bus route shapes.

---

## Features

- **Bus-Only GTFS Filtering:** Filters GTFS feeds down to bus routes (`route_type == 3`) and outputs route-to-shape mapping.
- **RDP Simplification:** Strips GPS jitter and short excursions (deviation < tolerance) in one step before matching.
- **Stop-Aware Stop-Excursion Removal:** Joins `stops.txt`/`stop_times.txt` onto each shape and removes the short poke-outs where the shape leaves the road to reach a stop and returns. A vertex is removed when it returns near its start, pokes out by a minimum amount, is near a stop, and the corridor heading is unchanged before/after (the route continues along the road). Real turns change the corridor heading and are preserved.
- **Directional Bearing/Heading Matching:** Calculates compass bearings ($0^\circ-360^\circ$) for trace points so OSRM never snaps lower/underpass traces onto elevated overpasses or diagonal interstates.
- **OSRM Map Matching:** Snaps the cleaned skeleton to the OSM road network centerline via the local OSRM `match` API.
- **Match Quality Validation:** Flags each shape as `clean` / `suspect` / `untrusted` using confidence, endpoint error, lateral deviation, and length-ratio metrics.
- **Multithreaded Parallel Processing:** ThreadPoolExecutor support for fast parallel matching across multi-core CPUs.
- **Staged Output for Review:** The GeoJSON preserves the original shape, the RDP-simplified trace, the stop-excursion-removed trace, and the final matched centerline so each step's performance can be inspected.
- **Diagnostic Logging:** Logs OSRM confidence scores, matched distances, point reduction per stage, and stop-excursion removal to `execution_debug.log`.
- **Interactive Web Viewer:** Hardware-accelerated (Canvas) Leaflet visualizer with route selection, per-stage layer toggles, match-quality filtering, and hoverable trace point metadata tooltips.

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
               [--simplify-tolerance SIMPLIFY_TOLERANCE]
               [--spike-return SPIKE_RETURN] [--spike-deviation SPIKE_DEVIATION]
               [--min-confidence MIN_CONFIDENCE]
               [--max-endpoint-error MAX_ENDPOINT_ERROR]
               [--max-lateral-deviation MAX_LATERAL_DEVIATION]
               [--routes ROUTES] [--limit-shapes LIMIT_SHAPES]
               [--workers WORKERS] [--log-file LOG_FILE]
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
| `--snap-radius` | `15.0` | OSRM search radius in meters for snapping points to road segments. Tighten (e.g. `8.0-10.0`) near parallel corridors. |
| `--bearing-range` | `45` | Allowed directional heading variance in degrees ($\pm 45^\circ$). |
| `--no-bearings` | `False` | Disable compass heading/bearing matching in OSRM requests. |
| `--simplify-tolerance` | `15.0` | RDP simplification tolerance in meters applied before matching. Strips GPS jitter and short excursions; real turns deviate far more and are preserved. Set `0` to disable. |
| `--max-points` | `500` | Maximum trace points sent to OSRM after resampling. |
| `--spike-return` | `30.0` | Max chord length (m) for a stop excursion to count as returning to the corridor. |
| `--spike-deviation` | `8.0` | Min deviation (m) for a stop-excursion vertex to be removed. |
| `--stop-radius` | `60.0` | Max distance (m) from the excursion tip to a stop for removal. |
| `--min-confidence` | `0.75` | Mean confidence below which a match is flagged `suspect`. |
| `--max-endpoint-error` | `40.0` | Max mean start/end displacement in meters before a match is flagged. |
| `--max-lateral-deviation` | `50.0` | Max perpendicular deviation in meters before a match is flagged. |
| `--routes` | `None` | Comma-separated list of route IDs or short names to process (e.g. `--routes "79,J14"`). |
| `--limit-shapes` | `None` | Limit processing to the first $N$ shapes (debug mode). |
| `--workers` | `4` | Number of parallel worker threads for multi-core matching. |
| `--log-file` | `execution_debug.log` | Log file path for diagnostics and node trajectory tracing. |

---

## Example Commands

```bash
# Process specific route with tight 10m snap radius
python src/main.py gtfs.zip output.geojson --routes "79" --snap-radius 10.0

# Tune jitter/tail stripping (larger tolerance = smoother trace, fewer points)
python src/main.py gtfs.zip output.geojson --simplify-tolerance 20.0

# Tune stop-excursion removal (larger stop radius = more aggressive)
python src/main.py gtfs.zip output.geojson --stop-radius 50.0 --spike-return 40.0

# Flag matches whose mean confidence drops below 0.5 or that drift > 30m laterally
python src/main.py gtfs.zip output.geojson --min-confidence 0.5 --max-lateral-deviation 30.0

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
4. Toggle the **intermediate-stage layers** to review each preprocessing step:
   - **Original Shapes** (red, dashed) — raw GTFS
   - **RDP Simplified** (orange, dashed) — after jitter/short-excursion stripping
   - **Stop Excursions Removed** (purple) — after stop-aware poke removal
   - **Cleaned (Map Matched)** — final OSRM centerline
5. Click a cleaned shape to view its diagnostics: match quality, confidence, endpoint error, length ratio, max lateral deviation, point reduction per stage, and stop excursions removed.
6. Hover over individual point markers to inspect point sequence, lat/lon, status, and shape IDs.

