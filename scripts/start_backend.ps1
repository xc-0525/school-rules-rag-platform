# Start the v2 backend with REAL stores (global Python, never the venv).
#
# Why an explicit interpreter: the demo relies on sentence-transformers / torch /
# qdrant / neo4j / pymysql, which are installed in the global Python 3.14 only.
# If a virtualenv is active, "python" would resolve to the venv and fail.
#
# Prerequisites:
#   1) MySQL service running, database school_rules_rag created (sql/init_mysql.sql)
#   2) Neo4j running: powershell -ExecutionPolicy Bypass -File .\scripts\start_neo4j.ps1
#   3) rules ingested once (backend stopped): cd backend; python scripts/ingest_rules.py --dir ../rules --reset
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1 -Port 8001
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1 -PythonExe "C:\Python\python.exe"
#
# BGE warm-up takes about 2-3 minutes on first start; wait until:
#   http://127.0.0.1:8000/health  ->  "vector_ready": true
# Then open http://127.0.0.1:8000/docs

param(
    [int]$Port = 8000,
    [string]$PythonExe = 'G:\DevEnv\Python\Python314\python.exe'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$ModelPath = 'G:\ai-models\hf\hub\models--BAAI--bge-small-zh-v1.5\snapshots\7999e1d3359715c523056ef9478215996d62a620'

if (-not (Test-Path $BackendDir)) { throw "backend directory not found: $BackendDir" }
if (-not (Test-Path $ModelPath)) { throw "local BGE model not found: $ModelPath" }
if (-not (Test-Path $PythonExe)) {
    $fallback = (Get-Command python -ErrorAction SilentlyContinue)
    if ($fallback) {
        Write-Host "Python not found at $PythonExe; falling back to $($fallback.Source)" -ForegroundColor Yellow
        $PythonExe = $fallback.Source
    } else {
        throw "Python not found. Pass -PythonExe with a Python that has sentence-transformers installed."
    }
}

if ($env:VIRTUAL_ENV) {
    Write-Host "WARNING: a virtualenv is active ($env:VIRTUAL_ENV)." -ForegroundColor Yellow
    Write-Host "         This script uses $PythonExe explicitly, so the venv is ignored." -ForegroundColor Yellow
}

$env:LLM_MODE = 'deepseek'
$env:VECTOR_MODE = 'qdrant'
$env:GRAPH_MODE = 'neo4j'
$env:AUTH_REQUIRED = '1'
$env:SERVE_FRONTEND = '1'
$env:EMBEDDING_MODEL_PATH = $ModelPath
$env:QDRANT_PATH = 'data/qdrant'
$env:NEO4J_URI = 'bolt://127.0.0.1:7687'
$env:NEO4J_USER = ''
$env:NEO4J_PASSWORD = ''

Write-Host "Python       = $PythonExe" -ForegroundColor DarkGray
Write-Host "LLM_MODE     = $env:LLM_MODE" -ForegroundColor DarkGray
Write-Host "VECTOR_MODE  = $env:VECTOR_MODE" -ForegroundColor DarkGray
Write-Host "GRAPH_MODE   = $env:GRAPH_MODE" -ForegroundColor DarkGray
Write-Host "AUTH_REQUIRED= $env:AUTH_REQUIRED" -ForegroundColor DarkGray
Write-Host "SERVE_FRONTEND=$env:SERVE_FRONTEND" -ForegroundColor DarkGray
Write-Host "NEO4J_URI    = $env:NEO4J_URI" -ForegroundColor DarkGray

if ($env:SERVE_FRONTEND -eq '1' -and -not (Test-Path (Join-Path $RepoRoot 'frontend\dist\index.html'))) {
    Write-Host "WARNING: frontend/dist not found. Run: cd frontend; npm run build" -ForegroundColor Yellow
    Write-Host "         (or use the Vite dev server on 5173 instead)" -ForegroundColor Yellow
}

Set-Location $BackendDir
Write-Host "Starting v2 backend: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "First start loads BGE (2-3 min); wait for vector_ready=true at /health." -ForegroundColor Cyan
& $PythonExe -m uvicorn app.main:app --port $Port --host 127.0.0.1
