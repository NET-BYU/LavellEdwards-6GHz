"""
14_game_beacon_count_by_folder.py

Compute average beacon count per snapshot for all game folders,
listed one folder per row.

Scope: snapshots where campaign_type = 'game'
Output: outputs/14_game_beacon_count_by_folder.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import output_to, header, subheader, print_table, fmt

DB_DEFAULT = "stadium.duckdb"
OUT_FILE = Path("outputs/14_game_beacon_count_by_folder.txt")

QUERY = """
WITH per_snapshot AS (
    SELECT
        s.folder_name,
        s.snapshot_id,
        COUNT(w.id) AS beacon_count
    FROM snapshots s
    LEFT JOIN wifi_beacons w USING (snapshot_id)
    WHERE s.campaign_type = 'game'
    GROUP BY s.folder_name, s.snapshot_id
)
SELECT
    folder_name,
    COUNT(*) AS snapshots,
    AVG(beacon_count) AS mean_beacons,
    MEDIAN(beacon_count) AS median_beacons,
    STDDEV_SAMP(beacon_count) AS std_beacons,
    MIN(beacon_count) AS min_beacons,
    MAX(beacon_count) AS max_beacons,
    SUM(beacon_count) AS total_beacons
FROM per_snapshot
GROUP BY folder_name
ORDER BY folder_name
"""


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(QUERY).fetchall()
    con.close()

    header("SECTION 14 — AVERAGE BEACON COUNT BY GAME FOLDER")
    print("  Scope: campaign_type='game'")
    print("  Metric: beacon count per snapshot, aggregated by folder")

    if not rows:
        print("\n  No game-folder data found.")
        return

    subheader("14.1  Folder-by-folder beacon count")
    table_rows = []
    for folder, snapshots, mean_b, med_b, std_b, min_b, max_b, total_b in rows:
        table_rows.append([
            folder,
            f"{int(snapshots):,}",
            fmt(mean_b, 2),
            fmt(med_b, 2),
            fmt(std_b, 2),
            int(min_b),
            int(max_b),
            f"{int(total_b):,}",
        ])

    print()
    print_table(
        ["Folder", "Snapshots", "Avg/snapshot", "Median", "Std", "Min", "Max", "Total beacons"],
        table_rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Average beacon count by game folder")
    parser.add_argument("--db", default=DB_DEFAULT, help=f"DuckDB path (default: {DB_DEFAULT})")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with output_to(OUT_FILE):
        run(db_path)

    print(f"\nOutput written to: {OUT_FILE}")


if __name__ == "__main__":
    main()
