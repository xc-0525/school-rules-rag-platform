# Start the v2 backend with REAL stores (global Python, not the venv).
#
# Why global Python: sentence-transformers / torch / qdrant / neo4j / pymysql
# are already installed there; the backend/.venv only has lightweight deps.
#
# Prerequisites:
#   1) MySQL service running, database school_rules_rag created (sql/init_mysql.sql)
#   2) Neo4j running: powershell -ExecutionPolicy Bypass -File .\scripts\start_neo4j.ps1
#   3) rules ingested once: cd backend; python scripts/ingest_rules.py --dir ../rules --reset
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1 -Port 8001
#
# BGE warm-up takes about 2-3 minutes on first start; wait until:
#   http://127.0.0.1:8000/health  ->  "vector_ready": true
# Then open http://127.0.0.1:8000/docs

param(
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$ModelPath = 'G:\ai-models\hf\hub\models--BAAI--bge-small-zh-v1.5\snapshots\7999e1d3359715c523056ef9478215996d62a620'

if (-not (Test-Path $BackendDir)) { throw "backend directory not found: $BackendDir" }
if (-not (Test-Path $ModelPath)) { throw "local BGE model not found: $ModelPath" }
if ($env:VIRTUAL_ENV) {
    Write-Host "WARNING: a virtualenv is active ($env:VIRTUAL_ENV)." -ForegroundColor Yellow
    Write-Host "         It may lack sentence-transformers/neo4j; run this script in a clean shell." -ForegroundColor Yellow
}

$env:LLM_MODE = 'deepseek'
$env:VECTOR_MODE = 'qdrant'
$env:GRAPH_MODE = 'neo4j'
$env:EMBEDDING_MODEL_PATH = $ModelPath
$env:QDRANT_PATH = 'data/qdrant'
$env:NEO4J_URI = 'bolt://127.0.0.1:7687'
$env:NEO4J_USER = ''
$env:NEO4J_PASSWORD = ''

Write-Host "LLM_MODE     = $env:LLM_MODE" -ForegroundColor DarkGray
Write-Host "VECTOR_MODE  = $env:VECTOR_MODE" -ForegroundColor DarkGray
Write-Host "GRAPH_MODE   = $env:GRAPH_MODE" -ForegroundColor DarkGray
Write-Host "NEO4J_URI    = $env:NEO4J_URI" -ForegroundColor DarkGray

Set-Location $BackendDir
Write-Host "Starting v2 backend: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "First start loads BGE (2-3 min); wait for vector_ready=true at /health." -ForegroundColor Cyan
& python -m uvicorn app.main:app --port $Port --host 127.0.0.1
