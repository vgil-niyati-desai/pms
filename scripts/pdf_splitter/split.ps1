# Runs the PDF splitter using this tool's own virtual environment.
#
# Why this exists: the splitter needs pypdf and openpyxl, which the backend
# does not use. Rather than adding them to backend\requirements.txt (and to
# the machine's other Python installs), this tool keeps its own .venv beside
# it, and this script calls that interpreter directly so it works whether or
# not any venv happens to be activated.
#
# Usage (from anywhere):
#   .\scripts\pdf_splitter\split.ps1 combined.pdf index.xlsx -Out .\output\batch1
#   .\scripts\pdf_splitter\split.ps1 combined.pdf index.xlsx -DryRun
#
# Any other splitter flag can be passed through after the arguments, e.g.
#   .\scripts\pdf_splitter\split.ps1 a.pdf i.xlsx --infer-end-page --overwrite

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Pdf,

    [Parameter(Mandatory = $true, Position = 1)]
    [string]$Index,

    [string]$Out,

    [switch]$DryRun,

    # Everything not matched above is forwarded to the Python CLI unchanged.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Extra
)

$ErrorActionPreference = "Stop"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Error @"
The splitter's virtual environment was not found at:
    $venvPython

Create it once, from the project root:
    python -m venv scripts\pdf_splitter\.venv
    scripts\pdf_splitter\.venv\Scripts\python.exe -m pip install -r scripts\pdf_splitter\requirements.txt
"@
    exit 1
}

# Fail early with a clear message if the dependencies were never installed,
# rather than a traceback from inside the tool.
& $venvPython -c "import pypdf, openpyxl" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error @"
The splitter's virtual environment is missing dependencies. Install them with:
    scripts\pdf_splitter\.venv\Scripts\python.exe -m pip install -r scripts\pdf_splitter\requirements.txt
"@
    exit 1
}

$arguments = @((Join-Path $PSScriptRoot "split.py"), $Pdf, $Index)
if ($Out)    { $arguments += @("--out", $Out) }
if ($DryRun) { $arguments += "--dry-run" }
if ($Extra)  { $arguments += $Extra }

& $venvPython @arguments
exit $LASTEXITCODE
