"""
query_db.py

Interactive exploration tool for the stadium measurement DuckDB database.

Usage:
  python query_db.py                        # Print schema and run all examples
  python query_db.py --schema               # Print table schema only
  python query_db.py --examples             # Run all example queries
  python query_db.py --query "SELECT ..."   # Run a custom SQL query
  python query_db.py --interactive          # Drop into an interactive SQL prompt
  python query_db.py --db path/to/db.duckdb # Use a different database file
"""

import argparse
import sys
from pathlib import Path

import duckdb

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

W = 80


def header(title: str) -> None:
    print()
    print("=" * W)
    print(f"  {title}")
    print("=" * W)


def subheader(title: str) -> None:
    print()
    print(f"--- {title} " + "-" * max(0, W - 5 - len(title)))


def print_results(rel, max_rows: int = 40) -> None:
    """Print a DuckDB relation as a formatted table."""
    rows = rel.fetchall()
    cols = [d[0] for d in rel.description]

    if not rows:
        print("  (no results)")
        return

    # Calculate column widths
    widths = [len(c) for c in cols]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(
                str(val) if val is not None else "NULL"))

    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    sep = "  " + "  ".join("-" * w for w in widths)

    print(fmt.format(*cols))
    print(sep)
    for i, row in enumerate(rows):
        if i == max_rows:
            print(f"  ... ({len(rows) - max_rows} more rows)")
            break
        print(fmt.format(*[str(v) if v is not None else "NULL" for v in row]))
    print(f"\n  {len(rows)} row(s)")


# ---------------------------------------------------------------------------
# Schema display
# ---------------------------------------------------------------------------

TABLES = [
    ("snapshots",      "One row per JSON file. The root record tying everything together."),
    ("wifi_beacons",   "One row per Wi-Fi access point visible in each snapshot."),
    ("cell_info",      "One row per LTE/4G cell tower visible in each snapshot."),
    ("nr_info",        "One row per 5G NR cell tower visible in each snapshot."),
    ("iperf_results",  "One row per iperf throughput measurement interval."),
    ("gps_satellites", "One row per GPS/GNSS satellite observation in each snapshot."),
    ("http_info",      "One row per snapshot's HTTP download measurement."),
]


def show_schema(con: duckdb.DuckDBPyConnection) -> None:
    header("DATABASE SCHEMA")

    for table, description in TABLES:
        subheader(f"{table}  --  {description}")
        cols = con.execute(f"""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = '{table}'
            ORDER BY ordinal_position
        """).fetchall()
        print(f"  {'Column':<30} {'Type':<15} Nullable")
        print(f"  {'-'*30} {'-'*15} --------")
        for col_name, dtype, nullable in cols:
            print(f"  {col_name:<30} {dtype:<15} {nullable}")

    header("TABLE RELATIONSHIPS")
    print("""
  snapshots (snapshot_id)
      |
      +-- wifi_beacons    (snapshot_id -> snapshots.snapshot_id)
      +-- cell_info       (snapshot_id -> snapshots.snapshot_id)
      +-- nr_info         (snapshot_id -> snapshots.snapshot_id)
      +-- iperf_results   (snapshot_id -> snapshots.snapshot_id)
      +-- gps_satellites  (snapshot_id -> snapshots.snapshot_id)
      +-- http_info       (snapshot_id -> snapshots.snapshot_id)

  Key columns for filtering:
    snapshots.campaign_type  TEXT    'game' or 'iperf'
    snapshots.game_num       INTEGER  1, 2, ...
    snapshots.round_num      INTEGER  1, 2, ...
    snapshots.section        TEXT    '1'..'6', 'N', 'S', 'E', 'W', or NULL for iperf
    snapshots.uuid           TEXT    unique device identifier
    snapshots.device_name    TEXT    e.g. 'Pixel 6', 'r12s'
    snapshots.datetime_iso   TEXT    ISO 8601 timestamp string (cast to TIMESTAMPTZ)

    wifi_beacons.band        TEXT    '2.4GHz', '5GHz', or '6GHz'
    wifi_beacons.primary_freq INTEGER frequency in MHz
    wifi_beacons.rssi        INTEGER signal strength in dBm (negative; closer to 0 = stronger)
    wifi_beacons.sta_count   INTEGER connected station count (-1 = unreported)
    wifi_beacons.ch_util     DOUBLE  channel utilization 0.0-1.0 (-1 = unreported)
    wifi_beacons.standard    TEXT    '11n', '11ac', '11ax', '11be', ...
    wifi_beacons.connected   BOOLEAN whether the device was connected to this AP

    cell_info.status         TEXT    'primary' or 'secondary'
    cell_info.registered     BOOLEAN whether device is registered on this cell

    iperf_results.direction  TEXT    'download' or 'upload'
    iperf_results.tput_mbps  DOUBLE  throughput in Megabits per second
    iperf_results.protocol   TEXT    'TCP' or 'UDP'
    """)


