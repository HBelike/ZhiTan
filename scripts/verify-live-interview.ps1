param(
    [switch]$Live,
    [string[]]$MeetingApps = @("Teams", "Tencent Meeting"),
    [ValidateRange(1, 240)]
    [int]$DurationMinutes = 60
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
$desktopRoot = Join-Path $workspaceRoot "desktop-interview-assistant"
$webUiRoot = Join-Path $workspaceRoot "web-ui"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project Python environment was not found: $pythonPath"
}

Push-Location $workspaceRoot
try {
    & $pythonPath -m pytest `
        tests/test_live_interview_core.py `
        tests/test_live_interview_services.py `
        tests/test_live_interview_desktop_launcher.py `
        tests/test_live_interview_web.py `
        tests/test_career_turn_api.py `
        tests/test_career_optional_context_repository.py `
        -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $heads = (& $pythonPath -m alembic heads | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($heads -ne "20260823_18 (head)") {
        throw "Unexpected Alembic head: $heads"
    }

    $migrationText = Get-Content -LiteralPath "migrations/versions/20260823_18_live_interview_assistant.py" -Raw
    if ($migrationText -match "pcm_base64|\.wav|partial_text|audio_bytes") {
        throw "The migration contains a prohibited raw-audio or partial field."
    }

    Push-Location $desktopRoot
    try {
        npm test -- --run
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npm run typecheck
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npm run build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npm audit --audit-level=high
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }

    Push-Location $webUiRoot
    try {
        npm test
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        npm run build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }

    Write-Host "Automated acceptance passed: backend, protocol, desktop launcher, desktop and main UI tests, type checks, builds, migration, and dependency audit."

    if ($Live) {
        if (-not $env:DASHSCOPE_API_KEY -and -not $env:OPENAI_API_KEY) {
            throw "Live acceptance requires DASHSCOPE_API_KEY or OPENAI_API_KEY. Automated acceptance remains valid."
        }
        Write-Host "Live prerequisites are ready. Test $($MeetingApps -join ', ') with two call endpoints for $DurationMinutes minutes; record ASR quality, first-answer latency, device switching, and capture shutdown."
    }
}
finally {
    Pop-Location
}
