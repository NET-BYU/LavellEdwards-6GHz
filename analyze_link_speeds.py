"""
analyze_link_speeds.py

Compares Wi-Fi link speeds (tx, rx) of connected access points recorded
during iperf test campaigns, split by 5 GHz vs. 6 GHz band.

Produces:
  - Descriptive statistics table
  - Normality test (Shapiro-Wilk)
  - Independent samples t-test
  - Mann-Whitney U test (non-parametric)
  - Cohen's d effect size
  - Cliff's delta effect size (non-parametric)
  - Text-based CDF and histogram for quick visual inspection

Usage:
  python analyze_link_speeds.py [--db stadium.duckdb]
"""

import argparse
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
from scipy import stats

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

W = 76

def header(title: str) -> None:
    print()
    print("=" * W)
    print(f"  {title}")
    print("=" * W)


def subheader(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, W - 5 - len(title)))


def fmt(v, decimals=2):
    if v is None:
        return "N/A"
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    return f"{v:.{decimals}f}"


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------

QUERY = """
SELECT
    w.tx_link_speed,
    w.rx_link_speed,
    w.band,
    s.game_num,
    s.round_num,
    s.uuid
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.connected = true
  AND s.campaign_type = 'iperf'
  AND w.band IN ('5GHz', '6GHz')
  AND w.tx_link_speed > 0
  AND w.rx_link_speed > 0
ORDER BY w.band, s.datetime_iso
"""


def fetch_data(db_path: Path) -> dict:
    """Return {'5GHz': {'tx': [...], 'rx': [...]}, '6GHz': {...}}"""
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(QUERY).fetchall()
    con.close()

    data = {'5GHz': {'tx': [], 'rx': []}, '6GHz': {'tx': [], 'rx': []}}
    for tx, rx, band, *_ in rows:
        if band in data:
            data[band]['tx'].append(tx)
            data[band]['rx'].append(rx)

    return {band: {k: np.array(v, dtype=float) for k, v in fields.items()}
            for band, fields in data.items()}


# ---------------------------------------------------------------------------
# Statistical tests
# ---------------------------------------------------------------------------

def descriptive(arr: np.ndarray) -> dict:
    return {
        'n':      len(arr),
        'mean':   np.mean(arr),
        'median': np.median(arr),
        'std':    np.std(arr, ddof=1),
        'min':    np.min(arr),
        'p25':    np.percentile(arr, 25),
        'p75':    np.percentile(arr, 75),
        'max':    np.max(arr),
    }


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    """Pooled-SD Cohen's d (positive = a > b)."""
    n_a, n_b = len(a), len(b)
    pooled_sd = math.sqrt(
        ((n_a - 1) * np.var(a, ddof=1) + (n_b - 1) * np.var(b, ddof=1))
        / (n_a + n_b - 2)
    )
    return (np.mean(a) - np.mean(b)) / pooled_sd if pooled_sd else float('nan')


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """
    Cliff's delta: non-parametric effect size.
    Range [-1, 1]. Magnitude guide: |d| < 0.147 negligible, < 0.33 small,
    < 0.474 medium, >= 0.474 large.
    """
    n_a, n_b = len(a), len(b)
    dominance = sum(1 if ai > bi else (-1 if ai < bi else 0)
                    for ai in a for bi in b)
    return dominance / (n_a * n_b)


def effect_label(d: float, kind: str = 'cohens') -> str:
    d = abs(d)
    if kind == 'cohens':
        if d < 0.2:   return "negligible"
        if d < 0.5:   return "small"
        if d < 0.8:   return "medium"
        return "large"
    else:  # cliff's
        if d < 0.147: return "negligible"
        if d < 0.330: return "small"
        if d < 0.474: return "medium"
        return "large"


# ---------------------------------------------------------------------------
# Text visualisations
# ---------------------------------------------------------------------------

def text_histogram(arr5: np.ndarray, arr6: np.ndarray,
                   label: str, bins: int = 12) -> None:
    """Side-by-side text histogram."""
    all_vals = np.concatenate([arr5, arr6])
    edges = np.linspace(all_vals.min(), all_vals.max(), bins + 1)
    counts5, _ = np.histogram(arr5, bins=edges)
    counts6, _ = np.histogram(arr6, bins=edges)
    max_count = max(counts5.max(), counts6.max())
    bar_width = 30

    print(f"\n  Histogram of {label} (Mbps)")
    print(f"  {'Range':>14}  {'5GHz count':>10}  {'':30}  {'6GHz count':>10}")
    print(f"  {'-'*14}  {'-'*10}  {'-'*30}  {'-'*10}")
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        bar5 = int(counts5[i] / max_count * bar_width) if max_count else 0
        bar6 = int(counts6[i] / max_count * bar_width) if max_count else 0
        range_str = f"{lo:6.0f}-{hi:6.0f}"
        print(f"  {range_str}  {counts5[i]:>10}  {'#' * bar5:<30}  {counts6[i]:>10}  {'#' * bar6}")


