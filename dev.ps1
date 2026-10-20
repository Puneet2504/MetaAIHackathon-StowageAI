# Starts the backend (port 8000) and frontend (port 5173) in two windows and opens the browser.
#   powershell -ExecutionPolicy Bypass -File .\dev.ps1

$Root = $PSScriptRoot
if (-not (Test-Path "$Root\backend\.venv")) { Write-Host "Run setup.ps1 first." -ForegroundColor Yellow; exit 1 }

Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\backend'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\frontend'; npm run dev"
Start-Sleep -Seconds 6
Start-Process "http://localhost:5173"
