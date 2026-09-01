$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
}
$VenvPython = '.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
& $VenvPython scripts/setup_common.py dev

if (Test-Path 'requirements.lock.txt') {
    & $VenvPython -m pip install -r requirements.lock.txt
} else {
    & $VenvPython -m pip install -r requirements.txt -r requirements-career-assistant.txt -r requirements-development.txt
}
npm --prefix web-ui ci

Write-Host '开发环境准备完成。请先启动 PostgreSQL，等待 healthy 后执行：'
Write-Host '.venv\Scripts\python.exe -m alembic upgrade head'
Write-Host '.venv\Scripts\python.exe preview_server.py --host 127.0.0.1 --port 18080'
Write-Host '.venv\Scripts\python.exe scripts/run_career_agent_worker.py'
Write-Host 'npm --prefix web-ui run dev -- --host 127.0.0.1 --port 5173'
