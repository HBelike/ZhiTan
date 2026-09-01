param(
    # 固定使用项目专属端口，避免与常见的 8080 / 8012 开发服务冲突。
    [int]$Port = 18080
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$workerProcess = $null
$backendExitCode = 1

# 本地 API 与持久化 Turn Worker 仍使用独立进程，保持和生产环境一致。
# Worker 由当前启动脚本管理；API 退出时只停止本脚本启动的 Worker。
$workerProcess = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList '.\scripts\run_career_agent_worker.py' `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru

if ($workerProcess.WaitForExit(1000)) {
    throw "求职助手 Worker 启动失败，退出码：$($workerProcess.ExitCode)"
}

# 兼容旧命令：实际唯一入口是项目根目录的 preview_server.py。
# 这个脚本不再直接拼接 Uvicorn 参数，避免和 Python 入口出现两套不同启动逻辑。
$env:PREVIEW_SERVER_PORT = "$Port"
$env:PREVIEW_SERVER_RELOAD = if ([string]::IsNullOrWhiteSpace($env:PREVIEW_SERVER_RELOAD)) { "true" } else { $env:PREVIEW_SERVER_RELOAD.ToLowerInvariant() }
try {
    & $pythonPath .\preview_server.py
    $backendExitCode = $LASTEXITCODE
}
finally {
    if ($null -ne $workerProcess -and -not $workerProcess.HasExited) {
        Stop-Process -Id $workerProcess.Id
        $workerProcess.WaitForExit()
    }
}

exit $backendExitCode
