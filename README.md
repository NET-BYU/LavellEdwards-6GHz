# Stadium WiFi Measurement Dataset

This repository contains a public dataset of WiFi measurements collected during stadium events. The data was gathered to characterize wireless network performance and behavior in high-density venue environments. This dataset is provided as a resource for researchers studying wireless network performance, crowd-sourced measurements, and venue networking.

## Dataset Description

The dataset consists of time-series WiFi telemetry data collected at regular intervals during multiple stadium events. Each measurement captures WiFi metrics including signal strength, channel utilization, connected device counts, beacon information, and GPS coordinates. The data is organized into folders representing different measurement campaigns across various stadium sections and time periods.

### Data Folder Naming Convention

The data is organized in the `data/` directory with the following naming structure:

**Game Measurement Folders:** `Game{game_number}_{round}_{section}`

- `game_number`: Sequential identifier for the event (1, 2, etc.)
- `round`: Measurement round during the event (1, 2, etc.)
- `section`: Stadium section identifier (1-6 for Game 1, cardinal directions E/N/S/W for Game 2)

Examples:

- `Game1_1_1`: Game 1, Round 1, Section 1
- `Game1_2_3`: Game 1, Round 2, Section 3
- `Game2_1_N`: Game 2, Round 1, North section

**Network Performance Testing Folders:** `iperf{game_number}_{round}`

- `game_number`: Sequential identifier for the event
- `round`: Testing round during the event

Examples:

- `iperf1_1`: Game 1, Round 1 network performance testing
- `iperf2_1`: Game 2, Round 1 network performance testing

### Data Format

Each data folder contains JSON files with timestamps in the filename format `YYYYMMDDTHHMMSSFFFZ-offset.json`. These files contain structured WiFi telemetry data captured at approximately 5-second intervals. The JSON structure includes:

- WiFi interface metrics (signal strength, noise, channel utilization)
- Connected station counts by frequency band (2.4 GHz, 5 GHz, 6 GHz)
- Beacon frame information
- GPS coordinates and accuracy
- Timestamp information

### Compressed Archives

The dataset may be distributed as compressed archives in the `zips/` directory. Two extraction utilities are provided for convenience:

- `extract-zips.sh`: Shell script for Linux and macOS systems
- `extract-zips.ps1`: PowerShell script for Windows systems

These scripts will automatically extract all archives from the `zips/` directory into the `data/` directory while preserving the folder structure.

### Data Processing Utility

The `format_json_files.py` script is provided to validate and reformat JSON files in the dataset. This utility can be used to:

- Validate JSON syntax across the dataset
- Standardize JSON formatting for consistency
- Repair malformed JSON files if needed

This tool is particularly useful when working with the raw measurement data or when integrating the dataset into analysis pipelines.

## License

This dataset is released under the Creative Commons CC0 1.0 Universal (CC0 1.0) Public Domain Dedication. This means the data has been dedicated to the public domain, and you can copy, modify, distribute, and use the data, even for commercial purposes, without asking permission. See the [LICENSE](LICENSE) file for complete legal details.

## Citation

If you use this dataset in your research, please cite the associated paper:

[Citation information to be added upon publication]

## Contact

For questions about the dataset or to report issues, please open an issue in this repository.

## Repository Contents

- `data/`: Measurement data organized by campaign
- `zips/`: Compressed archives of measurement data (if applicable)
- `extract-zips.sh`: Linux/macOS extraction utility
- `extract-zips.ps1`: Windows extraction utility
- `format_json_files.py`: JSON validation and formatting utility
- `LICENSE`: CC0 1.0 Universal Public Domain Dedication
- `README.md`: This file
