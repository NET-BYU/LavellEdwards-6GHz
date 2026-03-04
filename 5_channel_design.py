"""
5_channel_design.py  --  Section 5: Power and Channel Width Design Effects

5.1  Channel Width Comparison
     - Throughput per MHz by band (iperf connected APs)
     - Utilization per MHz by band (all beacons)
     - Spectral efficiency estimate

5.2  TX Power Comparison
     - Advertised TX power distributions per band
     - Spearman(tx_power, rssi) per band
     - Spearman(tx_power, tput_mbps) per band (iperf snapshots)

Output: outputs/5_channel_design.txt
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
    effect_label_d, effect_label_cliff, spearman,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE   = Path("outputs/5_channel_design.txt")

# All beacons with valid width for utilization-per-MHz
QUERY_UTIL_WIDTH = """
SELECT w.band, w.width, w.ch_util
FROM wifi_beacons w
WHERE w.ch_util >= 0
  AND w.width > 0
  AND w.band IN ('5GHz', '6GHz')
"""

# iperf snapshots: throughput joined to connected AP width + rssi
QUERY_TPUT_WIDTH = """
SELECT w.band, w.width, i.tput_mbps, w.rssi
FROM iperf_results i
JOIN snapshots s USING (snapshot_id)
JOIN wifi_beacons w USING (snapshot_id)
WHERE w.connected = true
  AND w.width > 0
  AND w.band IN ('5GHz', '6GHz')
  AND i.tput_mbps IS NOT NULL
"""

# All beacons with valid tx_power
QUERY_TXPOWER = """
SELECT w.band, w.tx_power, w.rssi
FROM wifi_beacons w
WHERE w.tx_power > 0
  AND w.tx_power < 2147483647
  AND w.band IN ('5GHz', '6GHz')
"""

# tx_power in iperf snapshots for correlation with tput
QUERY_TXPOWER_TPUT = """
SELECT w.band, w.tx_power, i.tput_mbps, w.rssi
FROM iperf_results i
JOIN snapshots s USING (snapshot_id)
JOIN wifi_beacons w USING (snapshot_id)
WHERE w.connected = true
  AND w.tx_power > 0
  AND w.tx_power < 2147483647
  AND w.band IN ('5GHz', '6GHz')
  AND i.tput_mbps IS NOT NULL
