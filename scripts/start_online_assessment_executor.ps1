param(
    [int]$Port = 2000
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $projectRoot "docker-compose.online-assessment.yml"
$baseUrl = "http://127.0.0.1:$Port"
$env:ONLINE_ASSESSMENT_PISTON_PORT = [string]$Port
$packageVolume = "wechat-agent-online-assessment-piston-packages"

docker volume inspect $packageVolume *> $null
if ($LASTEXITCODE -ne 0) {
    docker volume create $packageVolume | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Piston package volume could not be created"
    }
}

docker compose -f $composeFile up -d
if ($LASTEXITCODE -ne 0) {
    throw "Piston container failed to start"
}

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        Invoke-RestMethod -Uri "$baseUrl/api/v2/runtimes" -TimeoutSec 2 | Out-Null
        $ready = $true
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $ready) {
    throw "Piston did not become ready within 120 seconds; inspect the container logs"
}

$requiredRuntimes = @(
    @{ Runtime = "python"; Package = "python"; Version = "3.12.0" },
    @{ Runtime = "javascript"; Package = "node"; Version = "20.11.1" },
    @{ Runtime = "java"; Package = "java"; Version = "15.0.2" },
    @{ Runtime = "c++"; Package = "gcc"; Version = "10.2.0" }
)
$installed = @(Invoke-RestMethod -Uri "$baseUrl/api/v2/runtimes" -TimeoutSec 5)
foreach ($runtime in $requiredRuntimes) {
    if ($installed.language -contains $runtime.Runtime) {
        Write-Host "$($runtime.Runtime) is already installed"
        continue
    }
    Write-Host "Installing the $($runtime.Runtime) runtime; the first run downloads its package..."
    $body = @{ language = $runtime.Package; version = $runtime.Version } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v2/packages" -ContentType "application/json" -Body $body -TimeoutSec 1800 | Out-Null
}

$finalRuntimes = @(Invoke-RestMethod -Uri "$baseUrl/api/v2/runtimes" -TimeoutSec 5)
$requiredLanguageNames = @($requiredRuntimes | ForEach-Object { $_.Runtime })
$missing = @($requiredLanguageNames | Where-Object { $finalRuntimes.language -notcontains $_ })
if ($missing.Count -gt 0) {
    throw "Runtime installation failed: $($missing -join ', ')"
}

Write-Host "Online assessment executor is ready: $baseUrl"
$finalRuntimes |
    Where-Object { $_.language -in $requiredLanguageNames } |
    Select-Object language, version, aliases |
    Format-Table -AutoSize
