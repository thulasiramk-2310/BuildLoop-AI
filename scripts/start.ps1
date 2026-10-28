# Start backend (:8000) and frontend (:5173) once, in the background, with PID tracking.
# Refuses to start duplicates. Stop with scripts\stop.ps1. Logs: .run\api.log, .run\web.log
param([switch]$Http)   # -Http: plain http for browser automation (BUILDLOOP_HTTP=1). Default is https for phone camera access.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$run = Join-Path $root ".run"
New-Item -ItemType Directory -Force $run | Out-Null
$pidFile = Join-Path $run "pids.json"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Run scripts\setup.ps1 first."; exit 1 }

function Test-PortBusy($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }

if (Test-Path $pidFile) {
  $alive = (Get-Content $pidFile | ConvertFrom-Json) | Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue }
  if ($alive) { Write-Host "Already running (PIDs: $($alive.pid -join ', ')). Use scripts\stop.ps1 first."; exit 0 }
  Remove-Item $pidFile
}
foreach ($port in 8000, 5173) {
  if (Test-PortBusy $port) { Write-Error "Port $port is in use by a process BuildLoop did not start. Not touching it."; exit 1 }
}

$api = Start-Process -PassThru -WindowStyle Hidden -FilePath $py -WorkingDirectory (Join-Path $root "apps\api") `
  -ArgumentList "-m", "uvicorn", "buildloop.main:app", "--host", "0.0.0.0", "--port", "8000" `
  -RedirectStandardOutput (Join-Path $run "api.log") -RedirectStandardError (Join-Path $run "api.err.log")

if ($Http) { $env:BUILDLOOP_HTTP = "1" } else { Remove-Item Env:BUILDLOOP_HTTP -ErrorAction SilentlyContinue }
$node = (Get-Command node).Source
$vite = Join-Path $root "apps\web\node_modules\vite\bin\vite.js"
$web = Start-Process -PassThru -WindowStyle Hidden -FilePath $node -WorkingDirectory (Join-Path $root "apps\web") `
  -ArgumentList $vite, "--port", "5173", "--strictPort" `
  -RedirectStandardOutput (Join-Path $run "web.log") -RedirectStandardError (Join-Path $run "web.err.log")

@(
  @{ name = "api"; pid = $api.Id; port = 8000 },
  @{ name = "web"; pid = $web.Id; port = 5173 }
) | ConvertTo-Json | Set-Content $pidFile

for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  try { $h = Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 2; if ($h.status -eq "ok") { break } } catch { }
}
$scheme = if ($Http) { "http" } else { "https" }
Write-Host "API  : http://localhost:8000   (PID $($api.Id))"
Write-Host "Web  : ${scheme}://localhost:5173   (PID $($web.Id))"
Write-Host "Stop : scripts\stop.ps1"
