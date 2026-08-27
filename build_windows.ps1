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

# Optional installer build if Inno Setup is installed.
$innoCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)
$inno = $innoCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($inno) {
    Write-Host "Inno Setup found. Building installer..." -ForegroundColor Cyan
    & $inno "installer\DrawWise.iss"
    Write-Host "Installer output:" -ForegroundColor Green
    Write-Host "  $PSScriptRoot\installer-output\DrawWise-Setup-5.3.3.exe"
} else {
    Write-Host "Inno Setup was not found, so the portable desktop app was built but the Setup.exe was not." -ForegroundColor Yellow
    Write-Host "Install Inno Setup on this BUILD computer and run BUILD_INSTALLER_ONLY.bat if you want a normal installer." -ForegroundColor Yellow
}
