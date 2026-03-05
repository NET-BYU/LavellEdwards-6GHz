"""
ingest_to_duckdb.py

Reads all JSON measurement files from the data/ directory and ingests them into
a DuckDB database (stadium.duckdb) with a normalized relational schema.

Tables created:
  snapshots       - One row per JSON file (device, time, location, battery, etc.)
  wifi_beacons    - One row per beacon visible in wifi_info[]
  cell_info       - One row per cellular entry in cell_info[]
  nr_info         - One row per NR entry in nr_info[]
  iperf_results   - One row per iperf interval in iperf_info[]
  gps_satellites  - One row per satellite in gps_info[]
  http_info       - One row per snapshot's http_info block

Usage:
  python ingest_to_duckdb.py [--data-dir DATA_DIR] [--db DB_PATH] [--reset]

Arguments:
  --data-dir  Path to the data directory (default: ./data)
  --db        Path to the output DuckDB file (default: ./stadium.duckdb)
  --reset     Drop and recreate all tables before ingesting
"""

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path

import duckdb

# ---------------------------------------------------------------------------
# Folder name parsing
# ---------------------------------------------------------------------------


def parse_folder_name(folder_name: str) -> dict:
    """
    Parse a data folder name into its component parts.

    Game folders:  Game{game}_{round}_{section}
                   e.g. Game1_2_3  -> campaign_type='game', game=1, round=2, section='3'
                        Game2_1_N  -> campaign_type='game', game=2, round=1, section='N'

    iperf folders: iperf{game}_{round}
                   e.g. iperf1_2   -> campaign_type='iperf', game=1, round=2, section=None

    empty folders: EMPTY_{device}
                   e.g. EMPTY_GP6  -> campaign_type='empty', section='GP6'
    """
    game_match = re.fullmatch(r'Game(\d+)_(\d+)_(\w+)',
                              folder_name, re.IGNORECASE)
    if game_match:
        return {
            'campaign_type': 'game',
            'game_num': int(game_match.group(1)),
            'round_num': int(game_match.group(2)),
            'section': game_match.group(3),
        }

    iperf_match = re.fullmatch(r'iperf(\d+)_(\d+)', folder_name, re.IGNORECASE)
    if iperf_match:
        return {
            'campaign_type': 'iperf',
            'game_num': int(iperf_match.group(1)),
            'round_num': int(iperf_match.group(2)),
            'section': None,
        }

    empty_match = re.fullmatch(r'EMPTY_(\w+)', folder_name, re.IGNORECASE)
    if empty_match:
        return {
            'campaign_type': 'empty',
            'game_num': None,
            'round_num': None,
            'section': empty_match.group(1),
        }

    return {
        'campaign_type': 'unknown',
        'game_num': None,
        'round_num': None,
        'section': None,
    }


# ---------------------------------------------------------------------------
# JSON loading (handles NaN literals produced by Android app)
# ---------------------------------------------------------------------------

NAN_RE = re.compile(r':\s*NaN\b')


def load_json(path: Path) -> dict:
    """
    Load a JSON file, replacing bare NaN with null to satisfy the JSON spec.
    Returns None if the file cannot be parsed.
    """
    try:
        text = path.read_text(encoding='utf-8')
        text = NAN_RE.sub(': null', text)
        return json.loads(text)
    except Exception as exc:
        print(f"  [WARN] Could not parse {path.name}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe(value, default=None):
    """Return value unless it is None, in which case return default."""
    return default if value is None else value


def band_label(freq_mhz) -> str:
    """Classify a Wi-Fi frequency (MHz) into a human-readable band string."""
    if freq_mhz is None:
        return 'unknown'
    if freq_mhz < 2500:
        return '2.4GHz'
    if freq_mhz < 5925:
        return '5GHz'
    return '6GHz'


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE SEQUENCE IF NOT EXISTS seq_snapshot_id START 1;

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id      INTEGER PRIMARY KEY DEFAULT nextval('seq_snapshot_id'),
    folder_name      TEXT,
    campaign_type    TEXT,
    game_num         INTEGER,
    round_num        INTEGER,
    section          TEXT,
    file_name        TEXT,
    uuid             TEXT,
    device_name      TEXT,
    android_version  INTEGER,
    datetime_iso     TEXT,
    latitude         DOUBLE,
    longitude        DOUBLE,
    altitude         DOUBLE,
    hor_acc          DOUBLE,
    ver_acc          DOUBLE,
    indoor_outdoor   TEXT,
    network_type     TEXT,
    carrier_name     TEXT,
    op_name          TEXT,
    batt_cap_perc    DOUBLE,
    batt_temp_c      DOUBLE,
    batt_voltage_mv  INTEGER,
    batt_status      TEXT
);

