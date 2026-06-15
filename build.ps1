<#
  Build script for HyperFetch (Windows).

  Usage:
    .\build.ps1                      # build the onedir app into dist\
    .\build.ps1 -Installer           # also build the Inno Setup installer
    .\build.ps1 -Sign -CertPath x.pfx -CertPass ****   # sign the exe + installer

  Requirements:
    - Python 3.10+ with the project deps:  pip install -r requirements.txt pyinstaller pillow
    - For -Installer:  Inno Setup 6 (iscc.exe on PATH or at the default location)
    - For -Sign:       a code-signing cert (.pfx) and Windows SDK signtool.exe
#>
param(
    [switch]$Installer,
    [switch]$Sign,
    [string]$CertPath,
    [string]$CertPass
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Ensuring the .ico exists" -ForegroundColor Cyan
if (-not (Test-Path "assets\icon.ico")) {
    python -c "from PIL import Image; Image.open('assets/icon.png').convert('RGBA').save('assets/icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
}

Write-Host "==> Cleaning previous build" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Building with PyInstaller" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean HyperFetch.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$exe = "dist\HyperFetch\HyperFetch.exe"

Write-Host "==> Smoke-testing the frozen binary" -ForegroundColor Cyan
& $exe --selftest
if ($LASTEXITCODE -ne 0) { throw "selftest failed" }

function Invoke-Sign($target) {
    $signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
    if (-not $signtool) { throw "signtool.exe not found (install the Windows SDK)" }
    & $signtool sign /f $CertPath /p $CertPass /fd SHA256 `
        /tr http://timestamp.digicert.com /td SHA256 $target
    if ($LASTEXITCODE -ne 0) { throw "signing failed for $target" }
}

if ($Sign) {
    if (-not $CertPath) { throw "-Sign requires -CertPath <pfx>" }
    Write-Host "==> Signing the app exe" -ForegroundColor Cyan
    Invoke-Sign $exe
}

if ($Installer) {
    Write-Host "==> Building the Inno Setup installer" -ForegroundColor Cyan
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
    if (-not (Test-Path $iscc)) { throw "Inno Setup (iscc.exe) not found" }
    & $iscc "installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
    if ($Sign) {
        Write-Host "==> Signing the installer" -ForegroundColor Cyan
        Invoke-Sign (Get-ChildItem "dist\installer\*.exe" | Select-Object -First 1).FullName
    }
}

Write-Host "==> Done. Output in dist\" -ForegroundColor Green
