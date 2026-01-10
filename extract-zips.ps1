# Script to extract all zip files into corresponding folders
# and move the zip files to keep them out of the way

# Get all zip files in the current directory
$zipFiles = Get-ChildItem -Path . -Filter "*.zip"

if ($zipFiles.Count -eq 0) {
    Write-Host "No zip files found in the current directory."
    exit
}

Write-Host "Found $($zipFiles.Count) zip file(s) to process..."

# Create an archives folder to store the original zips
$archiveFolder = ".\archives"
if (-not (Test-Path $archiveFolder)) {
    New-Item -ItemType Directory -Path $archiveFolder | Out-Null
    Write-Host "Created 'archives' folder for storing zip files."
}

# Process each zip file
foreach ($zip in $zipFiles) {
    # Get the name without extension for the folder name
    $folderName = [System.IO.Path]::GetFileNameWithoutExtension($zip.Name)
    $destinationPath = ".\$folderName"
    
    Write-Host "`nProcessing: $($zip.Name)"
    
    # Create the destination folder if it doesn't exist
    if (-not (Test-Path $destinationPath)) {
        New-Item -ItemType Directory -Path $destinationPath | Out-Null
        Write-Host "  Created folder: $folderName"
    } else {
        Write-Host "  Folder already exists: $folderName"
    }
    
    # Extract the zip file
    try {
        Expand-Archive -Path $zip.FullName -DestinationPath $destinationPath -Force
        Write-Host "  Extracted successfully to: $folderName"
        
        # Move the zip file to archives folder
        Move-Item -Path $zip.FullName -Destination $archiveFolder -Force
        Write-Host "  Moved zip to: archives\$($zip.Name)"
    }
    catch {
        Write-Host "  ERROR: Failed to extract $($zip.Name): $_" -ForegroundColor Red
    }
}

Write-Host "`nAll done! Zip files have been moved to the 'archives' folder."

# Format extracted .txt files to .json
Write-Host "`nFormatting extracted files to JSON..."
if (Test-Path ".\format_json_files.py") {
    try {
        python3 .\format_json_files.py
    }
    catch {
        Write-Host "  WARNING: Could not run format_json_files.py" -ForegroundColor Yellow
    }
} else {
    Write-Host "  WARNING: format_json_files.py not found" -ForegroundColor Yellow
}
