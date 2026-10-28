# Start backend (:8000) and frontend (https://localhost:5173). Ctrl+C stops both.
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "Run scripts\setup.ps1 first."; exit 1 }

$api = Start-Process -PassThru -NoNewWindow -FilePath $py -WorkingDirectory (Join-Path $root "apps\api") `
  -ArgumentList "-m", "uvicorn", "buildloop.main:app", "--host", "0.0.0.0", "--port", "8000"
try {
  Push-Location (Join-Path $root "apps\web")
  npx vite --port 5173
} finally {
  Pop-Location
  if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
