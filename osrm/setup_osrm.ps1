# setup_osrm.ps1 — Download Chicago OSM data, preprocess with OSRM (bus profile), and start the routing server
# Requires: Docker Desktop running

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$OSRM_DIR  = Join-Path $ScriptDir "osrm-data"
$PBF_URL   = "https://download.bbbike.org/osm/bbbike/Chicago/Chicago.osm.pbf"
$PBF_FILE  = "Chicago.osm.pbf"
$OSRM_FILE = "Chicago.osrm"
$BUS_LUA   = "bus.lua"

# Locate bus.lua profile in current dir or preprocess/
$BUS_LUA_SRC = Join-Path $ScriptDir "bus.lua"
if (-not (Test-Path $BUS_LUA_SRC)) {
    $BUS_LUA_SRC = Join-Path $ScriptDir "preprocess\bus.lua"
}

# Create data directory
if (-not (Test-Path $OSRM_DIR)) {
    New-Item -ItemType Directory -Path $OSRM_DIR | Out-Null
    Write-Host "[1/6] Created $OSRM_DIR"
} else {
    Write-Host "[1/6] $OSRM_DIR already exists"
}

# Copy bus.lua profile to osrm-data
if (Test-Path $BUS_LUA_SRC) {
    Copy-Item -Path $BUS_LUA_SRC -Destination (Join-Path $OSRM_DIR $BUS_LUA) -Force
}

# Download PBF if not present
$pbfPath = Join-Path $OSRM_DIR $PBF_FILE
if (-not (Test-Path $pbfPath)) {
    Write-Host "[2/6] Downloading Chicago OSM data (~95MB) from BBBike..."
    Invoke-WebRequest -Uri $PBF_URL -OutFile $pbfPath -UseBasicParsing
    Write-Host "       Download complete."
} else {
    Write-Host "[2/6] PBF file already exists, skipping download."
}

# Ensure required index files exist for OSRM mmap on volume mounts
$fileIndex = Join-Path $OSRM_DIR "$OSRM_FILE.fileIndex"
$ramIndex  = Join-Path $OSRM_DIR "$OSRM_FILE.ramIndex"
if (-not (Test-Path $fileIndex)) { New-Item -Path $fileIndex -ItemType File -Force | Out-Null }
if (-not (Test-Path $ramIndex))  { New-Item -Path $ramIndex  -ItemType File -Force | Out-Null }

# Convert path to Docker-friendly format (Windows absolute path with forward slashes)
$dockerPath = $OSRM_DIR.Replace('\', '/')

Write-Host "[3/6] Extracting with custom bus profile (bus.lua)..."
docker run --rm -t -v "${dockerPath}:/data" osrm/osrm-backend osrm-extract -p /data/$BUS_LUA /data/$PBF_FILE

Write-Host "[4/6] Partitioning..."
docker run --rm -t -v "${dockerPath}:/data" osrm/osrm-backend osrm-partition /data/$OSRM_FILE

Write-Host "[5/6] Customizing..."
docker run --rm -t -v "${dockerPath}:/data" osrm/osrm-backend osrm-customize /data/$OSRM_FILE

Write-Host "[6/6] Starting OSRM server on port 5000..."
Write-Host "       Max matching size: 500"
Write-Host "       Algorithm: MLD"
Write-Host "       Profile: Bus (includes busways, PSV, transit terminals)"

# Stop and remove any existing container
try { docker rm -f osrm-backend 2>$null } catch {}

docker run -d `
    -p 5000:5000 `
    -v "${dockerPath}:/data" `
    --name osrm-backend `
    osrm/osrm-backend `
    osrm-routed --algorithm mld --max-matching-size 500 /data/$OSRM_FILE

Write-Host ""
Write-Host "OSRM bus routing server is running at http://localhost:5000"
Write-Host "Health check: curl http://localhost:5000/health"
Write-Host "To stop: docker stop osrm-backend"
