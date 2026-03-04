"""
8_game_consistency.py  --  Section 8: Event-to-Event Consistency

Compares key RF and throughput metrics between Game 1 and Game 2 to
assess whether the statistical patterns are reproducible across events.

8.1  Channel Utilization: Game 1 vs Game 2 (per band)
8.2  RSSI: Game 1 vs Game 2 (per band, all visible beacons)
8.3  Beacon Count per Snapshot: Game 1 vs Game 2 (per band)
8.4  Throughput: Game 1 vs Game 2 (download iperf, per band)
8.5  Consistency Index: direction of effect reproducible across games?

Output: outputs/8_game_consistency.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    output_to, header, subheader, fmt_p, sig_stars, print_table,
    descriptive, bootstrap_median_ci, mannwhitney, cohens_d, cliffs_delta,
    effect_label_d, effect_label_cliff,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE   = Path("outputs/8_game_consistency.txt")

QUERY_UTIL = """
SELECT w.band, s.game_num, w.ch_util
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.ch_util >= 0
  AND w.band IN ('5GHz', '6GHz')
  AND s.campaign_type = 'game'
"""

QUERY_RSSI = """
SELECT w.band, s.game_num, w.rssi
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.rssi IS NOT NULL
  AND w.band IN ('5GHz', '6GHz')
  AND s.campaign_type = 'game'
"""

# Beacon count per snapshot (by band)
QUERY_BEACON_COUNT = """
SELECT s.game_num, w.band, s.snapshot_id, COUNT(*) AS cnt
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.band IN ('5GHz', '6GHz')
  AND s.campaign_type = 'game'
GROUP BY s.game_num, w.band, s.snapshot_id
"""

QUERY_TPUT = """
SELECT s.game_num,
       -- connected AP band
       (SELECT w.band FROM wifi_beacons w
        WHERE w.snapshot_id = ir.snapshot_id AND w.connected = TRUE LIMIT 1) AS band,
       ir.tput_mbps
FROM iperf_results ir
JOIN snapshots s USING (snapshot_id)
WHERE ir.direction = 'download'
  AND ir.tput_mbps > 0
