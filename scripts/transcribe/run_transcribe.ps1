[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$MediaPath,
  [Parameter(Mandatory = $true)][string]$BaseDir,
  [ValidateSet('auto','flash','v3-standard')][string]$Engine = 'auto',
  [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = (@($machinePath, $userPath, $env:Path) | Where-Object { $_ }) -join ';'

function Resolve-Tool([string]$Name, [string[]]$Known = @()) {
  foreach ($file in $Known) { if ($file -and (Test-Path -LiteralPath $file)) { return $file } }
  $command = Get-Command $Name -ErrorAction SilentlyContinue
  if ($command -and $command.Source -and $command.Source -notmatch '\\WindowsApps\\') { return $command.Source }
  throw "Required tool was not found: $Name"
}

$media = (Resolve-Path -LiteralPath $MediaPath).Path
$base = [IO.Path]::GetFullPath($BaseDir)
$gitBash = Resolve-Tool 'git-bash-not-on-path' @(
  $env:GIT_BASH,
  'C:\Program Files\Git\bin\bash.exe',
  'C:\Program Files\Git\usr\bin\bash.exe',
  "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)
$ffmpeg = Resolve-Tool 'ffmpeg.exe' @(
  $env:FFMPEG_PATH,
  $(if ($env:SKILL_LOCAL_BIN) { Join-Path $env:SKILL_LOCAL_BIN 'ffmpeg.exe' }),
  $(if ($env:CODEX_WORKSPACE_BIN) { Join-Path $env:CODEX_WORKSPACE_BIN 'ffmpeg.exe' }),
  (Join-Path (Split-Path -Parent $scriptDir) 'work\bin\ffmpeg.exe'),
  (Join-Path (Split-Path -Parent (Split-Path -Parent $scriptDir)) 'work\bin\ffmpeg.exe'),
  "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
  "$env:LOCALAPPDATA\Programs\koubo-audio-video-maker\ffmpeg\bin\ffmpeg.exe",
  "$env:LOCALAPPDATA\Programs\ffmpeg\bin\ffmpeg.exe",
  "$env:ProgramFiles\ffmpeg\bin\ffmpeg.exe"
)
$node = Resolve-Tool 'node.exe' @('C:\Program Files\nodejs\node.exe')
$python = Resolve-Tool 'python3.exe' @(
  "$env:LOCALAPPDATA\Programs\Python\Python312\python3.exe",
  "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
)
$curl = Resolve-Tool 'curl.exe' @("$env:SystemRoot\System32\curl.exe")

function Convert-ToMsys([string]$Value) {
  $converted = & $gitBash -lc 'cygpath -u -- "$1"' _ $Value
  if ($LASTEXITCODE -ne 0 -or -not $converted) { throw "Path conversion failed: $Value" }
  return ($converted | Select-Object -Last 1).Trim()
}

$shimDir = Join-Path $base '.tool_shims'
New-Item -ItemType Directory -Path $shimDir -Force | Out-Null
$pythonUnix = Convert-ToMsys $python
$pythonShim = Join-Path $shimDir 'python3'
@"
#!/usr/bin/env bash
exec "$pythonUnix" "`$@"
"@ | Set-Content -LiteralPath $pythonShim -Encoding ASCII
$pythonShimUnix = Convert-ToMsys $pythonShim
& $gitBash -lc 'chmod +x "$1"' _ $pythonShimUnix | Out-Null

$toolDirs = @($shimDir) + (@($ffmpeg,$node,$python,$curl,$gitBash) | ForEach-Object { Split-Path -Parent $_ })
$toolDirs = $toolDirs | Select-Object -Unique
$env:Path = (($toolDirs + @($env:Path)) -join ';')

$mediaUnix = Convert-ToMsys $media
$baseUnix = Convert-ToMsys $base
$runnerUnix = Convert-ToMsys (Join-Path $scriptDir 'run_transcribe.sh')

function Get-MediaParts([string]$PathValue) {
  $item = Get-Item -LiteralPath $PathValue
  $exts = @('.mp3','.wav','.wave','.m4a','.aac','.flac','.ogg','.opus','.mp4','.mov','.m4v')
  if (-not $item.PSIsContainer) {
    if ($exts -contains $item.Extension.ToLowerInvariant()) { return @($item.FullName) }
    return @()
  }
  return @(Get-ChildItem -LiteralPath $item.FullName -File | Where-Object {
    $exts -contains $_.Extension.ToLowerInvariant()
  } | Sort-Object Name | ForEach-Object { $_.FullName })
}

$mediaParts = Get-MediaParts $media

$report = [ordered]@{
  ok = $true
  media = $media
  media_is_directory = (Get-Item -LiteralPath $media).PSIsContainer
  media_parts = $mediaParts
  media_parts_count = $mediaParts.Count
  base_dir = $base
  media_msys = $mediaUnix
  git_bash = $gitBash
  ffmpeg = $ffmpeg
  node = $node
  python = $python
  python3_shim = $pythonShim
  curl = $curl
  engine = $Engine
  upload_started = $false
}

& $ffmpeg -version 2>$null | Select-Object -First 1 | Out-Null
& $gitBash --version | Select-Object -First 1 | Out-Null
if ($CheckOnly) {
  $report | ConvertTo-Json -Depth 3
  exit 0
}

$engineArg = "--$Engine"
& $gitBash $runnerUnix $mediaUnix $baseUnix $engineArg
if ($LASTEXITCODE -ne 0) { throw "Transcription pipeline failed with exit code $LASTEXITCODE" }
