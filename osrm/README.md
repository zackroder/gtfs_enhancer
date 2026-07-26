# OSRM Bus Profile & Server Setup

This folder contains the customized OSRM `bus.lua` profile and setup scripts for running a local OSRM bus routing server.

## Files

- `bus.lua`: Customized OSRM profile for urban bus map matching:
  - `properties.max_height = 3.2` meters (~10'6") to allow 13'0" railroad underpass clearance.
  - Full support for `highway=busway`, `bus=designated`, `psv=designated`, and dedicated bus lanes.
- `setup_osrm.ps1`: Automated PowerShell script to extract, partition, customize, and launch OSRM in Docker.

## Usage

1. Download an OSM PBF file for your region (e.g. `illinois-latest.osm.pbf` from Geofabrik) into this `osrm/` folder.
2. Run `setup_osrm.ps1`:
   ```powershell
   .\osrm\setup_osrm.ps1 -OsmPbfPath "illinois-latest.osm.pbf"
   ```
3. The server will launch on `http://localhost:5000` with `--max-matching-size 500`.
