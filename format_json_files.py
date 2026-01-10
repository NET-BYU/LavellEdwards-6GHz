#!/usr/bin/env python3
"""
Script to rename .txt files to .json and format them for readability
Usage: python3 format_json_files.py [--name <keyword>]

Options:
  --name <keyword>    Use custom folder keyword (default: 'Section')
"""

import json
import os
import sys
from pathlib import Path

# Color codes for output
GREEN = '\033[0;32m'
BLUE = '\033[0;34m'
RED = '\033[0;31m'
NC = '\033[0m'  # No Color

def format_and_rename_files(folder_keyword='Section'):
    """Find all .txt files in section folders and convert them to formatted JSON."""
    
    print(f"{BLUE}Starting JSON file renaming and formatting...{NC}")
    print(f"{BLUE}Using folder keyword: {folder_keyword}{NC}")
    
    # Statistics
    total_files = 0
    successful_files = 0
    failed_files = 0
    
    # Get the current directory
    root_dir = Path.cwd()
    
    # Find all directories matching the section pattern
    for section_dir in sorted(root_dir.iterdir()):
        if section_dir.is_dir() and folder_keyword in section_dir.name:
            data_dir = section_dir / "data"
            
            # Check if data directory exists
            if data_dir.exists() and data_dir.is_dir():
                print(f"\n{GREEN}Processing: {section_dir.name}{NC}")
                
                # Find all .txt files
                txt_files = sorted(data_dir.glob("*.txt"))
                
                for txt_file in txt_files:
                    total_files += 1
                    json_file = txt_file.with_suffix('.json')
                    
                    try:
                        # Read and parse JSON
                        with open(txt_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        # Write formatted JSON
                        with open(json_file, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                        
                        # Remove original .txt file
                        txt_file.unlink()
                        
                        print(f"  ✓ Formatted and renamed: {txt_file.name} → {json_file.name}")
                        successful_files += 1
                        
                    except json.JSONDecodeError as e:
                        print(f"  {RED}✗ Failed to format: {txt_file.name} (invalid JSON: {e}){NC}")
                        failed_files += 1
                    except Exception as e:
                        print(f"  {RED}✗ Error processing: {txt_file.name} ({e}){NC}")
                        failed_files += 1
    
    # Print summary
    print(f"\n{BLUE}================================{NC}")
    print(f"{BLUE}Summary:{NC}")
    print(f"  Total files processed: {total_files}")
    print(f"  {GREEN}Successfully formatted: {successful_files}{NC}")
    if failed_files > 0:
        print(f"  {RED}Failed: {failed_files}{NC}")
    print(f"{BLUE}================================{NC}")
    
    if successful_files > 0:
        print(f"\n{GREEN}All done! Files have been renamed to .json and formatted for readability.{NC}")
    else:
        print(f"\n{RED}No files were processed. Make sure you're running this from the StadiumAnalysis directory.{NC}")

def main():
    # Parse --name flag
    folder_keyword = 'Section'
    if len(sys.argv) > 1:
        if sys.argv[1] in ['-h', '--help']:
            print(__doc__)
            sys.exit(0)
        if sys.argv[1] == '--name':
            if len(sys.argv) < 3:
                print("Error: --name requires a keyword argument")
                sys.exit(1)
            folder_keyword = sys.argv[2]
    
    format_and_rename_files(folder_keyword)

if __name__ == "__main__":
    main()
