"""
7_util_vs_tput.py  --  Section 7: Channel Utilization vs Throughput

Join iperf throughput intervals with the ch_util of the best-reported
beacon in that snapshot (the connected AP, if available; otherwise the
beacon with the strongest RSSI that has a valid ch_util reading).

7.1  Scatter / correlation:  Spearman(ch_util, tput_mbps) per band
7.2  Theil-Sen slope per band
7.3  Quartile breakdown: median tput by ch_util quartile per band
7.4  Per-game breakdown

Output: outputs/7_util_vs_tput.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    output_to, header, subheader, fmt, fmt_p, sig_stars, print_table,
    descriptive, bootstrap_median_ci, spearman, theilsen,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE   = Path("outputs/7_util_vs_tput.txt")

# For each iperf interval, find the ch_util of the connected AP beacon
# in that snapshot. If the connected AP has ch_util = -1 (unreported),
# fall back to the highest-RSSI beacon with valid ch_util on the same band.
QUERY = """
WITH iperf_snaps AS (
    SELECT ir.snapshot_id,
           ir.tput_mbps,
           s.game_num,
           -- connected AP info
           (SELECT w.ch_util
            FROM wifi_beacons w
            WHERE w.snapshot_id = ir.snapshot_id
              AND w.connected = TRUE
              AND w.ch_util >= 0
            LIMIT 1) AS connected_ch_util,
           -- connected AP band
           (SELECT w.band
            FROM wifi_beacons w
            WHERE w.snapshot_id = ir.snapshot_id
              AND w.connected = TRUE
            LIMIT 1) AS connected_band,
           -- fallback: best RSSI beacon with valid ch_util (same band as connected)
           (SELECT w2.ch_util
            FROM wifi_beacons w2
            WHERE w2.snapshot_id = ir.snapshot_id
              AND w2.band = (SELECT w.band FROM wifi_beacons w
                             WHERE w.snapshot_id = ir.snapshot_id
                               AND w.connected = TRUE LIMIT 1)
              AND w2.ch_util >= 0
            ORDER BY w2.rssi DESC
            LIMIT 1) AS fallback_ch_util
    FROM iperf_results ir
    JOIN snapshots s USING (snapshot_id)
    WHERE ir.direction = 'download'
      AND ir.tput_mbps > 0
)
SELECT
    game_num,
    tput_mbps,
    connected_band AS band,
    COALESCE(connected_ch_util, fallback_ch_util) AS ch_util
FROM iperf_snaps
WHERE connected_band IN ('5GHz', '6GHz')
  AND COALESCE(connected_ch_util, fallback_ch_util) IS NOT NULL
