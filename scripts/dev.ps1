param(
    [switch]$Stop,
    [switch]$Mock,
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$webRoot = Join-Path $repoRoot 'web'
$runtimeDir = Join-Path $repoRoot '.data\dev'
$statePath = Join-Path $runtimeDir 'processes.json'
$apiUrl = 'http://127.0.0.1:8000'
$webUrl = 'http://127.0.0.1:5173/'

function Get-ProcessRecord([int]$Id) {
    Get-CimInstance Win32_Process -Filter "ProcessId = $Id" -ErrorAction SilentlyContinue
}

function Stop-ProcessTree([int]$Id) {
    if (Get-ProcessRecord $Id) {
        & taskkill.exe /PID $Id /T /F 2>$null | Out-Null
    }
}

function Get-ListenerInfo([int]$Port) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $processInfo = Get-ProcessRecord ([int]$listener.OwningProcess)
        [pscustomobject]@{
            Port = $Port
            ProcessId = [int]$listener.OwningProcess
            Name = $processInfo.Name
            ExecutablePath = $processInfo.ExecutablePath
            CommandLine = $processInfo.CommandLine
        }
    }
}

if ($Stop) {
    $stopped = @{}
    if (Test-Path $statePath) {
        $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        foreach ($service in @(@{ Name = 'api'; ProcessId = $saved.api_pid },
                               @{ Name = 'web'; ProcessId = $saved.web_pid })) {
            if (-not $service.ProcessId) { continue }
            $processInfo = Get-ProcessRecord ([int]$service.ProcessId)
            $owned = if ($service.Name -eq 'api') {
                $processInfo.CommandLine -match 'uvicorn' -and $processInfo.CommandLine -match 'agentic_ops\.api:app'
            } else {
                $processInfo.CommandLine -match 'npm' -and $processInfo.CommandLine -match 'run dev'
            }
            if ($owned) {
                Stop-ProcessTree ([int]$service.ProcessId)
                $stopped[[int]$service.ProcessId] = $true
                Write-Host "Stopped the launched $($service.Name) process tree (PID $($service.ProcessId))."
            }
        }
    }
    foreach ($port in @(8000, 5173)) {
        foreach ($listener in @(Get-ListenerInfo $port)) {
            $expected = if ($port -eq 8000) {
                $listener.CommandLine -match 'uvicorn' -and $listener.CommandLine -match 'agentic_ops\.api:app' `
                    -and $listener.ExecutablePath `
                    -and $listener.ExecutablePath.IndexOf($repoRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            } else {
                $listener.CommandLine -match 'vite' -and $listener.CommandLine -match '5173' `
                    -and $listener.CommandLine.IndexOf($webRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            }
            if ($expected) {
                Stop-ProcessTree $listener.ProcessId
                $stopped[$listener.ProcessId] = $true
                Write-Host "Stopped this project's service on port $port (process $($listener.ProcessId))."
            } elseif ($listener.ProcessId) {
                Write-Warning "Port $port belongs to another or unrecognized process ($($listener.Name), PID $($listener.ProcessId)); left it running."
            }
        }
    }
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    if ($stopped.Count -eq 0) { Write-Host 'No tracked development services were running.' }
    exit 0
}

foreach ($port in @(8000, 5173)) {
    $listeners = @(Get-ListenerInfo $port)
    if ($listeners.Count -gt 0) {
        $owners = ($listeners | ForEach-Object { "PID $($_.ProcessId) ($($_.Name))" }) -join ', '
        throw "Port $port is already in use by $owners. Run .\scripts\dev.ps1 -Stop to stop this project's services, or free the port yourself. No process was terminated."
    }
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { throw 'uv was not found on PATH. Install uv, reopen PowerShell, and run this script again.' }
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
if (-not $npm) { throw 'npm was not found on PATH. Install Node.js, reopen PowerShell, and run this script again.' }

if (-not (Test-Path (Join-Path $repoRoot '.venv\Scripts\python.exe'))) {
    Write-Host 'Setting up Python dependencies...'
    Push-Location $repoRoot
    try { & $uv.Source sync --locked --extra rag-gpu; if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' } }
    finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $webRoot 'node_modules\vite\bin\vite.js'))) {
    if (-not (Test-Path (Join-Path $webRoot 'package-lock.json'))) {
        throw 'web/package-lock.json is missing, so npm ci cannot run. Restore the lockfile before starting the UI.'
    }
    Write-Host 'Setting up web dependencies...'
    Push-Location $webRoot
    try { & $npm.Source ci; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' } }
    finally { Pop-Location }
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$stdinPath = Join-Path $runtimeDir 'service.stdin'
if (-not (Test-Path -LiteralPath $stdinPath)) {
    New-Item -ItemType File -Path $stdinPath | Out-Null
}
$originalAgentMode = $env:AGENT_MODE
if ($Mock) { $env:AGENT_MODE = 'mock' }
else { Remove-Item Env:AGENT_MODE -ErrorAction SilentlyContinue }
$started = @()
try {
    $api = Start-Process -FilePath $uv.Source `
        -ArgumentList @('run', 'python', '-m', 'uvicorn', 'agentic_ops.api:app', '--app-dir', 'src', '--host', '127.0.0.1', '--port', '8000') `
        -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardInput $stdinPath `
        -RedirectStandardOutput (Join-Path $runtimeDir 'api.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'api.stderr.log')
    $started += [pscustomobject]@{ Name = 'api'; ProcessId = $api.Id }

    $ui = Start-Process -FilePath $npm.Source `
        -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173', '--strictPort') `
        -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardInput $stdinPath `
        -RedirectStandardOutput (Join-Path $runtimeDir 'web.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'web.stderr.log')
    $started += [pscustomobject]@{ Name = 'web'; ProcessId = $ui.Id }
} catch {
    foreach ($process in $started) { Stop-ProcessTree $process.ProcessId }
    throw
} finally {
    if ($Mock) {
        if ($null -eq $originalAgentMode) { Remove-Item Env:AGENT_MODE -ErrorAction SilentlyContinue }
        else { $env:AGENT_MODE = $originalAgentMode }
    }
}

$deadline = [DateTime]::UtcNow.AddSeconds(30)
$apiReady = $false
$webReady = $false
while ([DateTime]::UtcNow -lt $deadline -and (-not $apiReady -or -not $webReady)) {
    if (-not $apiReady) {
        try { $null = Invoke-RestMethod "$apiUrl/api/health" -TimeoutSec 2; $apiReady = $true } catch { }
    }
    if (-not $webReady) {
        try { $null = Invoke-WebRequest $webUrl -TimeoutSec 2 -UseBasicParsing; $webReady = $true } catch { }
    }
    if (-not $apiReady -or -not $webReady) { Start-Sleep -Milliseconds 400 }
}

if (-not $apiReady -or -not $webReady) {
    foreach ($port in @(8000, 5173)) {
        foreach ($listener in @(Get-ListenerInfo $port)) {
            $expected = if ($port -eq 8000) {
                $listener.CommandLine -match 'uvicorn' -and $listener.CommandLine -match 'agentic_ops\.api:app' `
                    -and $listener.ExecutablePath `
                    -and $listener.ExecutablePath.IndexOf($repoRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            } else {
                $listener.CommandLine -match 'vite' -and $listener.CommandLine -match '5173'
            }
            if ($expected) { Stop-ProcessTree $listener.ProcessId }
        }
    }
    foreach ($process in $started) { Stop-ProcessTree $process.ProcessId }
    throw "The services did not both become ready. Check logs under $runtimeDir."
}

@{ api_pid = $api.Id; web_pid = $ui.Id } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host ''
Write-Host 'Signalroom is ready.' -ForegroundColor Green
Write-Host "UI:      $webUrl"
Write-Host "API:     $apiUrl/api/health"
Write-Host "Stop:    & '$PSScriptRoot\dev.ps1' -Stop"
Write-Host "Logs:    $runtimeDir"
if ($OpenBrowser) { Start-Process -FilePath $webUrl }