CREATE SEQUENCE IF NOT EXISTS seq_wifi_id START 1;

CREATE TABLE IF NOT EXISTS wifi_beacons (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_wifi_id'),
    snapshot_id         INTEGER REFERENCES snapshots(snapshot_id),
    bssid               TEXT,
    primary_freq        INTEGER,
    center_freq0        INTEGER,
    center_freq1        INTEGER,
    width               INTEGER,
    channel_num         INTEGER,
    primary_ch_num      INTEGER,
    rssi                INTEGER,
    standard            TEXT,
    security            TEXT,
    timestamp_ms        BIGINT,
    timestamp_delta_ms  INTEGER,
    connected           BOOLEAN,
    link_speed          INTEGER,
    tx_link_speed       INTEGER,
    rx_link_speed       INTEGER,
    sta_count           INTEGER,
    ch_util             DOUBLE,
    tx_power            INTEGER,
    ap_name             TEXT,
    ap_type_6ghz        TEXT,
    band                TEXT
);

CREATE SEQUENCE IF NOT EXISTS seq_cell_id START 1;

CREATE TABLE IF NOT EXISTS cell_info (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_cell_id'),
    snapshot_id         INTEGER REFERENCES snapshots(snapshot_id),
    timestamp_ms        BIGINT,
    timestamp_delta_ms  INTEGER,
    pci                 INTEGER,
    ci                  BIGINT,
    earfcn              INTEGER,
    band                INTEGER,
    width               INTEGER,
    freq                DOUBLE,
    ss                  INTEGER,
    rsrp                INTEGER,
    rsrq                INTEGER,
    cqi                 INTEGER,
    rssi                INTEGER,
    rssnr               INTEGER,
    status              TEXT,
    registered          BOOLEAN
);

CREATE SEQUENCE IF NOT EXISTS seq_nr_id START 1;

CREATE TABLE IF NOT EXISTS nr_info (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_nr_id'),
    snapshot_id         INTEGER REFERENCES snapshots(snapshot_id),
    timestamp_ms        BIGINT,
    timestamp_delta_ms  INTEGER,
    nci                 BIGINT,
    pci                 INTEGER,
    nr_pci              INTEGER,
    nrarfcn             INTEGER,
    band                TEXT,
    width               INTEGER,
    freq                DOUBLE,
    channel_num         INTEGER,
    csi_rsrp            INTEGER,
    csi_rsrq            INTEGER,
    csi_sinr            INTEGER,
    csi_rssi            INTEGER,
    ss_rsrp             INTEGER,
    ss_rsrq             INTEGER,
    ss_sinr             INTEGER,
    ss_rssi             INTEGER,
    status              TEXT,
    is_signal_str_api   BOOLEAN
);

CREATE SEQUENCE IF NOT EXISTS seq_iperf_id START 1;

CREATE TABLE IF NOT EXISTS iperf_results (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_iperf_id'),
    snapshot_id     INTEGER REFERENCES snapshots(snapshot_id),
    timestamp_ms    BIGINT,
    interval_sec    DOUBLE,
    size_mbytes     DOUBLE,
    tput_mbps       DOUBLE,
    target          TEXT,
    direction       TEXT,
    protocol        TEXT
);

CREATE SEQUENCE IF NOT EXISTS seq_gps_id START 1;

CREATE TABLE IF NOT EXISTS gps_satellites (
    id                    INTEGER PRIMARY KEY DEFAULT nextval('seq_gps_id'),
    snapshot_id           INTEGER REFERENCES snapshots(snapshot_id),
    timestamp_ms          BIGINT,
    svid                  INTEGER,
    constellation_type    TEXT,
    azimuth_degrees       DOUBLE,
    elevation_degrees     DOUBLE,
    carrier_freq_hz       DOUBLE,
    baseband_cn0_db_hz    DOUBLE,
    cn0_db_hz             DOUBLE,
    has_almanac_data      BOOLEAN,
    has_ephemeris_data    BOOLEAN,
    used_in_fix           BOOLEAN,
    delta_timestamp_ms    INTEGER
);

