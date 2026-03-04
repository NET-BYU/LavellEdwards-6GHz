"""
12_device_heterogeneity.py  --  Section 12: Device Heterogeneity

Examines whether differences in throughput and RSSI are explained by
device type rather than (or in addition to) band differences.

12.1  Throughput by device (iperf download, all bands + per band)
12.2  RSSI by device (connected AP beacons)
12.3  Kruskal-Wallis across devices (throughput, RSSI)
12.4  Pairwise post-hoc comparisons (if KW significant, Bonferroni)
12.5  Band assignment per device (are some devices always on one band?)
12.6  Confounding check: does device explain the 5GHz vs 6GHz difference?

Output: outputs/12_device_heterogeneity.txt
"""

import argparse
import sys
from itertools import combinations
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    output_to, header, subheader, fmt_p, sig_stars, print_table,
    descriptive, bootstrap_median_ci, mannwhitney, cohens_d, cliffs_delta,
    effect_label_d, effect_label_cliff, kruskal_wallis,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE   = Path("outputs/12_device_heterogeneity.txt")

QUERY_TPUT = """
SELECT s.device_name,
       (SELECT w.band FROM wifi_beacons w
        WHERE w.snapshot_id = ir.snapshot_id AND w.connected = TRUE LIMIT 1) AS band,
       ir.tput_mbps
FROM iperf_results ir
JOIN snapshots s USING (snapshot_id)
WHERE ir.direction = 'download'
  AND ir.tput_mbps > 0
  AND s.device_name IS NOT NULL
"""

# Connected AP beacons for RSSI
QUERY_RSSI = """
SELECT s.device_name,
       w.band,
       w.rssi
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.connected = TRUE
  AND w.rssi IS NOT NULL
  AND w.band IN ('5GHz', '6GHz')
  AND s.device_name IS NOT NULL
"""

# Snapshot count per device per band (for band assignment analysis)
QUERY_BAND_ASSIGN = """
SELECT s.device_name,
       w.band,
       COUNT(DISTINCT s.snapshot_id) AS snap_count
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.connected = TRUE
  AND w.band IN ('5GHz', '6GHz')
  AND s.device_name IS NOT NULL
GROUP BY s.device_name, w.band
ORDER BY s.device_name, w.band
"""


