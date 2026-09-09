# Consolidate the two databases the PROJECT_ROOT bug created.
#
# Between the folder restructure and now, config.py resolved PROJECT_ROOT one
# level too deep, so the app wrote to backend/logs/ instead of logs/. That is
# fixed, but the live data is in the wrong place. This moves it back and keeps
# the old file rather than overwriting it.
#
# Stop the server first — SQLite files can't be moved while it holds them.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$stray  = "backend\logs"
$target = "logs"
$backup = "logs\pre-restructure-backup"

if (-not (Test-Path "$stray\faq_bot.db")) {
    Write-Host "Nothing to move - backend\logs\faq_bot.db is not there." -ForegroundColor Yellow
    exit 0
}

# Keep the older database instead of overwriting it. It has accounts the newer
# one doesn't, and deciding which matters is not this script's call.
if (Test-Path "$target\faq_bot.db") {
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Get-ChildItem "$target\faq_bot.db*" -File | ForEach-Object {
        Move-Item $_.FullName (Join-Path $backup $_.Name) -Force
    }
    Write-Host "Old database moved to $backup" -ForegroundColor Cyan
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
Get-ChildItem "$stray\faq_bot.db*" -File | ForEach-Object {
    Move-Item $_.FullName (Join-Path $target $_.Name) -Force
}
Write-Host "Live database moved to $target\faq_bot.db" -ForegroundColor Green

# Same bug, same fix, for the vector store - only if it was actually created.
if (Test-Path "backend\chroma_data") {
    if (Test-Path "chroma_data") {
        New-Item -ItemType Directory -Force -Path "chroma_data\pre-restructure-backup" | Out-Null
        Write-Host "Both chroma_data dirs exist - left alone, merge by hand." -ForegroundColor Yellow
    } else {
        Move-Item "backend\chroma_data" "chroma_data"
        Write-Host "Vector store moved to chroma_data\" -ForegroundColor Green
    }
}

Remove-Item $stray -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "`nDone. Start the server again." -ForegroundColor Green
