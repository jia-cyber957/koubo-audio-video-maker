param(
    [string]$PexelsApiKey = "",
    [string]$PixabayApiKey = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PexelsApiKey)) {
    $PexelsApiKey = Read-Host "Enter Pexels API Key"
}

if ([string]::IsNullOrWhiteSpace($PixabayApiKey)) {
    $PixabayApiKey = Read-Host "Enter Pixabay API Key"
}

if ([string]::IsNullOrWhiteSpace($PexelsApiKey) -or [string]::IsNullOrWhiteSpace($PixabayApiKey)) {
    throw "Both Pexels and Pixabay API keys are required."
}

[Environment]::SetEnvironmentVariable("PEXELS_API_KEY", $PexelsApiKey, "User")
[Environment]::SetEnvironmentVariable("PIXABAY_API_KEY", $PixabayApiKey, "User")

$env:PEXELS_API_KEY = $PexelsApiKey
$env:PIXABAY_API_KEY = $PixabayApiKey

Write-Host "Saved Windows user environment variables: PEXELS_API_KEY and PIXABAY_API_KEY"
Write-Host "They are active in this PowerShell window. New PowerShell/Codex terminals can read them too."
Write-Host "This config is for koubo-video-assembler only. VOLCENGINE_API_KEY stays managed by koubo-audio-video-maker."