"""


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)

    util_rows   = con.execute(QUERY_UTIL_WIDTH).fetchall()
    tput_rows   = con.execute(QUERY_TPUT_WIDTH).fetchall()
    txpow_rows  = con.execute(QUERY_TXPOWER).fetchall()
    tput_tx_rows = con.execute(QUERY_TXPOWER_TPUT).fetchall()
    con.close()

    # Organise by band
    def split(rows, *keys):
        out = {"5GHz": {k: [] for k in keys}, "6GHz": {k: [] for k in keys}}
        for row in rows:
            band = row[0]
            if band in out:
                for i, k in enumerate(keys):
                    out[band][k].append(row[1 + i])
        return {b: {k: np.array(v) for k, v in d.items()} for b, d in out.items()}

    uw = split(util_rows, "width", "ch_util")
    tw = split(tput_rows, "width", "tput", "rssi")
    tp = split(txpow_rows, "tx_power", "rssi")
    tt = split(tput_tx_rows, "tx_power", "tput", "rssi")

    header("SECTION 5 — POWER AND CHANNEL WIDTH DESIGN EFFECTS")

    # ------------------------------------------------------------------
    subheader("5.1  Channel Width Distribution by Band")

    for band in ("5GHz", "6GHz"):
        widths = uw[band]["width"]
        vals, counts = np.unique(widths, return_counts=True)
        total = len(widths)
        print(f"\n  {band} -- {total:,} beacons with reported width:")
        print(f"  {'Width (MHz)':<14} {'Count':>8} {'Pct':>8}")
        print(f"  {'-'*14} {'-'*8} {'-'*8}")
        for v, c in zip(vals, counts):
            print(f"  {int(v):<14} {c:>8,} {c/total*100:>7.1f}%")
        print(f"  Mean width: {np.mean(widths):.1f} MHz  |  Median: {np.median(widths):.0f} MHz")

    # Width comparison
    w5 = uw["5GHz"]["width"]
    w6 = uw["6GHz"]["width"]
    obs, ci_lo, ci_hi = bootstrap_median_ci(w5, w6)
    u, p_mw = mannwhitney(w5, w6)
    print(f"\n  Median width difference (6GHz - 5GHz): {obs:+.1f} MHz")
    print(f"  Bootstrap 95% CI: [{ci_lo:+.1f}, {ci_hi:+.1f}] MHz")
    print(f"  Mann-Whitney U: {u:.1f},  p = {fmt_p(p_mw)} {sig_stars(p_mw)}")

    # ------------------------------------------------------------------
    subheader("5.1b  Channel Utilization per MHz by Band")
    print(f"\n  Util/MHz = ch_util / channel_width_MHz")
    print(f"  Lower value = better spectral efficiency (less utilization per MHz consumed)\n")

    rows_out = []
    for band in ("5GHz", "6GHz"):
        util = uw[band]["ch_util"]
        width = uw[band]["width"]
        util_per_mhz = util / width
        d = descriptive(util_per_mhz)
        rows_out.append([band, f"{d['n']:,}", f"{d['mean']:.5f}",
                          f"{d['median']:.5f}", f"{d['std']:.5f}", f"{d['iqr']:.5f}"])
    print_table(["Band", "N", "Mean util/MHz", "Median util/MHz", "Std", "IQR"], rows_out)

    upm5 = uw["5GHz"]["ch_util"] / uw["5GHz"]["width"]
    upm6 = uw["6GHz"]["ch_util"] / uw["6GHz"]["width"]
    obs2, ci_lo2, ci_hi2 = bootstrap_median_ci(upm5, upm6)
    u2, p2 = mannwhitney(upm5, upm6)
    print(f"\n  Median util/MHz diff (6-5): {obs2:+.5f}")
    print(f"  Bootstrap 95% CI: [{ci_lo2:+.5f}, {ci_hi2:+.5f}]")
    print(f"  Mann-Whitney: U={u2:.1f},  p={fmt_p(p2)} {sig_stars(p2)}")

    # ------------------------------------------------------------------
    subheader("5.1c  Throughput per MHz (iperf connected APs)")
    print(f"\n  Tput/MHz = iperf_tput_mbps / connected_AP_channel_width\n")

    rows_out2 = []
    for band in ("5GHz", "6GHz"):
        tput = tw[band]["tput"]
        width = tw[band]["width"]
        if len(tput) == 0:
            rows_out2.append([band, "0", "N/A", "N/A", "N/A", "N/A"])
            continue
        tpm = tput / width
        d = descriptive(tpm)
        rows_out2.append([band, f"{d['n']:,}", f"{d['mean']:.4f}",
                           f"{d['median']:.4f}", f"{d['std']:.4f}", f"{d['iqr']:.4f}"])
    print_table(["Band", "N", "Mean Mbps/MHz", "Median Mbps/MHz", "Std", "IQR"], rows_out2)

    tpm5 = tw["5GHz"]["tput"] / tw["5GHz"]["width"]
    tpm6 = tw["6GHz"]["tput"] / tw["6GHz"]["width"]
    if len(tpm5) > 1 and len(tpm6) > 1:
        obs3, ci_lo3, ci_hi3 = bootstrap_median_ci(tpm5, tpm6)
        u3, p3 = mannwhitney(tpm5, tpm6)
        print(f"\n  Median Mbps/MHz diff (6-5): {obs3:+.4f}")
        print(f"  Bootstrap 95% CI: [{ci_lo3:+.4f}, {ci_hi3:+.4f}]")
        print(f"  Mann-Whitney: U={u3:.1f},  p={fmt_p(p3)} {sig_stars(p3)}")

    # ------------------------------------------------------------------
    subheader("5.2  TX Power Distribution by Band")

    rows_out3 = []
    for band in ("5GHz", "6GHz"):
        txp = tp[band]["tx_power"]
        d = descriptive(txp)
        rows_out3.append([band, f"{d['n']:,}", f"{d['mean']:.2f}",
                           f"{d['median']:.1f}", f"{d['std']:.2f}",
                           f"{d['min']:.0f}", f"{d['max']:.0f}"])
    print()
    print_table(["Band", "N", "Mean (dBm)", "Median", "Std", "Min", "Max"], rows_out3)

    tp5 = tp["5GHz"]["tx_power"]
    tp6 = tp["6GHz"]["tx_power"]
    obs4, ci_lo4, ci_hi4 = bootstrap_median_ci(tp5, tp6)
    u4, p4 = mannwhitney(tp5, tp6)
    cd = cohens_d(tp5, tp6)
    cld = cliffs_delta(tp5, tp6)
    print(f"\n  Median TX power diff (6GHz - 5GHz): {obs4:+.2f} dBm")
    print(f"  Bootstrap 95% CI: [{ci_lo4:+.2f}, {ci_hi4:+.2f}] dBm")
    print(f"  Mann-Whitney: U={u4:.1f},  p={fmt_p(p4)} {sig_stars(p4)}")
    print(f"  Cohen's d: {cd:+.4f}  [{effect_label_d(cd)}]")
    print(f"  Cliff's delta: {cld:+.4f}  [{effect_label_cliff(cld)}]")

    # ------------------------------------------------------------------
    subheader("5.2b  TX Power vs RSSI Correlation per Band")
    print(f"\n  (Tests whether advertised TX power predicts observed RSSI)")
    print(f"  Note: positive rho = higher TX power -> stronger received signal\n")
    print(f"  {'Band':<6} {'N':>6} {'Spearman rho':>14} {'p-value':>12} {'Sig':>5}")
    print(f"  {'-'*6} {'-'*6} {'-'*14} {'-'*12} {'-'*5}")
    for band in ("5GHz", "6GHz"):
        txpow = tp[band]["tx_power"]
        rssi  = tp[band]["rssi"]
        mask  = ~np.isnan(rssi) & ~np.isnan(txpow)
        if mask.sum() < 5:
            print(f"  {band:<6} insufficient data")
            continue
        rho, p_sp = spearman(txpow[mask], rssi[mask])
        print(f"  {band:<6} {mask.sum():>6,} {rho:>14.4f} {fmt_p(p_sp):>12} {sig_stars(p_sp):>5}")

    # ------------------------------------------------------------------
    subheader("5.2c  TX Power vs Throughput Correlation (iperf snapshots)")
    print(f"\n  {'Band':<6} {'N':>6} {'Spearman rho':>14} {'p-value':>12} {'Sig':>5}")
    print(f"  {'-'*6} {'-'*6} {'-'*14} {'-'*12} {'-'*5}")
    for band in ("5GHz", "6GHz"):
        txpow = tt[band]["tx_power"]
        tput  = tt[band]["tput"]
        if len(txpow) < 5:
            print(f"  {band:<6} insufficient data")
            continue
        rho, p_sp = spearman(txpow, tput)
        print(f"  {band:<6} {len(txpow):>6,} {rho:>14.4f} {fmt_p(p_sp):>12} {sig_stars(p_sp):>5}")


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
