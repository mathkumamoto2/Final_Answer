# Official Chrome for Testing downloader (no Selenium Manager, no version guessing)
param([string]$ChromeVersion = '')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = $PSScriptRoot
$TargetDir = Join-Path $Root 'ex1_web-scraping'
$Driver = Join-Path $TargetDir 'chromedriver.exe'
$DigestFile = Join-Path $TargetDir 'chromedriver.sha256'
$SourceFile = Join-Path $TargetDir 'chromedriver.source.json'

if (-not (Test-Path (Join-Path $TargetDir '1-2.py'))) {
    throw 'Run this script from the Python homework directory.'
}
if (Test-Path $Driver) {
    throw "chromedriver.exe already exists at $Driver. Do not silently overwrite it. Review the old file first."
}

if (-not $ChromeVersion) {
    $Candidates = @()
    foreach ($BaseDir in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not [string]::IsNullOrWhiteSpace($BaseDir)) {
            $Candidates += (Join-Path $BaseDir 'Google\Chrome\Application\chrome.exe')
        }
    }
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            $ChromeVersion = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Candidate).ProductVersion
            Write-Host "[INFO] Chrome: $Candidate"
            break
        }
    }
}
if (-not ($ChromeVersion -match '^(\d+)\.(\d+)\.(\d+)\.(\d+)$')) {
    throw "Could not determine a 4-part Chrome version: $ChromeVersion. Check chrome://version, then pass -ChromeVersion 'X.Y.Z.W'."
}
$Build = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
Write-Host "[INFO] Installed Chrome version: $ChromeVersion"
Write-Host "[INFO] Looking for exact major.minor.build: $Build"

# Official Chrome for Testing JSON version catalog.
$Api = 'https://googlechromelabs.github.io/chrome-for-testing/latest-patch-versions-per-build-with-downloads.json'
$Catalog = Invoke-RestMethod -Uri $Api -ErrorAction Stop
$BuildProp = $Catalog.builds.PSObject.Properties[$Build]
if (-not $BuildProp) {
    throw "No official ChromeDriver for build $Build. Update Chrome and rerun; do not guess a different driver version."
}
$Selected = $BuildProp.Value
$Platform = if ([Environment]::Is64BitOperatingSystem) { 'win64' } else { 'win32' }
$Download = @($Selected.downloads.chromedriver | Where-Object { $_.platform -eq $Platform })
if ($Download.Count -ne 1) {
    throw "No official ChromeDriver $Platform download for $($Selected.version)"
}
$Url = [uri]$Download[0].url
if ($Url.Scheme -ne 'https' -or $Url.Host -ne 'storage.googleapis.com' -or
    -not $Url.AbsolutePath.StartsWith('/chrome-for-testing-public/')) {
    throw "Unexpected ChromeDriver download origin: $Url"
}
if (-not $Selected.version.StartsWith($Build + '.')) {
    throw "ChromeDriver release $($Selected.version) does not match Chrome build $Build"
}

$Temp = Join-Path ([IO.Path]::GetTempPath()) ("cft-driver-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Temp | Out-Null
try {
    $Zip = Join-Path $Temp 'chromedriver.zip'
    Write-Host "[INFO] Official ChromeDriver: $($Selected.version) / $Platform"
    Write-Host "[INFO] Source: $Url"
    Invoke-WebRequest -Uri $Url.AbsoluteUri -OutFile $Zip -UseBasicParsing -ErrorAction Stop
    $Extract = Join-Path $Temp 'unzipped'
    Expand-Archive -LiteralPath $Zip -DestinationPath $Extract -ErrorAction Stop
    $MatchesInZip = @(Get-ChildItem -Path $Extract -Recurse -Filter 'chromedriver.exe' -File)
    if ($MatchesInZip.Count -ne 1) {
        throw "Expected exactly one ChromeDriver executable in ZIP; got $($MatchesInZip.Count)"
    }

    $DownloadedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $MatchesInZip[0].FullName).Hash.ToLowerInvariant()
    if ($DownloadedHash -notmatch '^[0-9a-f]{64}$') { throw 'Invalid SHA-256 output' }

    Copy-Item -LiteralPath $MatchesInZip[0].FullName -Destination $Driver -ErrorAction Stop
    $LocalHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Driver).Hash.ToLowerInvariant()
    if ($DownloadedHash -ne $LocalHash) { throw 'SHA-256 changed during copy' }

    [IO.File]::WriteAllText($DigestFile, $LocalHash + "`n", [Text.Encoding]::ASCII)
    $Manifest = [ordered]@{
        installed_chrome_version = $ChromeVersion
        chromedriver_version = $Selected.version
        platform = $Platform
        source = $Url.AbsoluteUri
        chrome_for_testing_catalog = $Api
        sha256 = $LocalHash
    }
    $Manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $SourceFile -Encoding UTF8
    Write-Host "[PASS] Official ChromeDriver downloaded: $Driver" -ForegroundColor Green
    Write-Host "[PASS] SHA-256: $LocalHash"
    Write-Host "[INFO] Before executing or committing it, review your antivirus status. If it warns, STOP."
}
finally {
    Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
