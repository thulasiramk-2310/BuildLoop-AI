# Stop only the processes recorded by scripts\start.ps1 (and their child trees). Unrelated processes are never touched.
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root ".run\pids.json"
if (-not (Test-Path $pidFile)) { Write-Host "Nothing recorded as running."; exit 0 }
foreach ($p in (Get-Content $pidFile | ConvertFrom-Json)) {
  $proc = Get-Process -Id $p.pid -ErrorAction SilentlyContinue
  if ($proc) {
    # /T kills the child tree (uvicorn reloader workers, vite esbuild). /F forces.
    taskkill /PID $p.pid /T /F | Out-Null
    Write-Host "Stopped $($p.name) (PID $($p.pid))"
  } else {
    Write-Host "$($p.name) (PID $($p.pid)) was not running"
  }
}
Remove-Item $pidFile
