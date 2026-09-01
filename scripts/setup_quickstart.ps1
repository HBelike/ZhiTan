param(
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw '未找到 Python 3.12 或 3.13。'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw '未找到 Docker Desktop / Docker Engine。'
}

docker compose version | Out-Null
$env:PYTHONUTF8 = '1'
python scripts/setup_common.py quickstart

Write-Host '下一步：docker compose --env-file .env.quickstart up -d --build --wait'
