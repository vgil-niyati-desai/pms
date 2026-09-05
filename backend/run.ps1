# Starts the backend using this project's virtual environment explicitly.
#
# Why this exists: there is a uvicorn.exe on the system PATH belonging to the
# global Python install, which does NOT have fastapi installed. Running a bare
# `uvicorn app.main:app` without activating the venv picks up that global one
# and fails with "ModuleNotFoundError: No module named 'fastapi'".
# Calling the venv's python directly sidesteps PATH entirely, so this works
# whether or not the venv happens to be activated.
#
# Usage (from the backend folder):
#   .\run.ps1
#   .\run.ps1 -Port 8001

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Error @"
Virtual environment not found at: $venvPython

Create it first, from the backend folder:
    python -m venv venv
    .\venv\Scripts\Activate.ps1
    pip install -r requirements.txt
"@
    exit 1
}

# Fail early with a clear message if dependencies were never installed,
# rather than a confusing traceback from inside uvicorn's reloader.
& $venvPython -c "import fastapi, uvicorn, pymongo, multipart" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error @"
The virtual environment is missing dependencies.

Install them from the backend folder:
    .\venv\Scripts\python.exe -m pip install -r requirements.txt
"@
    exit 1
}

Write-Host "Starting backend on http://127.0.0.1:$Port (interpreter: $venvPython)" -ForegroundColor Cyan
& $venvPython -m uvicorn app.main:app --reload --port $Port