ORDER BY game_num, connected_band
"""


def quartile_breakdown(util: np.ndarray, tput: np.ndarray) -> None:
    """Print median throughput for each quartile of ch_util."""
    q25, q50, q75 = np.percentile(util, [25, 50, 75])
    masks = [
        ("Q1 (low)",  util <= q25),
        (f"Q2",       (util > q25) & (util <= q50)),
        (f"Q3",       (util > q50) & (util <= q75)),
        ("Q4 (high)", util > q75),
    ]
    print(f"    ch_util percentiles: P25={q25:.1f}%, P50={q50:.1f}%, P75={q75:.1f}%")
    print(f"\n    {'Quartile':<12} {'ch_util range':>18} {'N':>6} "
          f"{'Median tput':>12} {'Mean tput':>12}")
    print(f"    {'-'*12} {'-'*18} {'-'*6} {'-'*12} {'-'*12}")
    for label, mask in masks:
        n = np.sum(mask)
        if n == 0:
            print(f"    {label:<12} {'—':>18} {'0':>6}")
            continue
        t = tput[mask]
        u_vals = util[mask]
        u_range = f"{u_vals.min():.0f}–{u_vals.max():.0f}%"
        print(f"    {label:<12} {u_range:>18} {n:>6,} "
              f"{np.median(t):>11.2f}  {np.mean(t):>11.2f}")


def run(db_path: Path) -> None:
    con  = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(QUERY).fetchall()
    con.close()

    # Organize data
    band_data: dict   = {}
    game_band_data: dict = {}
    n_connected_util  = 0
    n_fallback_util   = 0

    for game_num, tput, band, ch_util in rows:
        band_data.setdefault(band, {"util": [], "tput": []})
        band_data[band]["util"].append(ch_util)
        band_data[band]["tput"].append(tput)
        game_band_data.setdefault(game_num, {}).setdefault(band, {"util": [], "tput": []})
        game_band_data[game_num][band]["util"].append(ch_util)
        game_band_data[game_num][band]["tput"].append(tput)

    band_arr = {b: {"util": np.array(v["util"]), "tput": np.array(v["tput"])}
                for b, v in band_data.items()}

    header("SECTION 7 — CHANNEL UTILIZATION vs THROUGHPUT")

    # ------------------------------------------------------------------
    subheader("7.1  Overview: Available Paired Observations")
    print(f"\n  Total observations with valid ch_util + tput pairs:")
    for band in ("5GHz", "6GHz"):
        bd = band_arr.get(band)
        if bd is None:
            print(f"    {band}: NO DATA"); continue
        n = len(bd["util"])
        print(f"    {band}: N = {n:,},  "
              f"ch_util range {bd['util'].min():.0f}–{bd['util'].max():.0f}%,  "
              f"tput range {bd['tput'].min():.1f}–{bd['tput'].max():.1f} Mbps")

    # ------------------------------------------------------------------
    subheader("7.2  Spearman Correlation: ch_util vs Throughput (pooled)")
    print()
    for band in ("5GHz", "6GHz"):
        bd = band_arr.get(band)
        if bd is None:
            print(f"  {band}: NO DATA"); continue
        util = bd["util"]; tput = bd["tput"]
        rho, p_val = spearman(util, tput)
        print(f"  {band}:  rho = {rho:+.4f},  p = {fmt_p(p_val)} {sig_stars(p_val)}")
        print(f"           N = {len(util):,}")
        if p_val < 0.05:
            direction = "negative" if rho < 0 else "positive"
            print(f"           Interpretation: {direction} association — "
                  f"{'higher utilization correlates with LOWER throughput' if rho < 0 else 'higher utilization correlates with HIGHER throughput'}")
        else:
            print(f"           No significant association detected")
        print()

    # ------------------------------------------------------------------
    subheader("7.3  Theil-Sen Slope: ch_util vs Throughput")
    print()
    for band in ("5GHz", "6GHz"):
        bd = band_arr.get(band)
        if bd is None:
            print(f"  {band}: NO DATA"); continue
        util = bd["util"]; tput = bd["tput"]
        slope, intercept, lo, hi = theilsen(util, tput)
        print(f"  {band}:")
        print(f"    Slope     : {slope:.4f} Mbps per 1% increase in ch_util")
        print(f"    Intercept : {intercept:.2f} Mbps")
        print(f"    95% CI    : [{lo:.4f}, {hi:.4f}]")
        print(f"    Practical : +10% util → {slope * 10:+.2f} Mbps change")
        print()

    # ------------------------------------------------------------------
    subheader("7.4  Throughput by ch_util Quartile")
    for band in ("5GHz", "6GHz"):
        bd = band_arr.get(band)
        if bd is None:
            print(f"\n  {band}: NO DATA"); continue
        print(f"\n  {band}:")
        quartile_breakdown(bd["util"], bd["tput"])

    # ------------------------------------------------------------------
    subheader("7.5  Per-Game Spearman Correlation")
    games = sorted(k for k in game_band_data.keys() if k is not None)
    print()
    print(f"  {'Game':<6} {'Band':<6} {'N':>6} {'rho':>8} {'p':>12} {'Sig':>5}")
    print(f"  {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*12} {'-'*5}")
    for g in games:
        for band in ("5GHz", "6GHz"):
            gbd = game_band_data.get(g, {}).get(band)
            if gbd is None:
                print(f"  {g:<6} {band:<6}  NO DATA"); continue
            util = np.array(gbd["util"]); tput = np.array(gbd["tput"])
            if len(util) < 3:
                print(f"  {g:<6} {band:<6} {len(util):>6}  (too few points)"); continue
            rho, p_val = spearman(util, tput)
            print(f"  {g:<6} {band:<6} {len(util):>6,} {rho:>+8.4f} "
                  f"{fmt_p(p_val):>12} {sig_stars(p_val):>5}")

    # ------------------------------------------------------------------
    subheader("7.6  Median Throughput at Low (< 20%) vs High (>= 20%) Utilization")
    print()
    threshold = 20.0
    for band in ("5GHz", "6GHz"):
        bd = band_arr.get(band)
        if bd is None:
            print(f"  {band}: NO DATA"); continue
        util = bd["util"]; tput = bd["tput"]
        lo_mask = util < threshold
        hi_mask = util >= threshold
        if np.sum(lo_mask) == 0 or np.sum(hi_mask) == 0:
            print(f"  {band}: insufficient data in one bin (threshold={threshold}%)"); continue
        t_lo = tput[lo_mask]; t_hi = tput[hi_mask]
        print(f"  {band}:")
        print(f"    Low ch_util  (< {threshold:.0f}%): N={len(t_lo):,},  "
              f"median={np.median(t_lo):.2f} Mbps,  mean={np.mean(t_lo):.2f} Mbps")
        print(f"    High ch_util (>={threshold:.0f}%): N={len(t_hi):,},  "
              f"median={np.median(t_hi):.2f} Mbps,  mean={np.mean(t_hi):.2f} Mbps")
        obs, ci_lo, ci_hi = bootstrap_median_ci(t_lo, t_hi)  # high - low
        print(f"    Median diff (high-low): {obs:+.2f} Mbps  95% CI [{ci_lo:+.2f}, {ci_hi:+.2f}]")
        print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DB_DEFAULT)
    args = p.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}"); sys.exit(1)
    with output_to(OUT_FILE):
        run(db_path)
        print(f"\n\n  Output written to: {OUT_FILE}")


if __name__ == "__main__":
    main()