CREATE TABLE IF NOT EXISTS http_info (
    snapshot_id         INTEGER REFERENCES snapshots(snapshot_id),
    target_url          TEXT,
    bytes_downloaded    BIGINT,
    start_time_ms       BIGINT,
    duration_nano       BIGINT
);
"""

RESET_SQL = """
DROP TABLE IF EXISTS http_info;
DROP TABLE IF EXISTS gps_satellites;
DROP TABLE IF EXISTS iperf_results;
DROP TABLE IF EXISTS nr_info;
DROP TABLE IF EXISTS cell_info;
DROP TABLE IF EXISTS wifi_beacons;
DROP TABLE IF EXISTS snapshots;
DROP SEQUENCE IF EXISTS seq_snapshot_id;
DROP SEQUENCE IF EXISTS seq_wifi_id;
DROP SEQUENCE IF EXISTS seq_cell_id;
DROP SEQUENCE IF EXISTS seq_nr_id;
DROP SEQUENCE IF EXISTS seq_iperf_id;
DROP SEQUENCE IF EXISTS seq_gps_id;
"""


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def build_snapshot_row(folder_info: dict, folder_name: str, file_name: str, data: dict) -> tuple:
    loc = data.get('location') or {}
    sensor = data.get('sensor') or {}
    return (
        folder_name,
        folder_info['campaign_type'],
        folder_info['game_num'],
        folder_info['round_num'],
        folder_info['section'],
        file_name,
        data.get('uuid'),
        data.get('deviceName'),
        data.get('androidVersion'),
        data.get('datetimeIso'),
        loc.get('latitude'),
        loc.get('longitude'),
        loc.get('altitude'),
        loc.get('hor_acc'),
        loc.get('ver_acc'),
        data.get('indoorOutdoorPrediction'),
        data.get('networkType'),
        data.get('carrierName'),
        data.get('opName'),
        sensor.get('battCapPerc'),
        sensor.get('battTempC'),
        sensor.get('battVoltageMv'),
        sensor.get('battStatus'),
    )


def build_wifi_rows(snapshot_id: int, wifi_list: list) -> list[tuple]:
    rows = []
    for w in wifi_list:
        freq = w.get('primaryFreq')
        rows.append((
            snapshot_id,
            w.get('bssid'),
            freq,
            w.get('centerFreq0'),
            w.get('centerFreq1'),
            w.get('width'),
            w.get('channelNum'),
            w.get('primaryChNum'),
            w.get('rssi'),
            w.get('standard'),
            w.get('security'),
            w.get('timestampMs'),
            w.get('timestampDeltaMs'),
            w.get('connected'),
            w.get('linkSpeed'),
            w.get('txLinkSpeed'),
            w.get('rxLinkSpeed'),
            w.get('staCount'),
            w.get('chUtil'),
            w.get('txPower'),
            w.get('apName'),
            w.get('6GHzApType'),
            band_label(freq),
        ))
    return rows


def build_cell_rows(snapshot_id: int, cell_list: list) -> list[tuple]:
    rows = []
    for c in cell_list:
        rows.append((
            snapshot_id,
            c.get('timestampMs'),
            c.get('timestampDeltaMs'),
            c.get('pci'),
            c.get('ci'),
            c.get('earfcn'),
            c.get('band'),
            c.get('width'),
            c.get('freq'),
            c.get('ss'),
            c.get('rsrp'),
            c.get('rsrq'),
            c.get('cqi'),
            c.get('rssi'),
            c.get('rssnr'),
            c.get('status'),
            c.get('registered'),
        ))
    return rows


def build_nr_rows(snapshot_id: int, nr_list: list) -> list[tuple]:
    rows = []
    for n in nr_list:
        # band can be a list of integers or a single integer; store as CSV string
        band_raw = n.get('band')
        if isinstance(band_raw, list):
            band_str = ','.join(str(b) for b in band_raw)
        elif band_raw is not None:
            band_str = str(band_raw)
        else:
            band_str = None
        rows.append((
            snapshot_id,
            n.get('timestampMs'),
            n.get('timestampDeltaMs'),
            n.get('nci'),
            n.get('pci'),
            n.get('nrPci'),
            n.get('nrarfcn'),
            band_str,
            n.get('width'),
            n.get('freq'),
            n.get('channelNum'),
            n.get('csiRsrp'),
            n.get('csiRsrq'),
            n.get('csiSinr'),
            n.get('csiRssi'),
            n.get('ssRsrp'),
            n.get('ssRsrq'),
            n.get('ssSinr'),
            n.get('ssRssi'),
            n.get('status'),
            n.get('isSignalStrAPI'),
        ))
    return rows


def build_iperf_rows(snapshot_id: int, iperf_list: list) -> list[tuple]:
    rows = []
    for i in iperf_list:
        rows.append((
            snapshot_id,
            i.get('timestampMs'),
            i.get('intervalSec'),
            i.get('sizeMbytes'),
            i.get('tputMbps'),
            i.get('target'),
            i.get('direction'),
            i.get('protocol'),
        ))
    return rows


def build_gps_rows(snapshot_id: int, gps_list: list) -> list[tuple]:
    rows = []
    for g in gps_list:
        rows.append((
            snapshot_id,
            g.get('timestampMs'),
            g.get('svid'),
            g.get('constellationType'),
            g.get('azimuthDegrees'),
            g.get('elevationDegrees'),
            g.get('carrierFreqHz'),
            g.get('basebandCn0DbHz'),
            g.get('cn0DbHz'),
            g.get('hasAlmanacData'),
            g.get('hasEphemerisData'),
            g.get('usedInFix'),
            g.get('deltaTimestampMs'),
        ))
    return rows


def build_http_row(snapshot_id: int, http: dict) -> tuple | None:
    if not http:
        return None
    return (
        snapshot_id,
        http.get('targetUrl'),
        http.get('bytesDownloaded'),
        http.get('startTimeMs'),
        http.get('durationNano'),
    )


# ---------------------------------------------------------------------------
# Insert helpers (parameterised, batch)
# ---------------------------------------------------------------------------

SNAPSHOT_INSERT = """
INSERT INTO snapshots
  (folder_name, campaign_type, game_num, round_num, section, file_name,
   uuid, device_name, android_version, datetime_iso,
   latitude, longitude, altitude, hor_acc, ver_acc,
   indoor_outdoor, network_type, carrier_name, op_name,
   batt_cap_perc, batt_temp_c, batt_voltage_mv, batt_status)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
