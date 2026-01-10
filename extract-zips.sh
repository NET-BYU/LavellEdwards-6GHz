#!/bin/bash
# Script to extract all zip files into corresponding folders
# and move the zip files to keep them out of the way
#
# Usage: ./extract-zips.sh
#
# This script will:
# 1. Find all *.zip files in the current directory
# 2. Create a folder for each zip (named after the zip file)
# 3. Extract the contents into that folder
# 4. Move the zip file to an 'archives' folder

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Count zip files in current directory
shopt -s nullglob
zip_files=(*.zip)
shopt -u nullglob

if [ ${#zip_files[@]} -eq 0 ]; then
    echo "No zip files found in the current directory."
    exit 0
fi

echo "Found ${#zip_files[@]} zip file(s) to process..."

# Create archives folder if it doesn't exist
archive_folder="./archives"
if [ ! -d "$archive_folder" ]; then
    mkdir -p "$archive_folder"
    echo "Created 'archives' folder for storing zip files."
fi

# Process each zip file
for zip_file in "${zip_files[@]}"; do
    # Get the filename without extension
    folder_name="${zip_file%.zip}"
    destination_path="./$folder_name"
    
    echo ""
    echo "Processing: $zip_file"
    
    # Create the destination folder if it doesn't exist
    if [ ! -d "$destination_path" ]; then
        mkdir -p "$destination_path"
        echo "  Created folder: $folder_name"
    else
        echo "  Folder already exists: $folder_name"
    fi
    
    # Extract the zip file
    if unzip -q -o "$zip_file" -d "$destination_path"; then
        echo -e "  ${GREEN}Extracted successfully to: $folder_name${NC}"
        
        # Move the zip file to archives folder
        if mv "$zip_file" "$archive_folder/"; then
            echo "  Moved zip to: archives/$zip_file"
        else
            echo -e "  ${YELLOW}WARNING: Could not move $zip_file to archives${NC}"
        fi
    else
        echo -e "  ${RED}ERROR: Failed to extract $zip_file${NC}"
    fi
done

echo ""
echo "All done! Zip files have been moved to the 'archives' folder."

# Format extracted .txt files to .json
echo ""
echo "Formatting extracted files to JSON..."
if [ -f "./format_json_files.py" ]; then
    if python3 format_json_files.py; then
        echo -e "${GREEN}JSON formatting complete!${NC}"
    else
        echo -e "${YELLOW}WARNING: Could not run format_json_files.py${NC}"
    fi
else
    echo -e "${YELLOW}WARNING: format_json_files.py not found${NC}"
fi
