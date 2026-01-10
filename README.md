# Stadium WiFi Analysis Toolkit

A comprehensive toolkit for processing, analyzing, and visualizing WiFi telemetry collected during stadium measurement campaigns. This repository provides both a **unified analysis system** and legacy individual scripts for analyzing GPS, WiFi metrics, beacon counts, signal strength, channel utilization, and network quality.

## Quick Start

### Prerequisites
- Python 3.8+
- Dependencies:

```bash
pip install -r requirements.txt
```

### Recommended: Unified Analysis System

The new unified system consolidates all analysis capabilities with a consistent interface and publication-ready output:

```bash
# Basic histogram
python unified_analysis.py station-hist --primary Section

# Comparison with custom labels
python unified_analysis.py station-hist --compare Empty --label1 "Occupied" --label2 "Empty"

# 5GHz vs 6GHz comparison
python unified_analysis.py band-comparison --cdf

# WiFi metrics time series
python unified_analysis.py wifi-metrics --smooth

# GPS map
python unified_analysis.py gps-map --primary Section
```

**📖 See [UNIFIED_ANALYSIS_DOCS.md](UNIFIED_ANALYSIS_DOCS.md) for complete documentation.**

### Legacy Scripts (Individual Tools)

For specific one-off analyses, individual scripts are still available:

```bash
# Extract archives
./extract-zips.sh  # or extract-zips.ps1 on Windows

# Process GPS and create maps
python batch_process_gps.py all --map

# WiFi metrics analysis
python analyze_wifi_metrics.py --all
python plot_wifi_metrics.py --combined --avg

# Station count analysis
python plot_station_count_histogram.py --compare Empty
python plot_station_count_5g_vs_6g.py --cdf

# Beacon analysis
python count_beacons.py --all
python plot_beacons.py --combined
```

---

## Project Structure

### New Unified System
```
unified_analysis.py         # Main unified analysis script
analysis_config.py           # Configuration file (colors, sizes, labels, etc.)
UNIFIED_ANALYSIS_DOCS.md    # Complete documentation
```

### Input/Output Directories
```
inputs/                     # Place all section folders here (e.g., 1Section1, 1Empty1)
plots/                      # Generated plots
gps_data/                   # GPS exports
processed_data/             # Processed metrics
```

### Legacy Scripts
```
plot_*.py                   # Individual plotting scripts
analyze_*.py                # Analysis scripts
batch_*.py                  # Batch processing tools
```

### Documentation
```
README.md                   # This file
UNIFIED_ANALYSIS_DOCS.md   # Unified system docs
QUICKSTART.md              # Legacy quick start
STRUCTURE.md               # Data format reference
METRICS.md                 # Metrics definitions
WIFI_METRICS.md            # WiFi-specific details
```

---

## Key Features

### Unified Analysis System

✅ **Single Tool, All Capabilities**
- Station count histograms and CDFs
- Beacon count analysis
- 5GHz vs 6GHz band comparison
- WiFi metrics time series (RSSI, channel utilization)
- WiFi metrics CDFs
- GPS tracking maps

✅ **Fully Configurable**
- Edit `analysis_config.py` to customize:
  - Figure sizes (IEEE paper format by default)
  - Colors and line styles
  - Axis labels and legends
  - Statistical options (outlier removal, smoothing)
  - All styling parameters

✅ **Publication Ready**
- IEEE paper format sizing (3.5" single column, 7" double column)
- No titles or statistics by default (configurable)
- High-resolution output (300+ DPI)
- Consistent styling across all plots

✅ **Comparison Mode**
- Side-by-side comparisons with `--compare` flag
- Custom labels with `--label1` and `--label2`
- Automatic outlier removal (95th percentile)

---

## Available Analysis Types

### Currently Implemented

| Analysis Type | Description | Example |
|--------------|-------------|---------|
| `station-hist` | Station count histogram | `python unified_analysis.py station-hist` |
| `station-cdf` | Station count CDF | `python unified_analysis.py station-cdf` |
| `beacon-hist` | Beacon count histogram | `python unified_analysis.py beacon-hist` |
| `beacon-cdf` | Beacon count CDF | `python unified_analysis.py beacon-cdf` |
| `band-comparison` | 5GHz vs 6GHz comparison | `python unified_analysis.py band-comparison --cdf` |
| `wifi-metrics` | RSSI & channel util over time | `python unified_analysis.py wifi-metrics --smooth` |
| `wifi-cdf` | WiFi metrics CDFs | `python unified_analysis.py wifi-cdf` |
| `gps-map` | GPS tracking map | `python unified_analysis.py gps-map` |

### Planned
- `wifi-quality` - Quality metrics over time
- `wifi-violin` - Violin plot comparisons
- `heatmap` - Geographic heatmaps
- `battery` - Battery vs beacons analysis

---

## Data Format

### JSON Structure

Each telemetry file is timestamped JSON:
```
YYYYMMDDTHHMMSSMSZ-TIMEZONE.json
```

Key fields:
```json
{
  "timestamp": "2025-01-15T10:30:45.123Z",
  "location": {
    "latitude": 37.7749,
    "longitude": -122.4194,
    "altitude": 10.5
  },
  "wifi_info": [
    {
      "bssid": "hashed_value",
      "rssi": -65,
      "primaryFreq": 5180,
      "chUtil": 0.35,
      "staCount": 12,
      "standard": "11ax"
    }
  ]
}
```