def text_cdf(arr5: np.ndarray, arr6: np.ndarray, label: str,
             steps: int = 15) -> None:
    """Approximate text CDF at evenly spaced percentile points."""
    percentiles = np.linspace(0, 100, steps)
    p5 = np.percentile(arr5, percentiles)
    p6 = np.percentile(arr6, percentiles)

    print(f"\n  CDF of {label} (Mbps) -- value at each percentile")
    print(f"  {'Percentile':>12}  {'5GHz':>10}  {'6GHz':>10}  {'Diff (6-5)':>12}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*12}")
    for pct, v5, v6 in zip(percentiles, p5, p6):
        print(f"  {pct:>11.0f}%  {v5:>10.1f}  {v6:>10.1f}  {v6 - v5:>+12.1f}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run_analysis(data: dict) -> None:

    for speed_type, type_label in [('tx', 'TX Link Speed (device -> AP)'),
                                   ('rx', 'RX Link Speed (AP -> device)')]:
        arr5 = data['5GHz'][speed_type]
        arr6 = data['6GHz'][speed_type]

        header(f"ANALYSIS: {type_label}")

        # --- Descriptive stats ---
        subheader("Descriptive Statistics")
        d5 = descriptive(arr5)
        d6 = descriptive(arr6)

        col = 14
        print(f"\n  {'Statistic':<16} {'5 GHz':>{col}}  {'6 GHz':>{col}}")
        print(f"  {'-'*16} {'-'*col}  {'-'*col}")
        for key, label in [
            ('n',      'N'),
            ('mean',   'Mean (Mbps)'),
            ('median', 'Median (Mbps)'),
            ('std',    'Std Dev'),
            ('min',    'Min'),
            ('p25',    '25th pct'),
            ('p75',    '75th pct'),
            ('max',    'Max'),
        ]:
            v5 = d5[key]
            v6 = d6[key]
            s5 = str(int(v5)) if key == 'n' else fmt(v5)
            s6 = str(int(v6)) if key == 'n' else fmt(v6)
            print(f"  {label:<16} {s5:>{col}}  {s6:>{col}}")

        # --- Normality ---
        subheader("Normality Test (Shapiro-Wilk, H0: data is normally distributed)")
        for arr, band in [(arr5, '5GHz'), (arr6, '6GHz')]:
            # Shapiro-Wilk is reliable up to ~5000 samples; subsample if larger
            sample = arr if len(arr) <= 5000 else np.random.choice(arr, 5000, replace=False)
            stat, p = stats.shapiro(sample)
            normal = "YES  (p >= 0.05)" if p >= 0.05 else "NO   (p <  0.05)"
            print(f"  {band}:  W = {stat:.4f},  p = {p:.4e}  ->  Normal? {normal}")

        # --- Independent samples t-test ---
        subheader("Independent Samples t-test (H0: equal means)")
        t_stat, t_p = stats.ttest_ind(arr5, arr6, equal_var=False)  # Welch's
        sig_t = "YES" if t_p < 0.05 else "NO"
        print(f"  Welch's t = {t_stat:.4f},  p = {t_p:.4e}")
        print(f"  Significant at alpha=0.05? {sig_t}")
        mean_diff = np.mean(arr6) - np.mean(arr5)
        ci = stats.t.interval(0.95, df=len(arr5) + len(arr6) - 2,
                              loc=mean_diff,
                              scale=stats.sem(np.concatenate([arr5, arr6])))
        print(f"  Mean difference (6GHz - 5GHz): {mean_diff:+.2f} Mbps")
        print(f"  95% CI of difference: [{ci[0]:+.2f}, {ci[1]:+.2f}] Mbps")

        # --- Mann-Whitney U ---
        subheader("Mann-Whitney U Test (H0: same distribution, non-parametric)")
        u_stat, u_p = stats.mannwhitneyu(arr5, arr6, alternative='two-sided')
        sig_u = "YES" if u_p < 0.05 else "NO"
        print(f"  U = {u_stat:.1f},  p = {u_p:.4e}")
        print(f"  Significant at alpha=0.05? {sig_u}")

        # --- Effect sizes ---
        subheader("Effect Sizes")
        cd = cohens_d(arr6, arr5)   # positive = 6GHz > 5GHz
        cld = cliffs_delta(arr6, arr5)
        print(f"  Cohen's d  (6GHz vs 5GHz): {cd:+.4f}  [{effect_label(cd, 'cohens')}]")
        print(f"  Cliff's delta              : {cld:+.4f}  [{effect_label(cld, 'cliffs')}]")
        print(f"  Interpretation: positive = 6 GHz tends to be higher")

        # --- Visualisations ---
        subheader("Distribution (text histogram)")
        text_histogram(arr5, arr6, type_label)

        subheader("Distribution (approximate CDF)")
        text_cdf(arr5, arr6, type_label)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare 5 GHz vs 6 GHz link speeds from iperf test campaigns."
    )
    parser.add_argument("--db", default="stadium.duckdb",
                        help="Path to the DuckDB database (default: stadium.duckdb)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Error: database not found: {db_path}")
        print("Run ingest_to_duckdb.py first.")
        sys.exit(1)

    print(f"Loading data from {db_path} ...")
    data = fetch_data(db_path)

    n5 = len(data['5GHz']['tx'])
    n6 = len(data['6GHz']['tx'])
    print(f"  5 GHz connected observations: {n5}")
    print(f"  6 GHz connected observations: {n6}")

    if n5 < 2 or n6 < 2:
        print("Not enough data in one or both groups to run statistics.")
        sys.exit(1)

    run_analysis(data)
    print()


if __name__ == "__main__":
    main()
