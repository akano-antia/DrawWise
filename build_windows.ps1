$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "=== DrawWise Windows Desktop Builder ===" -ForegroundColor Cyan
Write-Host "This creates a standalone Windows application. VS Code is not required." -ForegroundColor Gray
Write-Host ""

$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
    $pythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
    $pythonArgs = @()
} else {
    throw "Python was not found. Install Python 3.10+ on this BUILD computer, then run this script again."
}

$venv = Join-Path $PSScriptRoot ".build-venv"
if (-not (Test-Path $venv)) {
    & $python @pythonArgs -m venv $venv
}

$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install --upgrade pyinstaller
& $venvPython -m pip install -r "requirements.txt"

if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }

& $venvPython -m PyInstaller --noconfirm "DrawWise.spec"

$exe = Join-Path $PSScriptRoot "dist\DrawWise\DrawWise.exe"
if (-not (Test-Path $exe)) {
    throw "Build did not create $exe"
}

Write-Host ""
Write-Host "Desktop app built successfully:" -ForegroundColor Green
Write-Host "  $exe"
Write-Host ""
Write-Host "The entire dist\DrawWise folder is portable and can run on another Windows PC without Python or VS Code." -ForegroundColor Yellow
Write-Host ""

Write-Host "For a normal Setup.exe, use the separate DrawWise 5.4.0 Installer Builder." -ForegroundColor Cyan
Write-Host "That builder creates installer-output\DrawWise-Setup-5.4.0.exe without requiring Inno Setup." -ForegroundColor Gray
