# One-time setup for the Product Tanker Planner on Windows 11.
# Run from the project folder (D:\MetaAIHackathon):
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Run($exe, [string[]]$argList) {
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $exe $($argList -join ' ')" }
}

Step "Checking tools"
$missing = @()
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { $missing += "Python 3.12:  winget install -e --id Python.Python.3.12" }
if (-not (Get-Command node -ErrorAction SilentlyContinue))   { $missing += "Node.js LTS:  winget install -e --id OpenJS.NodeJS.LTS" }
if (-not (Get-Command git -ErrorAction SilentlyContinue))    { $missing += "Git:          winget install -e --id Git.Git" }
if ($missing.Count -gt 0) {
    Write-Host "Missing tools. Install them, open a NEW PowerShell window, then run setup.ps1 again:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  $_" }
    exit 1
}

$pyVersion = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])") 2>$null
if (-not $pyVersion -or [version]$pyVersion -lt [version]"3.10") {
    Write-Host "Python 3.10 or newer is required (found: '$pyVersion')." -ForegroundColor Yellow
    Write-Host "If Windows opens the Microsoft Store instead, turn off the python.exe alias in"
    Write-Host "Settings > Apps > Advanced app settings > App execution aliases."
    exit 1
}
Write-Host "Python $pyVersion, Node $(node --version), $(git --version)"

Step "Backend: virtual environment and packages"
Push-Location "$Root\backend"
try {
    if (-not (Test-Path ".venv")) { Run "python" @("-m", "venv", ".venv") }
    $py = ".\.venv\Scripts\python.exe"
    Run $py @("-m", "pip", "install", "--upgrade", "pip")
    Run $py @("-m", "pip", "install", "-r", "requirements.txt")
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created backend\.env" }
    Step "Backend: running engine tests"
    Run $py @("-m", "unittest", "discover", "tests")
} finally { Pop-Location }

Step "Frontend: npm packages"
Push-Location "$Root\frontend"
try { Run "npm" @("install") } finally { Pop-Location }

Step "Git repository"
if (-not (Test-Path "$Root\.git")) {
    Run "git" @("-C", $Root, "init")
    Run "git" @("-C", $Root, "add", ".")
    & git -C $Root commit -m "Initial project setup" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Repository created, but the first commit was skipped. Set your name and email, then commit:" -ForegroundColor Yellow
        Write-Host '  git config --global user.name "Your Name"'
        Write-Host '  git config --global user.email "you@example.com"'
    }
} else { Write-Host "Already a git repository" }

Step "Done"
Write-Host "Start the app with:  powershell -ExecutionPolicy Bypass -File .\dev.ps1"