RETURNING snapshot_id
"""

WIFI_INSERT = """
INSERT INTO wifi_beacons
  (snapshot_id, bssid, primary_freq, center_freq0, center_freq1,
   width, channel_num, primary_ch_num, rssi, standard, security,
   timestamp_ms, timestamp_delta_ms, connected, link_speed,
   tx_link_speed, rx_link_speed, sta_count, ch_util, tx_power,
   ap_name, ap_type_6ghz, band)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

CELL_INSERT = """
INSERT INTO cell_info
  (snapshot_id, timestamp_ms, timestamp_delta_ms, pci, ci, earfcn,
   band, width, freq, ss, rsrp, rsrq, cqi, rssi, rssnr, status, registered)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

NR_INSERT = """
INSERT INTO nr_info
  (snapshot_id, timestamp_ms, timestamp_delta_ms, nci, pci, nr_pci,
   nrarfcn, band, width, freq, channel_num,
   csi_rsrp, csi_rsrq, csi_sinr, csi_rssi,
   ss_rsrp, ss_rsrq, ss_sinr, ss_rssi, status, is_signal_str_api)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

IPERF_INSERT = """
INSERT INTO iperf_results
  (snapshot_id, timestamp_ms, interval_sec, size_mbytes, tput_mbps,
   target, direction, protocol)
VALUES (?,?,?,?,?,?,?,?)
"""

GPS_INSERT = """
INSERT INTO gps_satellites
  (snapshot_id, timestamp_ms, svid, constellation_type, azimuth_degrees,
   elevation_degrees, carrier_freq_hz, baseband_cn0_db_hz, cn0_db_hz,
   has_almanac_data, has_ephemeris_data, used_in_fix, delta_timestamp_ms)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

HTTP_INSERT = """
INSERT INTO http_info
  (snapshot_id, target_url, bytes_downloaded, start_time_ms, duration_nano)