# ---------------------------------------------------------------------------
# Example queries
# ---------------------------------------------------------------------------

EXAMPLES = [
    (
        "Dataset overview: snapshot counts by campaign",
        """
        SELECT campaign_type,
               game_num,
               round_num,
               section,
               COUNT(*)                AS snapshots,
               COUNT(DISTINCT uuid)    AS devices,
               MIN(datetime_iso)       AS first_ts,
               MAX(datetime_iso)       AS last_ts
        FROM snapshots
        GROUP BY campaign_type, game_num, round_num, section
        ORDER BY campaign_type, game_num, round_num, section
        """
    ),
    (
        "Devices in the dataset",
        """
        SELECT uuid,
               device_name,
               COUNT(DISTINCT folder_name)  AS campaigns,
               COUNT(*)                     AS total_snapshots
        FROM snapshots
        GROUP BY uuid, device_name
        ORDER BY total_snapshots DESC
        """
    ),
    (
        "Wi-Fi beacons seen per snapshot by band (averages per section)",
        """
        SELECT s.section,
               w.band,
               ROUND(AVG(beacon_count), 1)   AS avg_beacons_per_snapshot,
               MAX(beacon_count)             AS max_beacons_per_snapshot
        FROM (
            SELECT snapshot_id, band, COUNT(*) AS beacon_count
            FROM wifi_beacons
            GROUP BY snapshot_id, band
        ) w
        JOIN snapshots s USING (snapshot_id)
        WHERE s.campaign_type = 'game'
        GROUP BY s.section, w.band
        ORDER BY s.section, w.band
        """
    ),
    (
        "Average RSSI by band and section (game snapshots only)",
        """
        SELECT s.section,
               w.band,
               ROUND(AVG(w.rssi), 1)        AS avg_rssi_dbm,
               ROUND(STDDEV(w.rssi), 1)     AS stddev_rssi,
               COUNT(*)                     AS beacon_observations
        FROM wifi_beacons w
        JOIN snapshots s USING (snapshot_id)
        WHERE s.campaign_type = 'game'
        GROUP BY s.section, w.band
        ORDER BY s.section, w.band
        """
    ),
    (
        "Channel utilization distribution: % of beacons in low/med/high utilization buckets",
        """
        SELECT s.section,
               w.band,
               COUNT(*) FILTER (WHERE w.ch_util >= 0 AND w.ch_util < 0.30)  AS low_util,
               COUNT(*) FILTER (WHERE w.ch_util >= 0.30 AND w.ch_util < 0.60) AS med_util,
               COUNT(*) FILTER (WHERE w.ch_util >= 0.60)                     AS high_util,
               COUNT(*) FILTER (WHERE w.ch_util < 0)                         AS unreported
        FROM wifi_beacons w
        JOIN snapshots s USING (snapshot_id)
        WHERE s.campaign_type = 'game'
        GROUP BY s.section, w.band
        ORDER BY s.section, w.band
        """
    ),
    (
        "Average station count per AP by band and section (excluding unreported -1 values)",
        """
        SELECT s.section,
               w.band,
               ROUND(AVG(w.sta_count), 1)   AS avg_sta_count,
               MAX(w.sta_count)             AS max_sta_count,
               COUNT(*)                     AS observations
        FROM wifi_beacons w
        JOIN snapshots s USING (snapshot_id)
        WHERE w.sta_count >= 0
          AND s.campaign_type = 'game'
        GROUP BY s.section, w.band
        ORDER BY s.section, w.band
        """
    ),
    (
        "iperf throughput summary by game and round",
        """
        SELECT s.game_num,
               s.round_num,
               i.direction,
               i.protocol,
               COUNT(*)                         AS intervals,
               ROUND(AVG(i.tput_mbps), 2)       AS avg_tput_mbps,
               ROUND(STDDEV(i.tput_mbps), 2)    AS stddev_mbps,
               ROUND(MIN(i.tput_mbps), 2)       AS min_tput_mbps,
               ROUND(MAX(i.tput_mbps), 2)       AS max_tput_mbps
        FROM iperf_results i
        JOIN snapshots s USING (snapshot_id)
        GROUP BY s.game_num, s.round_num, i.direction, i.protocol
        ORDER BY s.game_num, s.round_num, i.direction
        """
    ),
    (
        "Beacons on 6 GHz band only, with their channel utilization",
        """
        SELECT s.section,
               s.game_num,
               w.bssid,
               w.channel_num,
               w.primary_freq,
               w.rssi,
               w.sta_count,
               ROUND(w.ch_util, 3) AS ch_util,
               w.standard
        FROM wifi_beacons w
        JOIN snapshots s USING (snapshot_id)
        WHERE w.band = '6GHz'
          AND w.ch_util >= 0
        ORDER BY w.ch_util DESC
        LIMIT 20
        """
    ),
    (
        "Timeline: average beacon count per minute for Game 1, Round 1, Section 1",
        """
        SELECT STRFTIME(CAST(datetime_iso AS TIMESTAMPTZ), '%Y-%m-%dT%H:%M') AS minute,
               COUNT(DISTINCT snapshot_id)       AS snapshots,
               ROUND(AVG(beacon_count), 1)       AS avg_beacons
        FROM (
            SELECT s.snapshot_id,
                   s.datetime_iso,
                   COUNT(*) AS beacon_count
            FROM wifi_beacons w
            JOIN snapshots s USING (snapshot_id)
            WHERE s.game_num = 1
              AND s.round_num = 1
              AND s.section = '1'
            GROUP BY s.snapshot_id, s.datetime_iso
        )
        GROUP BY minute
        ORDER BY minute
        LIMIT 20
        """
    ),
    (
        "Connected AP per device: what was each device connected to and when",
        """
        SELECT s.uuid,
               s.device_name,
               s.game_num,
               s.section,
               s.datetime_iso,
               w.bssid,
               w.band,
               w.rssi,
               w.link_speed
        FROM wifi_beacons w
        JOIN snapshots s USING (snapshot_id)
        WHERE w.connected = true
        ORDER BY s.uuid, s.datetime_iso
        LIMIT 20
        """
    ),
    (
        "GPS fix quality over time for a specific device (first device found)",
        """
        SELECT s.datetime_iso,
               s.latitude,
               s.longitude,
               s.hor_acc,
               s.ver_acc,
               s.indoor_outdoor
        FROM snapshots s
        WHERE s.latitude IS NOT NULL
          AND s.uuid = (SELECT uuid FROM snapshots WHERE latitude IS NOT NULL LIMIT 1)
        ORDER BY s.datetime_iso
        LIMIT 20
        """
    ),
]


