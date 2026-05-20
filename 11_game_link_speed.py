"""
11_game_link_speed.py  --  Section 11: Advertised Link Speed Stats (Game Data)

Descriptive statistics for advertised Wi-Fi link speed metrics from game data only.
Uses connected AP records from wifi_beacons joined to snapshots where campaign_type='game'.

Metrics:
  - link_speed (legacy)
  - tx_link_speed
  - rx_link_speed

Outputs:
  - overall pooled stats
  - per-band stats (2.4GHz / 5GHz / 6GHz / other)
  - per-game pooled stats

Output file: outputs/11_game_link_speed.txt

Usage:
  python 11_game_link_speed.py [--db stadium.duckdb]
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import output_to, header, subheader, descriptive, print_table, fmt

DB_DEFAULT = "stadium.duckdb"
OUT_FILE = Path("outputs/11_game_link_speed.txt")

QUERY = """
SELECT
    s.game_num,
    s.section,
    s.uuid,
    s.datetime_iso,
    w.band,
    w.link_speed,
    w.tx_link_speed,
    w.rx_link_speed
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE s.campaign_type = 'game'
  AND w.connected = TRUE
  AND (
      w.link_speed IS NOT NULL
      OR w.tx_link_speed IS NOT NULL
      OR w.rx_link_speed IS NOT NULL
  )
ORDER BY s.game_num, s.section, s.datetime_iso
"""

IPERF_TPUT_QUERY = """
SELECT
        s.game_num,
        s.round_num,
        s.uuid,
        s.datetime_iso,
        i.direction,
        i.protocol,
        i.tput_mbps,
        (
            SELECT w.band
            FROM wifi_beacons w
            WHERE w.snapshot_id = s.snapshot_id
                AND w.connected = TRUE
            LIMIT 1
        ) AS connected_band
FROM iperf_results i
JOIN snapshots s USING (snapshot_id)
WHERE s.campaign_type = 'iperf'
    AND i.tput_mbps IS NOT NULL
    AND i.tput_mbps > 0
