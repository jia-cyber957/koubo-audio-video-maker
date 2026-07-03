[CmdletBinding()]
param(
  [switch]$CheckOnly,
  [switch]$ForceDownload,
  [string]$InstallRoot = "$env:LOCALAPPDATA\Programs\koubo-audio-video-maker\ffmpeg"
)

$ErrorActionPreference = 'Stop'

function Find-Ffmpeg {
  $command = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  $known = @(
    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
    "$env:LOCALAPPDATA\Programs\ffmpeg\bin\ffmpeg.exe",
    "$env:ProgramFiles\ffmpeg\bin\ffmpeg.exe"
  )
  foreach ($file in $known) { if ($file -and (Test-Path -LiteralPath $file)) { return $file } }
  $packages = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
  if (Test-Path -LiteralPath $packages) {
    $match = Get-ChildItem -LiteralPath $packages -Filter ffmpeg.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($match) { return $match.FullName }
  }
  return $null
}

function Find-FfprobeNear([string]$FfmpegPath) {
  if (-not $FfmpegPath) { return $null }
  $candidate = Join-Path (Split-Path -Parent $FfmpegPath) 'ffprobe.exe'
  if (Test-Path -LiteralPath $candidate) { return $candidate }
  $command = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
  if ($command -and (Test-Path -LiteralPath $command.Source)) { return $command.Source }
  return $null
}

function Install-StableCopy([string]$FfmpegPath) {
  if (-not $FfmpegPath -or -not (Test-Path -LiteralPath $FfmpegPath)) { return $null }
  $targetBin = Join-Path $InstallRoot 'bin'
  New-Item -ItemType Directory -Path $targetBin -Force | Out-Null
  $targetFfmpeg = Join-Path $targetBin 'ffmpeg.exe'
  $targetFfprobe = Join-Path $targetBin 'ffprobe.exe'
  if ($FfmpegPath.TrimEnd('\') -ine $targetFfmpeg.TrimEnd('\')) {
    Copy-Item -LiteralPath $FfmpegPath -Destination $targetFfmpeg -Force
  }
  $ffprobe = Find-FfprobeNear $FfmpegPath
  if ($ffprobe -and $ffprobe.TrimEnd('\') -ine $targetFfprobe.TrimEnd('\')) {
    Copy-Item -LiteralPath $ffprobe -Destination $targetFfprobe -Force
  }
  return $targetFfmpeg
}

function Add-UserPath([string]$Directory) {
  $current = [Environment]::GetEnvironmentVariable('Path', 'User')
  $parts = @($current -split ';' | Where-Object { $_ })
  $kept = @($parts | Where-Object { $_.TrimEnd('\') -ine $Directory.TrimEnd('\') })
  [Environment]::SetEnvironmentVariable('Path', ((@($Directory) + $kept) -join ';'), 'User')
  if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') })) {
    $env:Path = "$Directory;$env:Path"
  }
}

function Find-Winget {
  $command = Get-Command winget.exe -ErrorAction SilentlyContinue
  if ($command -and (Test-Path -LiteralPath $command.Source)) { return $command.Source }
  $package = Get-AppxPackage Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue | Sort-Object Version -Descending | Select-Object -First 1
  if ($package) {
    $candidate = Join-Path $package.InstallLocation 'winget.exe'
    if (Test-Path -LiteralPath $candidate) { return $candidate }
  }
  return $null
}

$existing = Find-Ffmpeg
if ($existing -and -not $ForceDownload) {
  if ($CheckOnly) {
    & $existing -version | Select-Object -First 1
    Write-Output "FFMPEG_PATH=$existing"
    Write-Output ("BIN_PATH=" + (Split-Path -Parent $existing))
    exit 0
  }
  try {
    $stable = Install-StableCopy $existing
    if ($stable) { $existing = $stable }
  } catch {
    Write-Warning "Found ffmpeg but could not copy it to stable user path: $($_.Exception.Message)"
  }
  Add-UserPath (Split-Path -Parent $existing)
  & $existing -version | Select-Object -First 1
  $probe = Join-Path (Split-Path -Parent $existing) 'ffprobe.exe'
  if (Test-Path -LiteralPath $probe) { & $probe -version | Select-Object -First 1 }
  Write-Output "FFMPEG_PATH=$existing"
  Write-Output ("BIN_PATH=" + (Split-Path -Parent $existing))
  exit 0
}
if ($CheckOnly) { throw 'ffmpeg was not found. Run this script without -CheckOnly to install it.' }

$installed = $false
if (-not $ForceDownload) {
  $winget = Find-Winget
  if ($winget) {
    & $winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -eq 0) { $installed = $true }
  }
}

$ffmpeg = Find-Ffmpeg
if (-not $ffmpeg) {
  $tempRoot = Join-Path $env:TEMP ("koubo-audio-video-maker-ffmpeg-" + [guid]::NewGuid().ToString('N'))
  $archive = Join-Path $tempRoot 'ffmpeg.zip'
  $extract = Join-Path $tempRoot 'extract'
  New-Item -ItemType Directory -Path $extract -Force | Out-Null
  try {
    Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $archive -UseBasicParsing
    Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
    $downloaded = Get-ChildItem -LiteralPath $extract -Filter ffmpeg.exe -File -Recurse | Select-Object -First 1
    if (-not $downloaded) { throw 'ffmpeg.exe was not found in the downloaded archive.' }
    $sourceBin = Split-Path -Parent $downloaded.FullName
    $targetBin = Join-Path $InstallRoot 'bin'
    New-Item -ItemType Directory -Path $targetBin -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceBin 'ffmpeg.exe') -Destination $targetBin -Force
    Copy-Item -LiteralPath (Join-Path $sourceBin 'ffprobe.exe') -Destination $targetBin -Force
    $ffplay = Join-Path $sourceBin 'ffplay.exe'
    if (Test-Path -LiteralPath $ffplay) { Copy-Item -LiteralPath $ffplay -Destination $targetBin -Force }
    $ffmpeg = Join-Path $targetBin 'ffmpeg.exe'
  } finally {
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
  }
}

if (-not $ffmpeg -or -not (Test-Path -LiteralPath $ffmpeg)) { throw 'ffmpeg could not be located after installation.' }
try {
  $stableAfterInstall = Install-StableCopy $ffmpeg
  if ($stableAfterInstall) { $ffmpeg = $stableAfterInstall }
} catch {
  Write-Warning "Could not copy ffmpeg to stable user path: $($_.Exception.Message)"
}
$bin = Split-Path -Parent $ffmpeg
Add-UserPath $bin
& $ffmpeg -version | Select-Object -First 1
$ffprobePath = Join-Path $bin 'ffprobe.exe'
if (Test-Path -LiteralPath $ffprobePath) { & $ffprobePath -version | Select-Object -First 1 }
Write-Output "FFMPEG_PATH=$ffmpeg"
Write-Output "BIN_PATH=$bin"
