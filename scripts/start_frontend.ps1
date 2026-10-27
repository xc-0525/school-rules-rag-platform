# Start the Vue3 + Vite frontend dev server.
#
# Backend must be running first: scripts/start_backend.ps1  (http://127.0.0.1:8000)
# Then open http://127.0.0.1:5173
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1 -Port 5174
#
# First run installs node_modules (npm install), later runs go straight to dev.

param(
    [int]$Port = 5173
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $RepoRoot 'frontend'
if (-not (Test-Path $FrontendDir)) { throw "frontend directory not found: $FrontendDir" }

Set-Location $FrontendDir
$env:npm_config_cache = Join-Path $FrontendDir '.npm-cache'

if (-not (Test-Path (Join-Path $FrontendDir 'node_modules\vite'))) {
    Write-Host "node_modules missing, running npm install..." -ForegroundColor Yellow
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
}

Write-Host "Backend expected at http://127.0.0.1:8000 (scripts/start_backend.ps1)" -ForegroundColor DarkGray
Write-Host "Starting Vite dev server: http://127.0.0.1:$Port" -ForegroundColor Cyan
npm run dev -- --port $Port
