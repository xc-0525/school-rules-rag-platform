# Start the local Neo4j 5.26.30 installation (native install, no Docker).
#
# Usage (from your own PowerShell, NOT the AI sandbox):
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_neo4j.ps1
#
# Notes:
# - Neo4j is installed at G:\neo4j (5.26.30), auth is disabled in its config.
# - Uses the local Java 17, no system PATH changes.
# - Runs in foreground; press Ctrl+C to stop. Keep this window open while demoing.
# - Bolt endpoint: 127.0.0.1:7687 / Browser console: http://127.0.0.1:7474
# - This script only STARTS the server. It does not modify or clear graph data.

$ErrorActionPreference = 'Stop'

$JavaHome = 'G:\DevEnv\Java-JDK17\jdk-17.0.1.12-hotspot'
$Neo4jHome = 'G:\neo4j'

if (-not (Test-Path $JavaHome)) {
    throw "Java 17 not found: $JavaHome"
}
if (-not (Test-Path "$Neo4jHome\bin\neo4j.bat")) {
    throw "Neo4j not found: $Neo4jHome"
}

$env:JAVA_HOME = $JavaHome
Write-Host "JAVA_HOME = $JavaHome" -ForegroundColor DarkGray
Write-Host "Neo4j     = $Neo4jHome" -ForegroundColor DarkGray

$portInUse = $false
try {
    $listener = Get-NetTCPConnection -LocalPort 7687 -State Listen -ErrorAction SilentlyContinue
    if ($listener) { $portInUse = $true }
} catch {
    $portInUse = $false
}

if ($portInUse) {
    Write-Host "Port 7687 is already listening; Neo4j seems to be running already." -ForegroundColor Yellow
    Write-Host "Browser console: http://127.0.0.1:7474" -ForegroundColor Yellow
    return
}

Write-Host "Starting Neo4j in foreground (Ctrl+C to stop)..." -ForegroundColor Cyan
Write-Host "Wait for 'Started.' in the log, then open http://127.0.0.1:7474" -ForegroundColor Cyan
& "$Neo4jHome\bin\neo4j.bat" console