def pairwise_posthoc(data: dict, label: str, metric: str) -> None:
    devices = sorted(data.keys())
    pairs = list(combinations(devices, 2))
    if not pairs:
        return
    alpha_bonf = 0.05 / len(pairs)
    print(f"\n  Pairwise {metric} (Bonferroni alpha = {alpha_bonf:.4f}):")
    print(f"  {'Device pair':<30} {'U':>10} {'p':>12} {'Sig':>5} {'Cliff d':>8}")
    print(f"  {'-'*30} {'-'*10} {'-'*12} {'-'*5} {'-'*8}")
    for d1, d2 in pairs:
        a1 = data[d1]; a2 = data[d2]
        if len(a1) < 3 or len(a2) < 3:
            print(f"  {d1} vs {d2:<15}  (too few data)"); continue
        u, p = mannwhitney(a1, a2)
        cld = cliffs_delta(a1, a2)
        sig = "***" if p < alpha_bonf else ("*" if p < 0.05 else "ns")
        pair_label = f"{d1} vs {d2}"
        print(f"  {pair_label:<30} {u:>10.1f} {fmt_p(p):>12} {sig:>5} {cld:>+8.4f}")


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    tput_rows   = con.execute(QUERY_TPUT).fetchall()
    rssi_rows   = con.execute(QUERY_RSSI).fetchall()
    band_rows   = con.execute(QUERY_BAND_ASSIGN).fetchall()
    con.close()

    # Organize throughput
    tput_dev: dict  = {}   # device → [tput, ...]
    tput_db: dict   = {}   # (device, band) → [tput, ...]
    for device, band, tput in tput_rows:
        tput_dev.setdefault(device, []).append(tput)
        if band in ("5GHz", "6GHz"):
            tput_db.setdefault((device, band), []).append(tput)
    tput_dev = {k: np.array(v) for k, v in tput_dev.items()}
    tput_db  = {k: np.array(v) for k, v in tput_db.items()}

    # Organize RSSI
    rssi_dev: dict = {}
    rssi_db: dict  = {}
    for device, band, rssi in rssi_rows:
        rssi_dev.setdefault(device, []).append(rssi)
        rssi_db.setdefault((device, band), []).append(rssi)
    rssi_dev = {k: np.array(v) for k, v in rssi_dev.items()}
    rssi_db  = {k: np.array(v) for k, v in rssi_db.items()}

    # Band assignment
    band_assign: dict = {}   # device → {band: count}
    for device, band, count in band_rows:
        band_assign.setdefault(device, {})[band] = count

    devices = sorted(set(tput_dev.keys()) | set(rssi_dev.keys()))

    header("SECTION 12 — DEVICE HETEROGENEITY")

    # ------------------------------------------------------------------
    subheader("12.1  Throughput by Device (all bands combined)")
    print()
    rows_out = []
    for dev in devices:
        arr = tput_dev.get(dev, np.array([]))
        if len(arr) == 0:
            rows_out.append([dev, "0", "–", "–", "–", "–"])
            continue
        d = descriptive(arr)
        rows_out.append([dev, f"{d['n']:,}", f"{d['median']:.2f}",
                         f"{d['mean']:.2f}", f"{d['std']:.2f}", f"{d['iqr']:.2f}"])
    print_table(["Device", "N", "Median (Mbps)", "Mean", "Std", "IQR"], rows_out)

    if len([d for d in devices if len(tput_dev.get(d, [])) >= 3]) >= 3:
        groups = [tput_dev[d] for d in devices if len(tput_dev.get(d, [])) >= 3]
        h, p_kw = kruskal_wallis(*groups)
        print(f"\n  Kruskal-Wallis H = {h:.3f},  p = {fmt_p(p_kw)} {sig_stars(p_kw)}")
        if p_kw < 0.05:
            print(f"  Significant differences across devices — running pairwise post-hoc:")
            pairwise_posthoc({d: tput_dev[d] for d in devices if len(tput_dev.get(d, [])) >= 3},
                             "Throughput", "throughput (all bands)")
        else:
            print(f"  No significant differences in throughput across devices")

    # ------------------------------------------------------------------
    subheader("12.2  Throughput by Device × Band")
    print()
    header_row = ["Device", "Band", "N", "Median (Mbps)", "Mean", "Std", "IQR"]
    rows_out = []
    for dev in devices:
        for band in ("5GHz", "6GHz"):
            arr = tput_db.get((dev, band), np.array([]))
            if len(arr) == 0:
                rows_out.append([dev, band, "0", "–", "–", "–", "–"])
                continue
            d = descriptive(arr)
            rows_out.append([dev, band, f"{d['n']:,}", f"{d['median']:.2f}",
                             f"{d['mean']:.2f}", f"{d['std']:.2f}", f"{d['iqr']:.2f}"])
    print_table(header_row, rows_out)

    # ------------------------------------------------------------------
    subheader("12.3  RSSI by Device (connected AP)")
    print()
    rows_out = []
    for dev in devices:
        arr = rssi_dev.get(dev, np.array([]))
        if len(arr) == 0:
            rows_out.append([dev, "0", "–", "–", "–", "–"])
            continue
        d = descriptive(arr)
        rows_out.append([dev, f"{d['n']:,}", f"{d['median']:.2f}",
                         f"{d['mean']:.2f}", f"{d['std']:.2f}", f"{d['iqr']:.2f}"])
    print_table(["Device", "N", "Median (dBm)", "Mean", "Std", "IQR"], rows_out)

    if len([d for d in devices if len(rssi_dev.get(d, [])) >= 3]) >= 3:
        groups = [rssi_dev[d] for d in devices if len(rssi_dev.get(d, [])) >= 3]
        h, p_kw = kruskal_wallis(*groups)
        print(f"\n  Kruskal-Wallis H = {h:.3f},  p = {fmt_p(p_kw)} {sig_stars(p_kw)}")
        if p_kw < 0.05:
            print(f"  Significant RSSI differences across devices — pairwise post-hoc:")
            pairwise_posthoc({d: rssi_dev[d] for d in devices if len(rssi_dev.get(d, [])) >= 3},
                             "RSSI", "RSSI (dBm)")
        else:
            print(f"  No significant RSSI differences across devices")

    # ------------------------------------------------------------------
    subheader("12.4  RSSI by Device × Band")
    print()
    rows_out = []
    for dev in devices:
        for band in ("5GHz", "6GHz"):
            arr = rssi_db.get((dev, band), np.array([]))
            if len(arr) == 0:
                rows_out.append([dev, band, "0", "–", "–", "–", "–"])
                continue
            d = descriptive(arr)
            rows_out.append([dev, band, f"{d['n']:,}", f"{d['median']:.2f}",
                             f"{d['mean']:.2f}", f"{d['std']:.2f}", f"{d['iqr']:.2f}"])
    print_table(["Device", "Band", "N", "Median (dBm)", "Mean", "Std", "IQR"], rows_out)

    # ------------------------------------------------------------------
    subheader("12.5  Band Assignment per Device")
    print(f"\n  How often was each device connected via 5GHz vs 6GHz?\n")
    print(f"  {'Device':<16} {'5GHz snaps':>12} {'6GHz snaps':>12} {'% 6GHz':>10}")
    print(f"  {'-'*16} {'-'*12} {'-'*12} {'-'*10}")
    for dev in devices:
        ba = band_assign.get(dev, {})
        n5 = ba.get("5GHz", 0)
        n6 = ba.get("6GHz", 0)
        total = n5 + n6
        pct6 = (n6 / total * 100) if total > 0 else 0.0
        print(f"  {dev:<16} {n5:>12,} {n6:>12,} {pct6:>9.1f}%")

    # ------------------------------------------------------------------
    subheader("12.6  5GHz vs 6GHz Effect: Does Device Confound?")
    print(f"""
  For each device, compute its own 5GHz vs 6GHz median throughput difference.
  Consistent direction across all devices = effect is robust beyond device type.
""")
    print(f"  {'Device':<16} {'5GHz Med':>10} {'6GHz Med':>10} {'Δ (6-5)':>10} {'N5':>6} {'N6':>6}")
    print(f"  {'-'*16} {'-'*10} {'-'*10} {'-'*10} {'-'*6} {'-'*6}")
    directions = []
    for dev in devices:
        a5 = tput_db.get((dev, "5GHz"), np.array([]))
        a6 = tput_db.get((dev, "6GHz"), np.array([]))
        if len(a5) == 0 and len(a6) == 0:
            print(f"  {dev:<16}  (no iperf data)"); continue
        m5 = np.median(a5) if len(a5) > 0 else float("nan")
        m6 = np.median(a6) if len(a6) > 0 else float("nan")
        if np.isnan(m5) or np.isnan(m6):
            delta_str = "N/A (no data for one band)"
            print(f"  {dev:<16} {str(round(m5, 2) if not np.isnan(m5) else '–'):>10} "
                  f"{str(round(m6, 2) if not np.isnan(m6) else '–'):>10} "
                  f"{'–':>10} {len(a5):>6,} {len(a6):>6,}")
        else:
            delta = m6 - m5
            directions.append(delta)
            print(f"  {dev:<16} {m5:>10.2f} {m6:>10.2f} {delta:>+10.2f} "
                  f"{len(a5):>6,} {len(a6):>6,}")

    if len(directions) >= 2:
        all_positive = all(d > 0 for d in directions)
        all_negative = all(d < 0 for d in directions)
        if all_positive:
            print(f"\n  >> 6GHz throughput advantage is CONSISTENT across all {len(directions)} devices (all positive deltas)")
        elif all_negative:
            print(f"\n  >> 5GHz throughput advantage is CONSISTENT across all {len(directions)} devices (all negative deltas)")
        else:
            pos = sum(1 for d in directions if d > 0)
            neg = len(directions) - pos
            print(f"\n  >> Mixed results: {pos} devices favour 6GHz, {neg} favour 5GHz")
            print(f"     Effect may be confounded by device capability or band assignment")

    # RSSI confounding check
    print(f"""
  RSSI confounding check (connected AP, per device):
""")
    print(f"  {'Device':<16} {'5GHz Med RSSI':>14} {'6GHz Med RSSI':>14} {'Δ RSSI':>8} {'N5':>6} {'N6':>6}")
    print(f"  {'-'*16} {'-'*14} {'-'*14} {'-'*8} {'-'*6} {'-'*6}")
    rssi_dirs = []
    for dev in devices:
        a5 = rssi_db.get((dev, "5GHz"), np.array([]))
        a6 = rssi_db.get((dev, "6GHz"), np.array([]))
        m5 = np.median(a5) if len(a5) > 0 else float("nan")
        m6 = np.median(a6) if len(a6) > 0 else float("nan")
        if np.isnan(m5) or np.isnan(m6):
            print(f"  {dev:<16} {'–':>14} {'–':>14} {'–':>8} {len(a5):>6,} {len(a6):>6,}")
        else:
            delta = m6 - m5
            rssi_dirs.append(delta)
            print(f"  {dev:<16} {m5:>14.2f} {m6:>14.2f} {delta:>+8.2f} {len(a5):>6,} {len(a6):>6,}")
    if len(rssi_dirs) >= 2:
        all_pos = all(d > 0 for d in rssi_dirs)
        all_neg = all(d < 0 for d in rssi_dirs)
        if all_pos:
            print(f"\n  >> 6GHz RSSI advantage consistent across all {len(rssi_dirs)} devices")
        elif all_neg:
            print(f"\n  >> 5GHz RSSI advantage consistent across all {len(rssi_dirs)} devices")
        else:
            pos = sum(1 for d in rssi_dirs if d > 0)
            print(f"\n  >> Mixed RSSI results: {pos}/{len(rssi_dirs)} devices show 6GHz advantage")


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
