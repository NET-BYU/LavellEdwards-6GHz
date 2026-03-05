#!/bin/bash
# Script to extract zip files from the zips/ folder into the data/ folder
#
# Usage: ./extract-zips.sh [KEYWORD]
#
# This script will:
# 1. Find all *.zip files in the zips/ folder (optionally filtered by KEYWORD)
# 2. Create a folder under data/ for each zip (named after the zip file)
# 3. Extract the contents into that folder

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

KEYWORD="${1:-}"
ZIPS_DIR="./zips"
DATA_DIR="./data"

if [ ! -d "$ZIPS_DIR" ]; then
    echo -e "${RED}ERROR: zips/ folder not found.${NC}"
    exit 1
fi

# Collect matching zip files
shopt -s nullglob
all_zips=("$ZIPS_DIR"/*.zip)
shopt -u nullglob

if [ -n "$KEYWORD" ]; then
    zip_files=()
    for f in "${all_zips[@]}"; do
        [[ "$(basename "$f")" == *"$KEYWORD"* ]] && zip_files+=("$f")
    done
    echo "Filtering for keyword: '$KEYWORD'"
else
    zip_files=("${all_zips[@]}")
fi

if [ ${#zip_files[@]} -eq 0 ]; then
    if [ -n "$KEYWORD" ]; then
        echo "No zip files matching '$KEYWORD' found in zips/."
    else
        echo "No zip files found in zips/."
    fi
    exit 0
fi

echo "Found ${#zip_files[@]} zip file(s) to process..."

# Ensure data/ folder exists
mkdir -p "$DATA_DIR"

# Process each zip file
for zip_file in "${zip_files[@]}"; do
    base="$(basename "$zip_file")"
    folder_name="${base%.zip}"
    destination_path="$DATA_DIR/$folder_name"

    echo ""
    echo "Processing: $base"

    # Create the destination folder if it doesn't exist
    if [ ! -d "$destination_path" ]; then
        mkdir -p "$destination_path"
        echo "  Created folder: data/$folder_name"
    else
        echo "  Folder already exists: data/$folder_name"
    fi

    # Extract the zip file
    if unzip -q -o "$zip_file" -d "$destination_path"; then
        echo -e "  ${GREEN}Extracted successfully to: data/$folder_name${NC}"
    else
        echo -e "  ${RED}ERROR: Failed to extract $base${NC}"
    fi
done

echo ""
echo "All done!"

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
