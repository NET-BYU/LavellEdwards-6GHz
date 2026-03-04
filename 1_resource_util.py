"""
1_resource_util.py  --  Section 1: Band-Level Resource Utilization

Compares 5 GHz vs 6 GHz channel utilization across the full dataset,
with per-game breakdown and consistency analysis.

Output: outputs/1_resource_util.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    output_to, header, subheader, fmt, fmt_p, sig_stars, print_table,
    descriptive, bootstrap_median_ci, mannwhitney, cohens_d, cliffs_delta,
    effect_label_d, effect_label_cliff, kruskal_wallis,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE   = Path("outputs/1_resource_util.txt")

# Only beacons with reported ch_util; exclude iperf campaigns (no ch_util context)
QUERY_OVERALL = """
SELECT w.band, w.ch_util, s.game_num
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.ch_util >= 0
  AND w.band IN ('5GHz', '6GHz')
"""

QUERY_2_4 = """
SELECT w.ch_util
FROM wifi_beacons w
WHERE w.ch_util >= 0 AND w.band = '2.4GHz'
"""


def load(con) -> dict:
    rows = con.execute(QUERY_OVERALL).fetchall()
    data = {}   # band -> {game_num -> [ch_util values]}
    for band, ch_util, game_num in rows:
        data.setdefault(band, {}).setdefault(game_num, []).append(ch_util)
    return {b: {g: np.array(v) for g, v in games.items()}
            for b, games in data.items()}


def all_values(data: dict, band: str) -> np.ndarray:
    arrays = list(data[band].values())
    return np.concatenate(arrays)


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    data = load(con)
    arr24 = np.array([r[0] for r in con.execute(QUERY_2_4).fetchall()])
    con.close()

    arr5 = all_values(data, "5GHz")
    arr6 = all_values(data, "6GHz")
    games = sorted(set(data["5GHz"].keys()) | set(data["6GHz"].keys()))

    header("SECTION 1 — BAND-LEVEL RESOURCE UTILIZATION")
    print(f"  Metric: Channel Utilization (0.0 = idle, 1.0 = fully occupied)")
    print(f"  Scope : All wifi_beacons with reported ch_util (ch_util >= 0)")
    print(f"  Bands : 2.4 GHz (reference), 5 GHz, 6 GHz")
    print(f"\n  Observations -- 2.4GHz: {len(arr24):,}  |  5GHz: {len(arr5):,}  |  6GHz: {len(arr6):,}")

    # ------------------------------------------------------------------
    subheader("1.1  Descriptive Statistics by Band")
    d24 = descriptive(arr24)
    d5  = descriptive(arr5)
    d6  = descriptive(arr6)

    rows = []
    for key, label in [
        ("n",      "N"),
        ("mean",   "Mean"),
        ("median", "Median"),
        ("std",    "Std Dev"),
        ("iqr",    "IQR"),
        ("p25",    "25th pct"),
        ("p75",    "75th pct"),
    ]:
        va = str(int(d24[key])) if key == "n" else f"{d24[key]:.4f}"
        vb = str(int(d5[key]))  if key == "n" else f"{d5[key]:.4f}"
        vc = str(int(d6[key]))  if key == "n" else f"{d6[key]:.4f}"
        rows.append([label, va, vb, vc])
    print()
    print_table(["Statistic", "2.4GHz", "5GHz", "6GHz"], rows)

    # ------------------------------------------------------------------
    subheader("1.2  5 GHz vs 6 GHz: Hypothesis Tests & Effect Sizes")

    obs, ci_lo, ci_hi = bootstrap_median_ci(arr5, arr6)
    abs_diff = np.median(arr6) - np.median(arr5)
    rel_diff = abs_diff / np.median(arr5) * 100 if np.median(arr5) != 0 else float("nan")

    print(f"\n  Median channel utilization:")
    print(f"    5 GHz : {np.median(arr5):.4f}")
    print(f"    6 GHz : {np.median(arr6):.4f}")
    print(f"    Absolute difference (6 - 5): {abs_diff:+.4f} ({abs_diff*100:+.2f} pp)")
    print(f"    Relative difference         : {rel_diff:+.1f}%")

    print(f"\n  Bootstrap 95% CI for median difference (6GHz - 5GHz):")
    print(f"    Observed : {obs:+.4f}")
    print(f"    CI       : [{ci_lo:+.4f}, {ci_hi:+.4f}]")
    zero_note = "Contains zero (NOT significant)" if ci_lo <= 0 <= ci_hi else "Does not contain zero (SIGNIFICANT)"
    print(f"    {zero_note}")

    u, p_mw = mannwhitney(arr5, arr6)
    print(f"\n  Mann-Whitney U test (H0: same distribution):")
    print(f"    U = {u:.1f},  p = {fmt_p(p_mw)} {sig_stars(p_mw)}")

    cd  = cohens_d(arr5, arr6)
    cld = cliffs_delta(arr5, arr6)
    print(f"\n  Effect sizes (positive = 6GHz > 5GHz):")
    print(f"    Cohen's d    : {cd:+.4f}  [{effect_label_d(cd)}]")
    print(f"    Cliff's delta: {cld:+.4f}  [{effect_label_cliff(cld)}]")

    # ------------------------------------------------------------------
    subheader("1.3  Per-Game Breakdown")
    print(f"\n  {'Game':<6} {'Band':<6} {'N':>7} {'Median':>8} {'Mean':>8} {'IQR':>8} {'Std':>8}")
    print(f"  {'-'*6} {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for g in games:
        for band in ("5GHz", "6GHz"):
            arr = data[band].get(g, np.array([]))
            if len(arr) == 0:
                print(f"  {g:<6} {band:<6} {'NO DATA':>7}")
                continue
            d = descriptive(arr)
            print(f"  {g:<6} {band:<6} {d['n']:>7,} {d['median']:>8.4f} "
                  f"{d['mean']:>8.4f} {d['iqr']:>8.4f} {d['std']:>8.4f}")

    # ------------------------------------------------------------------
    subheader("1.4  Per-Game Median Difference Consistency")
    print(f"\n  (Negative diff = 6GHz has lower channel utilization — desirable)")
    print(f"\n  {'Game':<6} {'5GHz Median':>14} {'6GHz Median':>14} {'Diff (6-5)':>12} {'% reduction':>12}")
    print(f"  {'-'*6} {'-'*14} {'-'*14} {'-'*12} {'-'*12}")
    diffs = []
    for g in games:
        a5 = data["5GHz"].get(g, np.array([]))
        a6 = data["6GHz"].get(g, np.array([]))
        if len(a5) == 0 or len(a6) == 0:
            print(f"  {g:<6} {'insufficient data':>42}")
            continue
        m5, m6 = np.median(a5), np.median(a6)
        diff = m6 - m5
        pct  = diff / m5 * 100 if m5 != 0 else float("nan")
        diffs.append(diff)
        print(f"  {g:<6} {m5:>14.4f} {m6:>14.4f} {diff:>+12.4f} {pct:>+11.1f}%")

    if len(diffs) >= 2:
        consistent = all(d < 0 for d in diffs) or all(d > 0 for d in diffs)
        direction  = "6 GHz lower (consistent)" if all(d < 0 for d in diffs) else \
                     "6 GHz higher (consistent)" if all(d > 0 for d in diffs) else \
                     "mixed direction (inconsistent)"
        print(f"\n  Direction consistency across games: {direction}")

    # ------------------------------------------------------------------
    subheader("1.5  Kruskal-Wallis: 5GHz vs 6GHz vs 2.4GHz")
    h, p_kw = kruskal_wallis(arr24, arr5, arr6)
    print(f"\n  H = {h:.3f},  p = {fmt_p(p_kw)} {sig_stars(p_kw)}")
    print(f"  Interpretation: {'Significant difference among bands' if p_kw < 0.05 else 'No significant difference'}")

    # ------------------------------------------------------------------
    subheader("1.6  Utilization Buckets (% of observations)")
    print(f"\n  {'Bucket':<25} {'5GHz':>10} {'6GHz':>10}")
    print(f"  {'-'*25} {'-'*10} {'-'*10}")
    thresholds = [
        ("Low   (< 30%)",  lambda x: x < 0.30),
        ("Medium (30-60%)", lambda x: (x >= 0.30) & (x < 0.60)),
        ("High   (>= 60%)", lambda x: x >= 0.60),
    ]
    for label, fn in thresholds:
        pct5 = np.mean(fn(arr5)) * 100
        pct6 = np.mean(fn(arr6)) * 100
        print(f"  {label:<25} {pct5:>9.1f}%  {pct6:>9.1f}%")


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
