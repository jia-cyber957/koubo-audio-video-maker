param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectDir
)

$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $ProjectDir).Path
$server = Join-Path $PSScriptRoot 'server.js'
$urlFile = Join-Path $resolved 'server_url.txt'
if (Test-Path -LiteralPath $urlFile) { Remove-Item -LiteralPath $urlFile -Force }
$node = (Get-Command node -ErrorAction Stop).Source
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
  $known = @(
    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
    "$env:LOCALAPPDATA\Programs\ffmpeg\bin\ffmpeg.exe",
    "$env:ProgramFiles\ffmpeg\bin\ffmpeg.exe"
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
  if ($known) { $env:FFMPEG_PATH = $known }
} else {
  $env:FFMPEG_PATH = $ffmpeg.Source
}
if (-not $env:FFMPEG_PATH) {
  Write-Warning "ffmpeg was not found. Review and FCPXML export can still work, but MP4 export will ask for ffmpeg. Run koubo-audio-video-maker/scripts/install_ffmpeg.ps1 or set FFMPEG_PATH, then restart."
} else {
  Write-Output "FFMPEG_PATH=$env:FFMPEG_PATH"
}
function Quote-Ps([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
$launcher = Join-Path $resolved 'start_assembly_review_server.ps1'
$launcherLines = @(
  '$ErrorActionPreference = ''Stop''',
  "Set-Location -LiteralPath $(Quote-Ps $resolved)"
)
if ($env:FFMPEG_PATH) {
  $launcherLines += "`$env:FFMPEG_PATH = $(Quote-Ps $env:FFMPEG_PATH)"
}
$launcherLines += @(
  "& $(Quote-Ps $node) $(Quote-Ps $server) $(Quote-Ps $resolved) 0",
  'if ($LASTEXITCODE -ne 0) { Read-Host ''Server stopped unexpectedly. Press Enter to close.'' }'
)
[IO.File]::WriteAllText($launcher, ($launcherLines -join "`r`n"), [Text.UTF8Encoding]::new($true))

$hostProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-File',$launcher) -WindowStyle Normal -PassThru
[IO.File]::WriteAllText((Join-Path $resolved '.review_launcher.pid'), [string]$hostProcess.Id, [Text.UTF8Encoding]::new($false))

for ($i = 0; $i -lt 50; $i++) {
  if (Test-Path -LiteralPath $urlFile) {
    $url = (Get-Content -Raw -LiteralPath $urlFile).Trim()
    Write-Output $url
    $browser = New-Object System.Diagnostics.ProcessStartInfo
    $browser.FileName = $url
    $browser.UseShellExecute = $true
    [void][System.Diagnostics.Process]::Start($browser)
    exit 0
  }
  Start-Sleep -Milliseconds 100
}

throw "Review server startup timed out. Run manually: $launcher"