"""


def compare_games(metric_name: str, band: str, g1: np.ndarray, g2: np.ndarray) -> dict:
    """Return a dict of comparison statistics for two games."""
    if len(g1) == 0 or len(g2) == 0:
        return {}
    d1 = descriptive(g1); d2 = descriptive(g2)
    obs, ci_lo, ci_hi = bootstrap_median_ci(g1, g2)  # game2 - game1
    u, p_mw = mannwhitney(g1, g2)
    cd = cohens_d(g1, g2)
    cld = cliffs_delta(g1, g2)
    return dict(
        metric=metric_name, band=band,
        n1=d1["n"], median1=d1["median"], iqr1=d1["iqr"],
        n2=d2["n"], median2=d2["median"], iqr2=d2["iqr"],
        diff_median=obs, ci_lo=ci_lo, ci_hi=ci_hi,
        u=u, p_mw=p_mw, cd=cd, cld=cld,
    )


def print_comparison(r: dict) -> None:
    if not r:
        print("    (insufficient data)")
        return
    sig = "CI excludes zero" if not (r["ci_lo"] <= 0 <= r["ci_hi"]) else "CI includes zero"
    print(f"    Game 1  : N={r['n1']:,},  Median={r['median1']:.3f},  IQR={r['iqr1']:.3f}")
    print(f"    Game 2  : N={r['n2']:,},  Median={r['median2']:.3f},  IQR={r['iqr2']:.3f}")
    print(f"    G2-G1   : {r['diff_median']:+.3f}  95% Bootstrap CI [{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}]  {sig}")
    print(f"    Mann-Whitney U={r['u']:.1f},  p={fmt_p(r['p_mw'])} {sig_stars(r['p_mw'])}")
    print(f"    Cohen's d={r['cd']:+.4f} [{effect_label_d(r['cd'])}],  "
          f"Cliff's delta={r['cld']:+.4f} [{effect_label_cliff(r['cld'])}]")


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    util_rows    = con.execute(QUERY_UTIL).fetchall()
    rssi_rows    = con.execute(QUERY_RSSI).fetchall()
    beacon_rows  = con.execute(QUERY_BEACON_COUNT).fetchall()
    tput_rows    = con.execute(QUERY_TPUT).fetchall()
    con.close()

    # Organise
    def collect(rows, val_col=2, group_cols=(0, 1)):
        out: dict = {}
        for row in rows:
            key = tuple(row[i] for i in group_cols)
            out.setdefault(key, []).append(row[val_col])
        return {k: np.array(v) for k, v in out.items()}

    util_data    = collect(util_rows,   val_col=2, group_cols=(1, 0))   # (game, band)
    rssi_data    = collect(rssi_rows,   val_col=2, group_cols=(1, 0))
    # beacon_rows: game_num, band, snapshot_id, cnt  → group by (game, band), values = cnt
    beacon_data: dict = {}
    for game_num, band, _snap, cnt in beacon_rows:
        beacon_data.setdefault((game_num, band), []).append(cnt)
    beacon_data  = {k: np.array(v) for k, v in beacon_data.items()}
    # tput: game_num=row[0], band=row[1], tput=row[2]
    tput_data: dict = {}
    for game_num, band, tput in tput_rows:
        if band in ("5GHz", "6GHz"):
            tput_data.setdefault((game_num, band), []).append(tput)
    tput_data = {k: np.array(v) for k, v in tput_data.items()}

    header("SECTION 8 — EVENT-TO-EVENT CONSISTENCY")

    all_results = []

    for metric_name, data in [
            ("ch_util (%)",   util_data),
            ("RSSI (dBm)",    rssi_data),
            ("beacon_count",  beacon_data),
            ("tput (Mbps)",   tput_data)]:

        subheader(f"8.x  {metric_name}")

        for band in ("5GHz", "6GHz"):
            g1 = data.get((1, band), np.array([]))
            g2 = data.get((2, band), np.array([]))
            print(f"\n  Band: {band}")
            r = compare_games(metric_name, band, g1, g2)
            print_comparison(r)
            if r:
                all_results.append(r)

    # ------------------------------------------------------------------
    subheader("8.5  Consistency Summary Table")
    print()
    print(f"  {'Metric':<18} {'Band':<6} {'G1 Med':>10} {'G2 Med':>10} "
          f"{'Δ Med':>8} {'p':>12} {'Reproduces?':>12}")
    print(f"  {'-'*18} {'-'*6} {'-'*10} {'-'*10} {'-'*8} {'-'*12} {'-'*12}")
    for r in all_results:
        reproduces = "YES" if r["p_mw"] >= 0.05 else "DIFFERS"
        print(f"  {r['metric']:<18} {r['band']:<6} {r['median1']:>10.3f} {r['median2']:>10.3f} "
              f"{r['diff_median']:>+8.3f} {fmt_p(r['p_mw']):>12} {reproduces:>12}")

    print("""
  Interpretation guide:
    'REPRODUCES': No statistically significant difference between games.
    'DIFFERS'   : Significant change detected — magnitudes may still be small.
""")

    # ------------------------------------------------------------------
    subheader("8.6  Effect Direction Consistency (5 GHz vs 6 GHz within each game)")
    print("""
  Do we see the same direction of 5GHz→6GHz effects in both games?
  (Positive = 6GHz higher than 5GHz; Negative = 6GHz lower)
""")
    print(f"  {'Metric':<18} {'G1 direction':>14} {'G2 direction':>14} {'Consistent?':>12}")
    print(f"  {'-'*18} {'-'*14} {'-'*14} {'-'*12}")

    for metric_name, data in [
            ("ch_util (%)",  util_data),
            ("RSSI (dBm)",   rssi_data),
            ("tput (Mbps)",  tput_data)]:
        directions = []
        for g in (1, 2):
            a5 = data.get((g, "5GHz"), np.array([]))
            a6 = data.get((g, "6GHz"), np.array([]))
            if len(a5) == 0 or len(a6) == 0:
                directions.append("N/A")
            else:
                d = np.median(a6) - np.median(a5)
                directions.append(f"+{d:+.3f}" if d >= 0 else f"{d:+.3f}")
        if "N/A" in directions:
            consistent = "N/A"
        else:
            consistent = ("YES" if
                (float(directions[0]) >= 0) == (float(directions[1]) >= 0)
                else "NO")
        print(f"  {metric_name:<18} {directions[0]:>14} {directions[1]:>14} {consistent:>12}")


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
