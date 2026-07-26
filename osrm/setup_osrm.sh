#!/bin/bash
# setup_osrm.sh — Download Chicago OSM data, preprocess with OSRM (bus profile), and start the routing server
# Requires: Docker running, wget or curl

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OSRM_DIR="${SCRIPT_DIR}/osrm-data"
PBF_URL="https://download.bbbike.org/osm/bbbike/Chicago/Chicago.osm.pbf"
PBF_FILE="Chicago.osm.pbf"
OSRM_FILE="Chicago.osrm"
BUS_LUA="bus.lua"

if [ -f "${SCRIPT_DIR}/bus.lua" ]; then
    BUS_LUA_SRC="${SCRIPT_DIR}/bus.lua"
else
    BUS_LUA_SRC="${SCRIPT_DIR}/preprocess/bus.lua"
fi

# Create data directory
if [ ! -d "$OSRM_DIR" ]; then
    mkdir -p "$OSRM_DIR"
    echo "[1/6] Created $OSRM_DIR"
else
    echo "[1/6] $OSRM_DIR already exists"
fi

# Copy bus.lua profile to osrm-data
if [ -f "$BUS_LUA_SRC" ]; then
    cp -f "$BUS_LUA_SRC" "$OSRM_DIR/$BUS_LUA"
fi

# Download PBF if not present
PBF_PATH="$OSRM_DIR/$PBF_FILE"
if [ ! -f "$PBF_PATH" ]; then
    echo "[2/6] Downloading Chicago OSM data (~95MB) from BBBike..."
    if command -v curl &> /dev/null; then
        curl -L -o "$PBF_PATH" "$PBF_URL"
    elif command -v wget &> /dev/null; then
        wget -O "$PBF_PATH" "$PBF_URL"
    else
        echo "Error: Neither curl nor wget found. Please install one of them."
        exit 1
    fi
    echo "       Download complete."
else
    echo "[2/6] PBF file already exists, skipping download."
fi

# Ensure required index files exist for OSRM mmap on volume mounts
touch "$OSRM_DIR/$OSRM_FILE.fileIndex" "$OSRM_DIR/$OSRM_FILE.ramIndex"

echo "[3/6] Extracting with custom bus profile (bus.lua)..."
docker run --rm -t -v "${OSRM_DIR}:/data" osrm/osrm-backend osrm-extract -p /data/$BUS_LUA /data/$PBF_FILE

echo "[4/6] Partitioning..."
docker run --rm -t -v "${OSRM_DIR}:/data" osrm/osrm-backend osrm-partition /data/$OSRM_FILE

echo "[5/6] Customizing..."
docker run --rm -t -v "${OSRM_DIR}:/data" osrm/osrm-backend osrm-customize /data/$OSRM_FILE

echo "[6/6] Starting OSRM server on port 5000..."
echo "       Max matching size: 500"
echo "       Algorithm: MLD"
echo "       Profile: Bus (includes busways, PSV, transit terminals)"

# Stop any existing container
docker rm -f osrm-backend 2>/dev/null || true

docker run -d \
    -p 5000:5000 \
    -v "${OSRM_DIR}:/data" \
    --name osrm-backend \
    osrm/osrm-backend \
    osrm-routed --algorithm mld --max-matching-size 500 /data/$OSRM_FILE

echo ""
echo "OSRM bus routing server is running at http://localhost:5000"
echo "Health check: curl http://localhost:5000/health"
echo "To stop: docker stop osrm-backend"