def run_examples(con: duckdb.DuckDBPyConnection) -> None:
    header("EXAMPLE QUERIES")
    for i, (title, sql) in enumerate(EXAMPLES, 1):
        subheader(f"Example {i}: {title}")
        print(f"  SQL:\n")
        for line in sql.strip().splitlines():
            print(f"    {line}")
        print()
        try:
            rel = con.execute(sql)
            print_results(rel)
        except Exception as exc:
            print(f"  [ERROR] {exc}")


# ---------------------------------------------------------------------------
# Interactive SQL prompt
# ---------------------------------------------------------------------------

def interactive(con: duckdb.DuckDBPyConnection) -> None:
    header("INTERACTIVE SQL PROMPT")
    print("  Type SQL queries and press Enter twice to run.")
    print("  Commands: .schema   .tables   .quit\n")

    while True:
        lines = []
        try:
            while True:
                prompt = "sql> " if not lines else "  -> "
                line = input(prompt)
                if line.strip() in (".quit", ".exit", "quit", "exit"):
                    print("Exiting.")
                    return
                if line.strip() == ".tables":
                    for t, _ in TABLES:
                        print(f"  {t}")
                    lines = []
                    break
                if line.strip() == ".schema":
                    show_schema(con)
                    lines = []
                    break
                lines.append(line)
                # Run if the accumulated text ends in a semicolon or is blank after content
                joined = " ".join(lines).strip()
                if joined.endswith(";") or (not line.strip() and joined):
                    sql = joined.rstrip(";")
                    if sql:
                        try:
                            rel = con.execute(sql)
                            print_results(rel)
                        except Exception as exc:
                            print(f"  [ERROR] {exc}")
                    lines = []
                    break
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explore the stadium measurement DuckDB database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python query_db.py                         # Show schema then run all examples
  python query_db.py --schema                # Show table schema only
  python query_db.py --examples              # Run all example queries
  python query_db.py --interactive           # Interactive SQL prompt
  python query_db.py --query "SELECT COUNT(*) FROM wifi_beacons WHERE band = '6GHz'"
        """,
    )
    parser.add_argument("--db", default="stadium.duckdb",
                        help="Path to the DuckDB database file (default: stadium.duckdb)")
    parser.add_argument("--schema", action="store_true",
                        help="Print the table schema and exit")
    parser.add_argument("--examples", action="store_true",
                        help="Run all example queries and exit")
    parser.add_argument("--interactive", action="store_true",
                        help="Start an interactive SQL prompt")
    parser.add_argument("--query", metavar="SQL",
                        help="Run a single SQL query and print results")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database file not found: {db_path}")
        print("Run ingest_to_duckdb.py first to create it.")
        sys.exit(1)

    con = duckdb.connect(str(db_path), read_only=True)

    if args.query:
        try:
            rel = con.execute(args.query)
            print_results(rel)
        except Exception as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    elif args.schema:
        show_schema(con)
    elif args.examples:
        run_examples(con)
    elif args.interactive:
        show_schema(con)
        interactive(con)
    else:
        # Default: show everything
        show_schema(con)
        run_examples(con)

    con.close()


if __name__ == "__main__":
    main()
