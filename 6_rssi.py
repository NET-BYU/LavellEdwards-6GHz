"""
6_rssi.py  --  Section 6: RSSI Distributions

6.1  RSSI by Band
     - Median, distribution comparison, CI for difference
     - Mann-Whitney U

6.2  RSSI vs Section Location
     - Kruskal-Wallis across sections per band
     - Post-hoc pairwise Mann-Whitney with Bonferroni correction

Output: outputs/6_rssi.txt
"""

import argparse
import sys
from itertools import combinations
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
OUT_FILE   = Path("outputs/6_rssi.txt")

QUERY_BAND = """
SELECT w.band, w.rssi, s.game_num
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.rssi IS NOT NULL
  AND w.band IN ('2.4GHz', '5GHz', '6GHz')
"""

QUERY_SECTION = """
SELECT w.band, w.rssi, s.section
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.rssi IS NOT NULL
  AND s.campaign_type = 'game'
  AND s.section IS NOT NULL
  AND w.band IN ('5GHz', '6GHz')
"""


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    band_rows    = con.execute(QUERY_BAND).fetchall()
    section_rows = con.execute(QUERY_SECTION).fetchall()
    con.close()

    # Band data
    band_data: dict = {}
    game_data: dict = {}
    for band, rssi, game_num in band_rows:
        band_data.setdefault(band, []).append(rssi)
        game_data.setdefault(band, {}).setdefault(game_num, []).append(rssi)
    band_arr  = {b: np.array(v) for b, v in band_data.items()}
    game_arr  = {b: {g: np.array(v) for g, v in gd.items()}
                 for b, gd in game_data.items()}

    # Section data
    sec_data: dict = {}
    for band, rssi, section in section_rows:
        sec_data.setdefault(band, {}).setdefault(section, []).append(rssi)
    sec_arr = {b: {sec: np.array(v) for sec, v in sd.items()}
               for b, sd in sec_data.items()}

    header("SECTION 6 — RSSI DISTRIBUTIONS")

    # ------------------------------------------------------------------
    subheader("6.1  Descriptive Statistics by Band")

    rows = []
    for band in ("2.4GHz", "5GHz", "6GHz"):
        arr = band_arr.get(band, np.array([]))
        if len(arr) == 0:
            rows.append([band, "0", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"])
            continue
        d = descriptive(arr)
        rows.append([band, f"{d['n']:,}", f"{d['mean']:.2f}", f"{d['median']:.1f}",
                     f"{d['std']:.2f}", f"{d['iqr']:.2f}", f"{d['p10']:.1f}", f"{d['p90']:.1f}"])
    print()
    print_table(["Band", "N", "Mean (dBm)", "Median", "Std", "IQR", "P10", "P90"], rows)

    # ------------------------------------------------------------------
    subheader("6.1b  5 GHz vs 6 GHz: Hypothesis Tests & Effect Sizes")

    arr5 = band_arr.get("5GHz", np.array([]))
    arr6 = band_arr.get("6GHz", np.array([]))

    obs, ci_lo, ci_hi = bootstrap_median_ci(arr5, arr6)
    print(f"\n  Median RSSI:")
    print(f"    5 GHz : {np.median(arr5):.2f} dBm")
    print(f"    6 GHz : {np.median(arr6):.2f} dBm")
    print(f"    Difference (6 - 5): {obs:+.2f} dBm")
    print(f"    Bootstrap 95% CI  : [{ci_lo:+.2f}, {ci_hi:+.2f}] dBm")
    print(f"    {'Contains zero (NOT significant)' if ci_lo <= 0 <= ci_hi else 'Does not contain zero (SIGNIFICANT)'}")

    u, p_mw = mannwhitney(arr5, arr6)
    print(f"\n  Mann-Whitney U: {u:.1f},  p = {fmt_p(p_mw)} {sig_stars(p_mw)}")

    cd  = cohens_d(arr5, arr6)
    cld = cliffs_delta(arr5, arr6)
    print(f"\n  Effect sizes (positive = 6GHz stronger signal):")
    print(f"    Cohen's d    : {cd:+.4f}  [{effect_label_d(cd)}]")
    print(f"    Cliff's delta: {cld:+.4f}  [{effect_label_cliff(cld)}]")

    # ------------------------------------------------------------------
    subheader("6.1c  RSSI Distribution Buckets")
    print(f"\n  {'Range (dBm)':<20} {'5GHz':>10} {'6GHz':>10}")
    print(f"  {'-'*20} {'-'*10} {'-'*10}")
    buckets = [
        ("Excellent (>= -67)", lambda x: x >= -67),
        ("Good      (-67 to -70)", lambda x: (x >= -70) & (x < -67)),
        ("Fair      (-70 to -80)", lambda x: (x >= -80) & (x < -70)),
        ("Weak      (< -80)",   lambda x: x < -80),
    ]
    for label, fn in buckets:
        p5 = np.mean(fn(arr5)) * 100
        p6 = np.mean(fn(arr6)) * 100
        print(f"  {label:<20} {p5:>9.1f}%  {p6:>9.1f}%")

    # ------------------------------------------------------------------
    subheader("6.1d  Per-Game RSSI Breakdown")
    games = sorted(set(game_arr.get("5GHz", {}).keys()) |
                   set(game_arr.get("6GHz", {}).keys()))
    print(f"\n  {'Game':<6} {'Band':<6} {'N':>7} {'Median':>8} {'Mean':>8} {'Std':>7} {'IQR':>7}")
    print(f"  {'-'*6} {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*7} {'-'*7}")
    for g in games:
        for band in ("5GHz", "6GHz"):
            arr = game_arr.get(band, {}).get(g, np.array([]))
            if len(arr) == 0:
                print(f"  {g:<6} {band:<6} {'NO DATA':>7}")
                continue
            d = descriptive(arr)
            print(f"  {g:<6} {band:<6} {d['n']:>7,} {d['median']:>8.2f} "
                  f"{d['mean']:>8.2f} {d['std']:>7.2f} {d['iqr']:>7.2f}")

    # ------------------------------------------------------------------
    subheader("6.2  RSSI vs Section Location (spatial heterogeneity)")
    print(f"\n  Kruskal-Wallis test: do sections differ in RSSI? (per band)")

    for band in ("5GHz", "6GHz"):
        sd = sec_arr.get(band, {})
        sections = sorted(sd.keys())
        if len(sections) < 2:
            print(f"\n  {band}: fewer than 2 sections with data — skipping")
            continue

        groups = [sd[s] for s in sections]
        h, p_kw = kruskal_wallis(*groups)
        print(f"\n  {band}:")
        print(f"    Sections: {', '.join(str(s) for s in sections)}")
        print(f"    Kruskal-Wallis H = {h:.3f},  p = {fmt_p(p_kw)} {sig_stars(p_kw)}")
        print(f"    {'Significant spatial heterogeneity detected' if p_kw < 0.05 else 'No significant difference across sections'}")

        # Per-section stats
        print(f"\n    {'Section':<10} {'N':>7} {'Median':>8} {'Mean':>8} {'IQR':>7}")
        print(f"    {'-'*10} {'-'*7} {'-'*8} {'-'*8} {'-'*7}")
        for sec in sections:
            arr = sd[sec]
            d = descriptive(arr)
            print(f"    {str(sec):<10} {d['n']:>7,} {d['median']:>8.2f} "
                  f"{d['mean']:>8.2f} {d['iqr']:>7.2f}")

        # Pairwise Mann-Whitney with Bonferroni correction
        if len(sections) <= 8:  # limit explosion of pairs
            pairs = list(combinations(sections, 2))
            alpha_bonf = 0.05 / len(pairs)
            print(f"\n    Pairwise Mann-Whitney (Bonferroni alpha = {alpha_bonf:.4f}):")
            print(f"    {'Pair':<18} {'U':>10} {'p':>12} {'Sig':>5} {'Cliff d':>8}")
            print(f"    {'-'*18} {'-'*10} {'-'*12} {'-'*5} {'-'*8}")
            for s1, s2 in pairs:
                a1, a2 = sd[s1], sd[s2]
                u_p, p_p = mannwhitney(a1, a2)
                cld = cliffs_delta(a1, a2)
                sig = "***" if p_p < alpha_bonf else ("*" if p_p < 0.05 else "ns")
                label = f"{s1} vs {s2}"
                print(f"    {label:<18} {u_p:>10.1f} {fmt_p(p_p):>12} {sig:>5} {cld:>+8.4f}")


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
