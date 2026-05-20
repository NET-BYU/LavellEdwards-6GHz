#!/usr/bin/env pwsh
# Script to extract zip files from the zips/ folder into the data/ folder
#
# Usage: ./extra-zips.ps1 [KEYWORD]
#
# This script will:
# 1. Find all *.zip files in the zips/ folder (optionally filtered by KEYWORD)
# 2. Create a folder under data/ for each zip (named after the zip file)
# 3. Extract the contents into that folder
# 4. Run format_json_files.py only after successful extractions

param(
    [Parameter(Position = 0)]
    [string]$Keyword = ""
)

$ErrorActionPreference = "Stop"

$ZipsDir = ".\zips"
$DataDir = ".\data"

# Use the venv python if available, otherwise fall back to python
$VenvPython = ".\.venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

if (-not (Test-Path $ZipsDir -PathType Container)) {
    Write-Host "ERROR: zips/ folder not found." -ForegroundColor Red
    exit 1
}

# Collect matching zip files
$AllZips = @(Get-ChildItem -Path $ZipsDir -Filter "*.zip" -File)

if (-not [string]::IsNullOrEmpty($Keyword)) {
    $ZipFiles = @($AllZips | Where-Object { $_.Name -like "*$Keyword*" })
    Write-Host "Filtering for keyword: '$Keyword'"
} else {
    $ZipFiles = $AllZips
}

if ($ZipFiles.Count -eq 0) {
    if (-not [string]::IsNullOrEmpty($Keyword)) {
        Write-Host "No zip files matching '$Keyword' found in zips/."
    } else {
        Write-Host "No zip files found in zips/."
    }
    exit 0
}

Write-Host "Found $($ZipFiles.Count) zip file(s) to process..."

# Ensure data/ folder exists
if (-not (Test-Path $DataDir -PathType Container)) {
    New-Item -Path $DataDir -ItemType Directory | Out-Null
}

# Track which folder names were successfully extracted this run
$NewlyExtracted = @()

foreach ($ZipFile in $ZipFiles) {
    $Base = $ZipFile.Name
    $FolderName = [System.IO.Path]::GetFileNameWithoutExtension($Base)
    $DestinationPath = Join-Path $DataDir $FolderName

    Write-Host ""
    Write-Host "Processing: $Base"

    if (-not (Test-Path $DestinationPath -PathType Container)) {
        New-Item -Path $DestinationPath -ItemType Directory | Out-Null
        Write-Host "  Created folder: data/$FolderName"
    } else {
        Write-Host "  Folder already exists: data/$FolderName"
    }

    try {
        Expand-Archive -Path $ZipFile.FullName -DestinationPath $DestinationPath -Force
        Write-Host "  Extracted successfully to: data/$FolderName" -ForegroundColor Green
        $NewlyExtracted += $FolderName
    } catch {
        Write-Host "  ERROR: Failed to extract $Base" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "All done!"

if ($NewlyExtracted.Count -eq 0) {
    Write-Host "No new folders extracted; skipping formatter."
    exit 0
}

Write-Host ""
Write-Host "Formatting extracted files to JSON..."
if (Test-Path ".\format_json_files.py" -PathType Leaf) {
    try {
        if (-not [string]::IsNullOrEmpty($Keyword)) {
            & $Python "format_json_files.py" "--name" $Keyword
        } else {
            & $Python "format_json_files.py"
        }

        if ($LASTEXITCODE -eq 0) {
            Write-Host "JSON formatting complete!" -ForegroundColor Green
        } else {
            Write-Host "WARNING: Could not run format_json_files.py" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "WARNING: Could not run format_json_files.py" -ForegroundColor Yellow
    }
} else {
    Write-Host "WARNING: format_json_files.py not found" -ForegroundColor Yellow
}