VALUES (?,?,?,?,?)
"""


# ---------------------------------------------------------------------------
# Main ingestion logic
# ---------------------------------------------------------------------------

def ingest(data_dir: Path, db_path: Path, reset: bool) -> None:
    print(f"Connecting to database: {db_path}")
    con = duckdb.connect(str(db_path))

    if reset:
        print("Resetting database (dropping existing tables)...")
        con.execute(RESET_SQL)

    print("Creating schema...")
    con.execute(SCHEMA_SQL)

    # Find all data folders (immediate children of data_dir that contain a data/ subfolder)
    data_folders = sorted([
        p for p in data_dir.iterdir()
        if p.is_dir() and (p / 'data').is_dir()
    ])

    if not data_folders:
        print(f"No data folders found under {data_dir}")
        return

    total_files = sum(
        len(list((folder / 'data').glob('*.json')))
        for folder in data_folders
    )
    print(
        f"Found {len(data_folders)} campaign folders, {total_files} JSON files total.\n")

    processed = 0
    skipped = 0
    errors = 0

    # Build a set of (folder_name, file_name) already in the database
    already_ingested = set(
        con.execute("SELECT folder_name, file_name FROM snapshots").fetchall()
    )

    for folder in data_folders:
        folder_name = folder.name
        folder_info = parse_folder_name(folder_name)
        json_files = sorted((folder / 'data').glob('*.json'))

        print(f"  [{folder_info['campaign_type'].upper():5}] {folder_name:20}  "
              f"game={folder_info['game_num']}  round={folder_info['round_num']}  "
              f"section={folder_info['section']}  "
              f"files={len(json_files)}")

        for json_file in json_files:
            if (folder_name, json_file.name) in already_ingested:
                skipped += 1
                continue
            data = load_json(json_file)
            if data is None:
                errors += 1
                continue

            try:
                con.begin()

                # --- snapshots ---
                snap_row = build_snapshot_row(
                    folder_info, folder_name, json_file.name, data)
                result = con.execute(SNAPSHOT_INSERT, snap_row).fetchone()
                snapshot_id = result[0]

                # --- wifi_beacons ---
                wifi_rows = build_wifi_rows(
                    snapshot_id, data.get('wifi_info') or [])
                if wifi_rows:
                    con.executemany(WIFI_INSERT, wifi_rows)

                # --- cell_info ---
                cell_rows = build_cell_rows(
                    snapshot_id, data.get('cell_info') or [])
                if cell_rows:
                    con.executemany(CELL_INSERT, cell_rows)

                # --- nr_info ---
                nr_rows = build_nr_rows(snapshot_id, data.get('nr_info') or [])
                if nr_rows:
                    con.executemany(NR_INSERT, nr_rows)

                # --- iperf_results ---
                iperf_rows = build_iperf_rows(
                    snapshot_id, data.get('iperf_info') or [])
                if iperf_rows:
                    con.executemany(IPERF_INSERT, iperf_rows)

                # --- gps_satellites ---
                gps_rows = build_gps_rows(
                    snapshot_id, data.get('gps_info') or [])
                if gps_rows:
                    con.executemany(GPS_INSERT, gps_rows)

                # --- http_info ---
                http_row = build_http_row(snapshot_id, data.get('http_info'))
                if http_row:
                    con.execute(HTTP_INSERT, http_row)

                con.commit()
                processed += 1

            except Exception:
                con.rollback()
                print(f"    [ERROR] Failed on {json_file.name}:")
                traceback.print_exc()
                errors += 1

    con.close()

    print(f"\nDone. {processed} files ingested, {skipped} skipped (already in DB), {errors} errors.")
    print(f"Database written to: {db_path}")


# ---------------------------------------------------------------------------
# Summary query
# ---------------------------------------------------------------------------

def print_summary(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    print("\nDatabase summary:")
    for table in ['snapshots', 'wifi_beacons', 'cell_info', 'nr_info',
                  'iperf_results', 'gps_satellites', 'http_info']:
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:20} {count:>10,} rows")
    print()

    print("Snapshots by campaign:")
    rows = con.execute("""
        SELECT campaign_type, game_num, round_num, section, COUNT(*) AS snapshots
        FROM snapshots
        GROUP BY campaign_type, game_num, round_num, section
        ORDER BY campaign_type, game_num, round_num, section
    """).fetchall()
    for r in rows:
        print(
            f"  {r[0]:6} game={r[1]}  round={r[2]}  section={str(r[3]):4}  {r[4]:>6} snapshots")
    con.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Ingest stadium JSON data into DuckDB.')
    parser.add_argument('--data-dir', default='data',
                        help='Path to the root data directory (default: ./data)')
    parser.add_argument('--db', default='stadium.duckdb',
                        help='Path to output DuckDB file (default: ./stadium.duckdb)')
    parser.add_argument('--reset', action='store_true',
                        help='Drop and recreate all tables before ingesting')
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = Path(args.db)

    if not data_dir.is_dir():
        print(f"Error: data directory not found: {data_dir}")
        sys.exit(1)

    ingest(data_dir, db_path, args.reset)
    print_summary(db_path)


if __name__ == '__main__':
    main()
