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
    [string]$CertPass,
    [string]$Version          # override the installer version (e.g. from a CI tag)
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> Ensuring the .ico exists" -ForegroundColor Cyan
if (-not (Test-Path "assets\icon.ico")) {
    python -c "from PIL import Image; Image.open('assets/icon.png').convert('RGBA').save('assets/icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
}

Write-Host "==> Ensuring aria2c (BitTorrent/magnet engine)" -ForegroundColor Cyan
$ariaVersion = "1.37.0"
$ariaBuild   = "aria2-$ariaVersion-win-64bit-build1"
$ariaExe     = "bin\aria2c.exe"
if (Test-Path $ariaExe) {
    Write-Host "    already present: $ariaExe" -ForegroundColor DarkGray
} else {
    try {
        New-Item -ItemType Directory -Force bin | Out-Null
        $zip = Join-Path $env:TEMP "$ariaBuild.zip"
        $url = "https://github.com/aria2/aria2/releases/download/release-$ariaVersion/$ariaBuild.zip"
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        $sha = (Get-FileHash $zip -Algorithm SHA256).Hash
        Write-Host "    SHA256 $sha" -ForegroundColor DarkGray
        Write-Host "    ^ verify against https://github.com/aria2/aria2/releases/tag/release-$ariaVersion" -ForegroundColor DarkGray
        $tmp = Join-Path $env:TEMP $ariaBuild
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        Copy-Item (Join-Path $tmp "$ariaBuild\aria2c.exe") $ariaExe -Force
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        Write-Host "    aria2c.exe -> $ariaExe" -ForegroundColor Green
    } catch {
        Write-Warning "aria2c fetch failed ($($_.Exception.Message)); build continues without the torrent engine."
    }
}

Write-Host "==> Ensuring ffmpeg (yt-dlp merge -> 1080p/4K + DASH-only videos)" -ForegroundColor Cyan
$ffExe = "bin\ffmpeg.exe"
if (Test-Path $ffExe) {
    Write-Host "    already present: $ffExe" -ForegroundColor DarkGray
} else {
    try {
        New-Item -ItemType Directory -Force bin | Out-Null
        # BtbN static Windows build (essentials would also do; gpl is fine)
        $ffUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        $zip = Join-Path $env:TEMP "ffmpeg-win64.zip"
        Invoke-WebRequest -Uri $ffUrl -OutFile $zip -UseBasicParsing
        $tmp = Join-Path $env:TEMP "ffmpeg-extract"
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $found = Get-ChildItem -Path $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
        if ($found) { Copy-Item $found.FullName $ffExe -Force; Write-Host "    ffmpeg.exe -> $ffExe" -ForegroundColor Green }
        else { Write-Warning "ffmpeg.exe not found in the archive" }
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "ffmpeg fetch failed ($($_.Exception.Message)); build continues, yt-dlp limited to <=720p muxed."
    }
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
    $isccArgs = @()
    if ($Version) { $isccArgs += "/DAppVersion=$Version" }
    & $iscc @isccArgs "installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
    if ($Sign) {
        Write-Host "==> Signing the installer" -ForegroundColor Cyan
        Invoke-Sign (Get-ChildItem "dist\installer\*.exe" | Select-Object -First 1).FullName
    }
}

Write-Host "==> Done. Output in dist\" -ForegroundColor Green
