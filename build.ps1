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
    [string]$Version         # override the installer version (e.g. from a CI tag)
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

# Building inside a synced folder does not work: OneDrive opens the freshly
# written exe to upload it, and the next build cannot delete it — "Access is
# denied" on HyperFetch.exe, from a process that is not the app. It also uploads
# a few hundred MB of artifact that is gitignored anyway.
if ($PSScriptRoot -like "*\OneDrive\*" -and -not $env:HYPERFETCH_ALLOW_SYNCED_BUILD) {
    Write-Host "==> This repo is inside OneDrive" -ForegroundColor Yellow
    Write-Host "    Building here fails once OneDrive locks the exe. Use:" -ForegroundColor Yellow
    Write-Host "      python -m PyInstaller --noconfirm --clean --distpath C:\dev\hf-build\dist --workpath C:\dev\hf-build\work HyperFetch.spec" -ForegroundColor DarkGray
    Write-Host "    or exclude dist\ and build\ from sync, then set" -ForegroundColor Yellow
    Write-Host "      `$env:HYPERFETCH_ALLOW_SYNCED_BUILD = 1" -ForegroundColor DarkGray
    throw "refusing to build inside a synced folder"
}

Write-Host "==> Cleaning previous build" -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "==> Building with PyInstaller" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean "HyperFetch.spec"
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
    # Only the machine-wide location was checked, but winget installs Inno
    # Setup per-user by default, so a working install still read as missing.
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        $iscc = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $iscc) { throw "Inno Setup (iscc.exe) not found - winget install JRSoftware.InnoSetup" }
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
