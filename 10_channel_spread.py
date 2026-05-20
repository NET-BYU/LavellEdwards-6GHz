"""
10_channel_spread.py  --  Section 10: Channel Spread of Detected Beacons

Compares how detected unique BSSIDs are distributed across channels in
5 GHz vs 6 GHz.

Outputs, per band (device-game normalized):
- Average / median / variance of per-channel counts
- Per-channel average unique-BSSID counts
- Concentration metrics using per-channel averages

Output: outputs/10_channel_spread.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import output_to, header, subheader, print_table, fmt

DB_DEFAULT = "stadium.duckdb"
OUT_FILE = Path("outputs/10_channel_spread.txt")

QUERY_TEMPLATE = """
SELECT
    s.uuid,
    s.game_num,
    s.campaign_type,
    s.folder_name,
    w.band,
    w.channel_num,
    w.bssid
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.band IN ('5GHz', '6GHz')
  AND w.channel_num IS NOT NULL
  AND w.channel_num > 0
  AND w.bssid IS NOT NULL
  AND {campaign_filter}
"""


def run(db_path: Path, scope: str) -> None:
    if scope == "game":
        campaign_filter = "s.campaign_type = 'game'"
        scope_label = "game only"
    elif scope == "empty":
        campaign_filter = "s.campaign_type = 'empty'"
        scope_label = "empty only"
    elif scope == "game-empty":
        campaign_filter = "s.campaign_type IN ('game', 'empty')"
        scope_label = "game + empty"
    else:
        campaign_filter = "1=1"
        scope_label = "all campaigns"

    query = QUERY_TEMPLATE.format(campaign_filter=campaign_filter)

    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(query).fetchall()
    con.close()

    if not rows:
        print("No rows found for the selected scope.")
        return

    # Per-slice de-duplication:
    # slice_key=(device, game/session), then band->channel->set(bssid)
    # This avoids inflating counts due to repeated scans across time.
    slice_band_channel = {}
    for uuid, game_num, campaign, folder_name, band, channel, bssid in rows:
        if band not in ("5GHz", "6GHz"):
            continue

        # For game campaigns, normalize per (device, game_num).
        # For non-game scopes, use folder_name as session key fallback.
        session_key = f"game:{game_num}" if game_num is not None else f"{campaign}:{folder_name}"
        device_key = uuid or "unknown-device"
        slice_key = (device_key, session_key)

        slice_band_channel.setdefault(slice_key, {}).setdefault(band, {}).setdefault(int(channel), set()).add(bssid)

    # Aggregate slice-level channel counts:
    # band -> channel -> [count_in_slice, ...]
    band_channel_counts = {"5GHz": {}, "6GHz": {}}
    for _, band_map in slice_band_channel.items():
        for band, ch_map in band_map.items():
            for ch, bssid_set in ch_map.items():
                band_channel_counts[band].setdefault(ch, []).append(len(bssid_set))

    header("SECTION 10 — BEACON CHANNEL SPREAD")
    print(f"  Scope: {scope_label}")
    print("  Metric: per (device, game/session) unique-BSSID count per channel, then averaged")
    print(f"  Device-game/session slices: {len(slice_band_channel):,}")

    # ------------------------------------------------------------------
    subheader("10.1  Summary Stats by Band")
    summary_rows = []
    for band in ("5GHz", "6GHz"):
        ch_map = band_channel_counts[band]
        if not ch_map:
            summary_rows.append([band, 0, "N/A", "N/A", "N/A", "N/A", "N/A"])
            continue

        avg_counts = np.array([float(np.mean(v)) for v in ch_map.values()], dtype=float)
        var = np.var(avg_counts, ddof=1) if len(avg_counts) > 1 else 0.0

        summary_rows.append([
            band,
            len(ch_map),
            fmt(np.mean(avg_counts), 2),
            fmt(np.median(avg_counts), 2),
            fmt(var, 2),
            fmt(np.min(avg_counts), 2),
            fmt(np.max(avg_counts), 2),
        ])

    print()
    print_table([
        "Band", "Channels seen",
        "Mean/ch", "Median/ch", "Variance", "Min/ch", "Max/ch"
    ], summary_rows)

    # ------------------------------------------------------------------
    subheader("10.2  Per-Channel Average Unique-BSSID Counts")
    for band in ("5GHz", "6GHz"):
        ch_map = band_channel_counts[band]
        print(f"\n  {band}:")
        if not ch_map:
            print("    NO DATA")
            continue

        rows_out = []
        for ch in sorted(ch_map.keys()):
            arr = np.array(ch_map[ch], dtype=float)
            rows_out.append([
                ch,
                fmt(np.mean(arr), 2),
                fmt(np.median(arr), 2),
                fmt(np.var(arr, ddof=1) if len(arr) > 1 else 0.0, 2),
                len(arr),
            ])
        print_table(["Channel", "Avg unique BSSIDs", "Median", "Variance", "Slices"], rows_out, indent=4)

    # ------------------------------------------------------------------
    subheader("10.3  Channel Concentration (Spread Quality)")
    print("""
    HHI = sum(p_i^2), where p_i is each channel's share of the per-channel average
    unique-BSSID counts.
  Lower HHI and higher effective_channels (=1/HHI) imply better channel spread.
""")

    conc_rows = []
    for band in ("5GHz", "6GHz"):
        ch_map = band_channel_counts[band]
        if not ch_map:
            conc_rows.append([band, "N/A", "N/A", "N/A"])
            continue

        avg_counts = np.array([float(np.mean(v)) for v in ch_map.values()], dtype=float)
        shares = avg_counts / np.sum(avg_counts)
        hhi = float(np.sum(shares ** 2))
        eff = float(1.0 / hhi) if hhi > 0 else float("nan")

        conc_rows.append([
            band,
            fmt(hhi, 4),
            fmt(eff, 2),
            fmt(len(ch_map), 0),
        ])

    print()
    print_table(["Band", "HHI", "Effective channels", "Observed channels"], conc_rows)


def main():
    parser = argparse.ArgumentParser(description="Section 10: channel spread of unique BSSIDs")
    parser.add_argument("--db", default=DB_DEFAULT,
                        help=f"DuckDB path (default: {DB_DEFAULT})")
    parser.add_argument("--scope", choices=["all", "game", "empty", "game-empty"], default="game",
                        help="Campaign scope (default: game)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with output_to(OUT_FILE):
        run(db_path, args.scope)

    print(f"\nOutput written to: {OUT_FILE}")


if __name__ == "__main__":
    main()