### Frequency Bands
- **2.4 GHz**: 2400-2500 MHz
- **5 GHz**: 5150-5875 MHz  
- **6 GHz**: 5925-7125 MHz (WiFi 6E/7)

---

## Metrics Reference

### RSSI (Signal Strength)
- **Units**: dBm
- **Ranges**:
  - Excellent: ≥ -67 dBm
  - Good: -67 to -70 dBm
  - Fair: -70 to -80 dBm
  - Weak: < -80 dBm

### Channel Utilization
- **Units**: 0.0-1.0 (or 0-100%)
- **Interpretation**:
  - Low: < 0.30 (< 30%)
  - Medium: 0.30-0.60 (30-60%)
  - High: > 0.60 (> 60%)

### Station Count
- Number of connected clients per AP
- **Threshold**: > 50 clients indicates potential overload
- `-1` indicates unknown value

### Beacon Count
- Number of unique visible access points
- Indicates AP density in the area

---

## Customization

### Using the Configuration File

Edit `analysis_config.py` to customize all aspects:

```python
# Change colors
PRIMARY_COLORS = {
    'histogram': 'steelblue',  # Change to any matplotlib color
    'line': 'blue',
    'cdf': 'steelblue',
}

# Adjust figure sizes
FIGSIZE = {
    'single_histogram': (3.5, 2.5),
    'comparison_histogram': (7.0, 2.5),
}

# Enable/disable features
STATISTICS_CONFIG = {
    'show_mean': False,
    'show_median': False,
    'remove_outliers': True,
    'outlier_percentile': 95,
}

# Change axis labels
AXIS_LABELS = {
    'station_count': 'Number of Clients',
    'frequency_count': 'Frequency',
}
```

### Directory Structure

```
inputs/
  1Section1/
    data/
      *.json
  1Section2/
    data/
      *.json
  1Empty1/
    data/
      *.json
```

Run analysis:
```bash
python unified_analysis.py station-hist --primary Section --compare Empty
```

Output:
```
plots/
  Section_vs_Empty_station_histogram.png
```

---

## Advanced Usage

### Batch Processing

```bash
# Generate multiple analyses
for analysis in station-hist station-cdf beacon-hist wifi-cdf; do
    python unified_analysis.py $analysis --primary Section --output results/
done
```

### Custom Output Directory

```bash
python unified_analysis.py station-hist --output figures/paper/
```

### Smoothing Time Series

```bash
python unified_analysis.py wifi-metrics --smooth
```

### Custom Labels

```bash
python unified_analysis.py station-hist --compare Empty \
    --label1 "Game Day - Occupied" \
    --label2 "Test Day - Empty"
```

---

## Documentation Files

- **[UNIFIED_ANALYSIS_DOCS.md](UNIFIED_ANALYSIS_DOCS.md)** - Complete unified system reference
- **[QUICKSTART.md](QUICKSTART.md)** - Quick start for legacy scripts
- **[STRUCTURE.md](STRUCTURE.md)** - JSON data structure details
- **[METRICS.md](METRICS.md)** - Metrics definitions
- **[WIFI_METRICS.md](WIFI_METRICS.md)** - WiFi-specific field descriptions
- **[QUALITY_METRICS.md](QUALITY_METRICS.md)** - Quality metrics and CQI
- **[IPERF_METRICS.md](IPERF_METRICS.md)** - iPerf throughput analysis

---

## Common Analysis Workflows

### Workflow 1: Basic Comparison
```bash
# Compare occupied vs empty stadium
python unified_analysis.py station-hist --primary Section --compare Empty
python unified_analysis.py beacon-hist --primary Section --compare Empty
python unified_analysis.py band-comparison --primary Section
```

### Workflow 2: Publication Figures
```bash
# Edit analysis_config.py:
# - SHOW_TITLES = False
# - PLOT_CONFIG['dpi'] = 600

python unified_analysis.py station-cdf --compare Empty \
    --label1 "Occupied" --label2 "Empty"
python unified_analysis.py band-comparison --cdf
python unified_analysis.py wifi-metrics --smooth
```

### Workflow 3: Comprehensive Analysis
```bash
# Station analysis
python unified_analysis.py station-hist --primary Section
python unified_analysis.py station-cdf --primary Section

# Band comparison
python unified_analysis.py band-comparison --cdf

# WiFi metrics
python unified_analysis.py wifi-metrics --smooth
python unified_analysis.py wifi-cdf

# GPS
python unified_analysis.py gps-map
```

---

## Troubleshooting

### "No folders found with keyword 'Section'"
**Solution**: Ensure folders are in the `inputs/` directory and contain the keyword in their name.

### "No station count data found"
**Solution**: Verify JSON files exist in `data/` subfolders within section folders.

### Remote/Headless Environment Errors
All scripts use the non-interactive `Agg` backend automatically - no display required.

### Figure Size Issues
Adjust sizes in `analysis_config.py` under the `FIGSIZE` dictionary.

---

## Contributing

The unified system is designed to be extensible. To add a new analysis type:

1. Add extraction function in the data extraction section
2. Add plotting function following existing patterns
3. Register in the `analysis_map` dictionary in `main()`
4. Update documentation

---