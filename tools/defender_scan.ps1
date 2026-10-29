<#
.SYNOPSIS
    Scans what is about to be published with Microsoft Defender and fails if it
    finds anything, or cannot scan.

.DESCRIPTION
    For the release build. It catches what Defender knows by signature - a
    bundled tool or library that has turned up on its list - before the files
    reach anyone.

    It does NOT catch what happened to 2.7.1: Defender's machine-learning guess
    ("...!ml") on one PC, as the app was being installed. The same exe scanned
    clean here, and installed and ran clean on a GitHub Windows machine with
    real-time and cloud protection switched on.

    GitHub's runners exclude C:\ and D:\ from Defender. With those in place a
    scan reads nothing and passes, so they are removed first.

.EXAMPLE
    .\tools\defender_scan.ps1 dist\HyperFetch, dist\installer
#>
param([Parameter(Mandatory = $true)][string[]]$Paths)

$ErrorActionPreference = 'Stop'

foreach ($x in @((Get-MpPreference).ExclusionPath)) {
    if ($x) { Remove-MpPreference -ExclusionPath $x }
}
# On a runner fresh from boot Defender's service may not answer yet - "The
# remote procedure call failed", then "Provider load failure" here: 2.8.1's
# release build, and both of 2.8.4's (2026-10-29). Wait for it, three minutes
# at most; one that never answers still fails the release, unscanned.
$status = $null
foreach ($try in 1..18) {
    try { $status = Get-MpComputerStatus; break }
    catch {
        $svc = (Get-Service WinDefend -ErrorAction SilentlyContinue).Status
        Write-Host "Defender is not answering ($try of 18, service: $svc): $($_.Exception.Message)"
        if ($try -eq 1) {
            try { Start-Service WinDefend } catch { Write-Host "could not start it: $($_.Exception.Message)" }
        }
        Start-Sleep -Seconds 10
    }
}
if (-not $status) {
    Write-Host "::error::Defender never answered - nothing was scanned"
    exit 1
}
try { Update-MpSignature } catch { Write-Host "could not update Defender's signatures: $($_.Exception.Message)" }
$status = Get-MpComputerStatus
Write-Host "Defender $($status.AMProductVersion), signatures $($status.AntivirusSignatureVersion) of $($status.AntivirusSignatureLastUpdated)"

$scanner = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
$failed = 0
foreach ($p in $Paths) {
    $full = (Resolve-Path $p).Path
    Write-Host "==> scanning $full"
    & $scanner -Scan -ScanType 3 -File $full -DisableRemediation | Out-String | Write-Host
    # 0: nothing found. 2: something found. Anything else: it did not scan.
    if ($LASTEXITCODE -ne 0) {
        $failed++
        Write-Host "::error::Defender did not pass $p (MpCmdRun exit code $LASTEXITCODE)"
    }
}
Get-MpThreatDetection | ForEach-Object {
    Write-Host ("detected: threat {0} in {1}" -f $_.ThreatID, ($_.Resources -join ' | '))
}
Get-MpThreat | ForEach-Object { Write-Host ("threat: {0}" -f $_.ThreatName) }
if ($failed) { exit 1 }
Write-Host "Defender found nothing."
