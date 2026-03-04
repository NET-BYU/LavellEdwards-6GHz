"""
2_throughput.py  --  Section 2: Throughput Performance

2.1  5 GHz vs 6 GHz iperf Throughput
2.2  Throughput vs RSSI per band (Spearman + Theil-Sen)
2.3  Throughput Stability (CV, variance, % below usability threshold)

Note: Only TCP download iperf data is present in this dataset.
      Band assignment is derived from the connected AP in each snapshot.

Output: outputs/2_throughput.txt
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
    effect_label_d, effect_label_cliff, levene_test, spearman, theilsen,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE   = Path("outputs/2_throughput.txt")

# One row per iperf interval joined to its connected AP band+rssi
QUERY = """
SELECT
    i.tput_mbps,
    w.band,
    w.rssi,
    s.game_num,
    s.round_num
FROM iperf_results i
JOIN snapshots s USING (snapshot_id)
JOIN wifi_beacons w USING (snapshot_id)
WHERE w.connected = true
  AND w.band IN ('5GHz', '6GHz')
  AND i.tput_mbps IS NOT NULL
ORDER BY w.band, s.datetime_iso
"""

USABILITY_THRESHOLD_MBPS = 10.0


def load(con) -> dict:
    rows = con.execute(QUERY).fetchall()
    # band -> {game_num -> {rssi: [], tput: []}}
    data: dict = {}
    for tput, band, rssi, game_num, round_num in rows:
        b = data.setdefault(band, {})
        g = b.setdefault(game_num, {"tput": [], "rssi": []})
        g["tput"].append(tput)
        g["rssi"].append(rssi)
    return {band: {g: {k: np.array(v) for k, v in gd.items()}
                   for g, gd in gdata.items()}
            for band, gdata in data.items()}


def all_arr(data, band, key) -> np.ndarray:
    arrs = [gd[key] for gd in data[band].values()]
    return np.concatenate(arrs)


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    data = load(con)
    con.close()

    arr5_t = all_arr(data, "5GHz", "tput")
    arr6_t = all_arr(data, "6GHz", "tput")
    arr5_r = all_arr(data, "5GHz", "rssi")
    arr6_r = all_arr(data, "6GHz", "rssi")
    games  = sorted(set(data["5GHz"].keys()) | set(data["6GHz"].keys()))

    header("SECTION 2 — THROUGHPUT PERFORMANCE")
    print(f"  Source  : iperf TCP download intervals joined to connected AP")
    print(f"  5 GHz observations : {len(arr5_t):,}")
    print(f"  6 GHz observations : {len(arr6_t):,}")

    # ------------------------------------------------------------------
    subheader("2.1  Descriptive Statistics & Two-Group Comparison")

    d5 = descriptive(arr5_t)
    d6 = descriptive(arr6_t)
    rows = []
    for key, label in [
        ("n",      "N"),
        ("mean",   "Mean (Mbps)"),
        ("median", "Median (Mbps)"),
        ("std",    "Std Dev"),
        ("iqr",    "IQR"),
        ("p10",    "10th pct (tail low)"),
        ("p90",    "90th pct (tail high)"),
        ("min",    "Min"),
        ("max",    "Max"),
    ]:
        va = str(int(d5[key])) if key == "n" else f"{d5[key]:.3f}"
        vb = str(int(d6[key])) if key == "n" else f"{d6[key]:.3f}"
        diff = "" if key == "n" else f"{d6[key]-d5[key]:+.3f}"
        rows.append([label, va, vb, diff])
    print()
    print_table(["Statistic", "5GHz", "6GHz", "Diff (6-5)"], rows)

    obs, ci_lo, ci_hi = bootstrap_median_ci(arr5_t, arr6_t)
    print(f"\n  Bootstrap 95% CI for median diff (6GHz - 5GHz):")
    print(f"    Observed : {obs:+.3f} Mbps")
    print(f"    CI       : [{ci_lo:+.3f}, {ci_hi:+.3f}] Mbps")
    print(f"    {'Contains zero (NOT significant)' if ci_lo <= 0 <= ci_hi else 'Does not contain zero (SIGNIFICANT)'}")

    u, p_mw = mannwhitney(arr5_t, arr6_t)
    print(f"\n  Mann-Whitney U: U={u:.1f},  p={fmt_p(p_mw)} {sig_stars(p_mw)}")

    cd  = cohens_d(arr5_t, arr6_t)
    cld = cliffs_delta(arr5_t, arr6_t)
    print(f"\n  Effect sizes (positive = 6GHz > 5GHz):")
    print(f"    Cohen's d    : {cd:+.4f}  [{effect_label_d(cd)}]")
    print(f"    Cliff's delta: {cld:+.4f}  [{effect_label_cliff(cld)}]")

    # ------------------------------------------------------------------
    subheader("2.1b  Per-Game Throughput Breakdown")
    print(f"\n  {'Game':<6} {'Band':<6} {'N':>6} {'Median':>8} {'Mean':>8} "
          f"{'P10':>8} {'P90':>8} {'IQR':>8}")
    print(f"  {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for g in games:
        for band in ("5GHz", "6GHz"):
            arr = data[band].get(g, {}).get("tput", np.array([]))
            if len(arr) == 0:
                print(f"  {g:<6} {band:<6} {'NO DATA':>6}")
                continue
            d = descriptive(arr)
            print(f"  {g:<6} {band:<6} {d['n']:>6,} {d['median']:>8.2f} {d['mean']:>8.2f} "
                  f"{d['p10']:>8.2f} {d['p90']:>8.2f} {d['iqr']:>8.2f}")

    # ------------------------------------------------------------------
    subheader("2.2  Throughput vs RSSI: Spearman Correlation")
    print(f"\n  (Tests whether stronger signal predicts higher throughput per band)")
    print(f"\n  {'Band':<6} {'N':>6} {'Spearman rho':>14} {'p-value':>12} {'Sig':>5} {'Slope (Mbps/dBm)':>18}")
    print(f"  {'-'*6} {'-'*6} {'-'*14} {'-'*12} {'-'*5} {'-'*18}")
    for band, arr_t, arr_r in [("5GHz", arr5_t, arr5_r), ("6GHz", arr6_t, arr6_r)]:
        mask = ~np.isnan(arr_r)
        t2, r2 = arr_t[mask], arr_r[mask]
        rho, p_sp = spearman(r2, t2)
        slope, intercept, *_ = theilsen(r2, t2)
        print(f"  {band:<6} {len(t2):>6,} {rho:>14.4f} {fmt_p(p_sp):>12} "
              f"{sig_stars(p_sp):>5} {slope:>18.4f}")

    print(f"\n  Theil-Sen slope (Mbps per 1 dBm improvement = less negative RSSI):")
    print(f"  Positive slope = better signal -> higher throughput")
    print(f"  Steeper slope in one band = more RSSI-sensitive throughput in that band")

    subheader("2.2b  Throughput vs RSSI per Game")
    print(f"\n  {'Game':<6} {'Band':<6} {'N':>6} {'Spearman rho':>14} {'p-value':>12} {'Sig':>5} {'Slope':>8}")
    print(f"  {'-'*6} {'-'*6} {'-'*6} {'-'*14} {'-'*12} {'-'*5} {'-'*8}")
    for g in games:
        for band in ("5GHz", "6GHz"):
            gdata = data[band].get(g)
            if gdata is None or len(gdata["tput"]) < 5:
                continue
            t2, r2 = gdata["tput"], gdata["rssi"]
            mask = ~np.isnan(r2)
            t2, r2 = t2[mask], r2[mask]
            if len(t2) < 5:
                continue
            rho, p_sp = spearman(r2, t2)
            slope, *_ = theilsen(r2, t2)
            print(f"  {g:<6} {band:<6} {len(t2):>6,} {rho:>14.4f} {fmt_p(p_sp):>12} "
                  f"{sig_stars(p_sp):>5} {slope:>8.4f}")

    # ------------------------------------------------------------------
    subheader("2.3  Throughput Stability")

    print(f"\n  Coefficient of Variation (CV = std/mean; lower = more stable):")
    for band, arr in [("5GHz", arr5_t), ("6GHz", arr6_t)]:
        d = descriptive(arr)
        print(f"    {band}: CV = {d['cv']:.4f}  ({d['cv']*100:.1f}%)")

    print(f"\n  Variance comparison (Levene test for homogeneity of variance):")
    lw, lp = levene_test(arr5_t, arr6_t)
    print(f"    W = {lw:.4f},  p = {fmt_p(lp)} {sig_stars(lp)}")
    print(f"    {'Variances are significantly different' if lp < 0.05 else 'No significant difference in variance'}")
    print(f"    5GHz variance: {np.var(arr5_t, ddof=1):.3f}  |  6GHz variance: {np.var(arr6_t, ddof=1):.3f}")

    threshold = USABILITY_THRESHOLD_MBPS
    print(f"\n  Percent of iperf intervals below usability threshold ({threshold} Mbps):")
    for band, arr in [("5GHz", arr5_t), ("6GHz", arr6_t)]:
        pct = np.mean(arr < threshold) * 100
        print(f"    {band}: {pct:.1f}%  ({int(np.sum(arr < threshold))} / {len(arr)} intervals)")

    print(f"\n  Zero-throughput intervals (tput_mbps == 0):")
    for band, arr in [("5GHz", arr5_t), ("6GHz", arr6_t)]:
        n_zero = int(np.sum(arr == 0))
        pct = n_zero / len(arr) * 100
        print(f"    {band}: {n_zero} ({pct:.1f}%)")


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
