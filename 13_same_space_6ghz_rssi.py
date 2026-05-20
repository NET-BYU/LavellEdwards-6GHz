"""
13_same_space_6ghz_rssi.py

Compute per-session average 6 GHz RSSI for a specific set of folders that map
to the same physical space.

Target folders:
- Game1_1_2
- Game1_1_3
- Game1_1_4
- Game1_2_2
- Game1_2_3
- Game1_2_4
- Game2_1_S
- Game2_1_E

Output: outputs/13_same_space_6ghz_rssi.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import output_to, header, subheader, print_table, fmt

DB_DEFAULT = "stadium.duckdb"
OUT_FILE = Path("outputs/13_same_space_6ghz_rssi.txt")

TARGET_FOLDERS = [
    "Game1_1_2",
    "Game1_1_3",
    "Game1_1_4",
    "Game1_2_2",
    "Game1_2_3",
    "Game1_2_4",
    "Game2_1_S",
    "Game2_1_E",
]

QUERY = """
SELECT
    s.folder_name,
    COUNT(w.rssi) AS n_beacons,
    AVG(w.rssi)   AS mean_rssi,
    STDDEV_SAMP(w.rssi) AS std_rssi,
    MIN(w.rssi) AS min_rssi,
    MAX(w.rssi) AS max_rssi
FROM snapshots s
JOIN wifi_beacons w USING (snapshot_id)
WHERE s.folder_name IN ({placeholders})
  AND w.band = '6GHz'
  AND w.rssi IS NOT NULL
GROUP BY s.folder_name
"""


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)

    placeholders = ",".join(["?"] * len(TARGET_FOLDERS))
    q = QUERY.format(placeholders=placeholders)
    rows = con.execute(q, TARGET_FOLDERS).fetchall()

    present = {r[0] for r in rows}
    missing = [f for f in TARGET_FOLDERS if f not in present]

    folder_stats = {
        folder: {
            "n": int(n),
            "mean": float(mean),
            "std": float(std) if std is not None else float("nan"),
            "min": float(min_v),
            "max": float(max_v),
        }
        for folder, n, mean, std, min_v, max_v in rows
    }

    con.close()

    header("SECTION 13 — SAME-SPACE SESSION CHECK (6 GHz RSSI)")
    print("  Metric: mean RSSI (dBm) for 6 GHz beacons")
    print(f"  Requested sessions: {len(TARGET_FOLDERS)}")
    print(f"  Sessions with 6 GHz data: {len(folder_stats)}")

    if missing:
        print("\n  Missing (no 6 GHz RSSI rows found):")
        for name in missing:
            print(f"    {name}")

    subheader("13.1  Average 6 GHz RSSI by Session")
    if not folder_stats:
        print("\n  No data found for the requested folders.")
        return

    table_rows = []
    means = []
    for folder in TARGET_FOLDERS:
        if folder not in folder_stats:
            table_rows.append([folder, 0, "N/A", "N/A", "N/A", "N/A"])
            continue

        s = folder_stats[folder]
        means.append(s["mean"])
        table_rows.append([
            folder,
            f"{s['n']:,}",
            fmt(s["mean"], 2),
            fmt(s["std"], 2),
            fmt(s["min"], 0),
            fmt(s["max"], 0),
        ])

    print()
    print_table(
        ["Folder", "N beacons", "Mean RSSI", "Std", "Min", "Max"],
        table_rows,
    )

    means_arr = np.array(means, dtype=float)
    if len(means_arr) >= 2:
        subheader("13.2  Across-Session Consistency of Means")
        mean_of_means = float(np.mean(means_arr))
        std_of_means = float(np.std(means_arr, ddof=1))
        spread = float(np.max(means_arr) - np.min(means_arr))

        print()
        print(f"  Mean of session means : {mean_of_means:.2f} dBm")
        print(f"  Std of session means  : {std_of_means:.2f} dB")
        print(f"  Range of means        : {np.min(means_arr):.2f} to {np.max(means_arr):.2f} dBm")
        print(f"  Max-min spread        : {spread:.2f} dB")

        if spread <= 2.0:
            verdict = "very consistent"
        elif spread <= 4.0:
            verdict = "moderately consistent"
        else:
            verdict = "meaningfully different"
        print(f"  Quick read            : session means are {verdict} across these same-space folders")


def main() -> None:
    parser = argparse.ArgumentParser(description="Average 6 GHz RSSI by specific same-space sessions")
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