ORDER BY s.game_num, s.round_num, s.datetime_iso
"""


def arr_stats(arr: np.ndarray) -> list:
    if len(arr) == 0:
        return [0, *["N/A"] * 9]
    d = descriptive(arr)
    return [
        int(d["n"]),
        fmt(d["mean"], 2),
        fmt(d["median"], 2),
        fmt(d["var"], 2),
        fmt(d["std"], 2),
        fmt(d["iqr"], 2),
        fmt(d["p10"], 2),
        fmt(d["p90"], 2),
        fmt(d["min"], 2),
        fmt(d["max"], 2),
    ]


def normalize_band(band: str | None) -> str:
    if band in ("2.4GHz", "5GHz", "6GHz"):
        return band
    return "other"


def metric_table(title: str, pooled: np.ndarray, by_band: dict, by_game: dict) -> None:
    subheader(title)

    print("\n  Overall and by-band descriptive statistics")
    hdr = ["Group", "N", "Mean", "Median", "Variance", "Std", "IQR", "P10", "P90", "Min", "Max"]
    rows = [["Pooled", *arr_stats(pooled)]]
    for band in ("2.4GHz", "5GHz", "6GHz", "other"):
        rows.append([band, *arr_stats(by_band.get(band, np.array([], dtype=float)))])
    print()
    print_table(hdr, rows)

    subheader(f"{title} by game (pooled across bands)")
    game_rows = []
    for game_key in sorted(by_game.keys(), key=lambda g: (g is None, g)):
        label = f"Game {game_key}" if game_key is not None else "Game N/A"
        game_rows.append([label, *arr_stats(by_game[game_key])])
    if game_rows:
        print()
        print_table(hdr, game_rows)
    else:
        print("\n  No game rows found for this metric.")


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(QUERY).fetchall()
    iperf_rows = con.execute(IPERF_TPUT_QUERY).fetchall()
    con.close()

    if not rows:
        print("No connected game records with link speed metrics were found.")
        return

    link_all = []
    tx_all = []
    rx_all = []

    link_by_band = {"2.4GHz": [], "5GHz": [], "6GHz": [], "other": []}
    tx_by_band = {"2.4GHz": [], "5GHz": [], "6GHz": [], "other": []}
    rx_by_band = {"2.4GHz": [], "5GHz": [], "6GHz": [], "other": []}

    link_by_game = {}
    tx_by_game = {}
    rx_by_game = {}

    unique_snapshots = set()
    unique_devices = set()

    for game_num, section, uuid, dt_iso, band, link, tx, rx in rows:
        b = normalize_band(band)

        unique_devices.add(uuid)
        unique_snapshots.add((game_num, section, uuid, dt_iso))

        if link is not None and link > 0:
            v = float(link)
            link_all.append(v)
            link_by_band[b].append(v)
            link_by_game.setdefault(game_num, []).append(v)

        if tx is not None and tx > 0:
            v = float(tx)
            tx_all.append(v)
            tx_by_band[b].append(v)
            tx_by_game.setdefault(game_num, []).append(v)

        if rx is not None and rx > 0:
            v = float(rx)
            rx_all.append(v)
            rx_by_band[b].append(v)
            rx_by_game.setdefault(game_num, []).append(v)

    link_all = np.array(link_all, dtype=float)
    tx_all = np.array(tx_all, dtype=float)
    rx_all = np.array(rx_all, dtype=float)

    link_by_band_np = {k: np.array(v, dtype=float) for k, v in link_by_band.items()}
    tx_by_band_np = {k: np.array(v, dtype=float) for k, v in tx_by_band.items()}
    rx_by_band_np = {k: np.array(v, dtype=float) for k, v in rx_by_band.items()}

    link_by_game_np = {k: np.array(v, dtype=float) for k, v in link_by_game.items()}
    tx_by_game_np = {k: np.array(v, dtype=float) for k, v in tx_by_game.items()}
    rx_by_game_np = {k: np.array(v, dtype=float) for k, v in rx_by_game.items()}

    header("SECTION 11 — ADVERTISED LINK SPEED (GAME DATA ONLY)")

    subheader("11.0  Data Coverage")
    game_values = sorted({r[0] for r in rows if r[0] is not None})
    game_list = ", ".join(str(g) for g in game_values) if game_values else "N/A"
    print()
    print(f"  Connected records read        : {len(rows)}")
    print(f"  Unique snapshots (approx)     : {len(unique_snapshots)}")
    print(f"  Unique devices (UUID count)   : {len(unique_devices)}")
    print(f"  Games observed                : {game_list}")

    metric_table("11.1  Link Speed (legacy) — Descriptive Statistics", link_all, link_by_band_np, link_by_game_np)
    metric_table("11.2  TX Link Speed — Descriptive Statistics", tx_all, tx_by_band_np, tx_by_game_np)
    metric_table("11.3  RX Link Speed — Descriptive Statistics", rx_all, rx_by_band_np, rx_by_game_np)

    # ------------------------------------------------------------------
    # 11B / second section: iperf throughput descriptive stats
    header("SECTION 11B — IPERF THROUGHPUT (IPERF FOLDERS ONLY)")

    if not iperf_rows:
        print("\n  No iperf throughput rows were found.")
        return

    tput_all = []
    tput_by_band = {"2.4GHz": [], "5GHz": [], "6GHz": [], "other": []}
    tput_by_game = {}
    tput_by_direction = {}

    iperf_snapshots = set()
    iperf_devices = set()
    iperf_games = set()

    for game_num, round_num, uuid, dt_iso, direction, protocol, tput, connected_band in iperf_rows:
        b = normalize_band(connected_band)
        v = float(tput)

        tput_all.append(v)
        tput_by_band[b].append(v)
        tput_by_game.setdefault(game_num, []).append(v)
        tput_by_direction.setdefault(direction or "unknown", []).append(v)

        iperf_snapshots.add((game_num, round_num, uuid, dt_iso))
        iperf_devices.add(uuid)
        if game_num is not None:
            iperf_games.add(game_num)

    tput_all_np = np.array(tput_all, dtype=float)
    tput_by_band_np = {k: np.array(v, dtype=float) for k, v in tput_by_band.items()}
    tput_by_game_np = {k: np.array(v, dtype=float) for k, v in tput_by_game.items()}

    subheader("11B.0  Data Coverage")
    print()
    print(f"  Iperf throughput records read : {len(iperf_rows)}")
    print(f"  Unique snapshots (approx)     : {len(iperf_snapshots)}")
    print(f"  Unique devices (UUID count)   : {len(iperf_devices)}")
    print(f"  Games observed                : {', '.join(str(g) for g in sorted(iperf_games)) if iperf_games else 'N/A'}")

    subheader("11B.1  Throughput (Mbps) — Descriptive Statistics")
    hdr = ["Group", "N", "Mean", "Median", "Variance", "Std", "IQR", "P10", "P90", "Min", "Max"]
    rows_t = [["Pooled", *arr_stats(tput_all_np)]]
    for band in ("2.4GHz", "5GHz", "6GHz", "other"):
        rows_t.append([band, *arr_stats(tput_by_band_np.get(band, np.array([], dtype=float)))])
    print()
    print_table(hdr, rows_t)

    subheader("11B.2  Throughput by game (pooled across bands)")
    game_rows = []
    for game_key in sorted(tput_by_game_np.keys(), key=lambda g: (g is None, g)):
        label = f"Game {game_key}" if game_key is not None else "Game N/A"
        game_rows.append([label, *arr_stats(tput_by_game_np[game_key])])
    if game_rows:
        print()
        print_table(hdr, game_rows)
    else:
        print("\n  No per-game throughput rows found.")

    subheader("11B.3  Throughput by iperf direction")
    dir_rows = []
    for direction in sorted(tput_by_direction.keys()):
        arr = np.array(tput_by_direction[direction], dtype=float)
        dir_rows.append([direction, *arr_stats(arr)])
    if dir_rows:
        print()
        print_table(["Direction", *hdr[1:]], dir_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DB_DEFAULT, help=f"DuckDB path (default: {DB_DEFAULT})")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with output_to(OUT_FILE):
        run(db_path)

    print(f"\nOutput saved to: {OUT_FILE}")


if __name__ == "__main__":
    main()
