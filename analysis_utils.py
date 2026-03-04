"""
analysis_utils.py

Shared helpers for all statistical analysis scripts.
"""

import math
import sys
from pathlib import Path
from contextlib import contextmanager

import numpy as np
from scipy import stats

# ---------------------------------------------------------------------------
# Output routing: write to file and stdout simultaneously
# ---------------------------------------------------------------------------

class Tee:
    """Writes to both a file and the original stdout."""
    def __init__(self, filepath: Path):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(filepath, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, text):
        self._stdout.write(text)
        self._file.write(text)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        self._file.close()


@contextmanager
def output_to(path: Path):
    """Context manager that redirects print() to both stdout and a file."""
    tee = Tee(path)
    old = sys.stdout
    sys.stdout = tee
    try:
        yield
    finally:
        sys.stdout = old
        tee.close()


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

W = 78

def header(title: str) -> None:
    print()
    print("=" * W)
    print(f"  {title}")
    print("=" * W)


def subheader(title: str) -> None:
    fill = max(0, W - 5 - len(title))
    print(f"\n--- {title} " + "-" * fill)


def fmt(v, decimals=3, pct=False):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "N/A"
    if pct:
        return f"{v*100:.{decimals}f}%"
    return f"{v:.{decimals}f}"


def fmt_p(p):
    """Format a p-value with appropriate precision."""
    if p < 0.001:
        return f"{p:.2e}"
    return f"{p:.4f}"


def sig_stars(p):
    if p < 0.001: return "***"
    if p < 0.01:  return "**"
    if p < 0.05:  return "*"
    return "ns"


def print_table(headers, rows, indent=2):
    """Print a simple aligned table."""
    pad = " " * indent
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(str(v)))
    fmt_str = pad + "  ".join(f"{{:<{w}}}" for w in widths)
    sep = pad + "  ".join("-" * w for w in widths)
    print(fmt_str.format(*[str(h) for h in headers]))
    print(sep)
    for row in rows:
        print(fmt_str.format(*[str(v) for v in row]))


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def descriptive(arr: np.ndarray) -> dict:
    """Full descriptive statistics for a 1-D array."""
    return {
        "n":      len(arr),
        "mean":   float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std":    float(np.std(arr, ddof=1)),
        "var":    float(np.var(arr, ddof=1)),
        "min":    float(np.min(arr)),
        "p10":    float(np.percentile(arr, 10)),
        "p25":    float(np.percentile(arr, 25)),
        "p75":    float(np.percentile(arr, 75)),
        "p90":    float(np.percentile(arr, 90)),
        "iqr":    float(np.percentile(arr, 75) - np.percentile(arr, 25)),
        "max":    float(np.max(arr)),
        "cv":     float(np.std(arr, ddof=1) / np.mean(arr)) if np.mean(arr) != 0 else float("nan"),
    }


def bootstrap_median_ci(a: np.ndarray, b: np.ndarray,
                        n_boot: int = 5000, rng_seed: int = 42,
                        max_sample: int = 10_000) -> tuple:
    """
    Bootstrap 95% CI for (median(b) - median(a)).
    Returns (observed_diff, ci_low, ci_high).
    Arrays larger than max_sample are randomly subsampled before bootstrapping
    to keep runtime reasonable (median CI stable above ~5K observations).
    """
    rng = np.random.default_rng(rng_seed)
    observed = np.median(b) - np.median(a)
    a_boot = a if len(a) <= max_sample else rng.choice(a, size=max_sample, replace=False)
    b_boot = b if len(b) <= max_sample else rng.choice(b, size=max_sample, replace=False)
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        sa = rng.choice(a_boot, size=len(a_boot), replace=True)
        sb = rng.choice(b_boot, size=len(b_boot), replace=True)
        diffs[i] = np.median(sb) - np.median(sa)
    ci_low  = float(np.percentile(diffs, 2.5))
    ci_high = float(np.percentile(diffs, 97.5))
    return float(observed), ci_low, ci_high


def mannwhitney(a: np.ndarray, b: np.ndarray) -> tuple:
    """Mann-Whitney U test (two-sided). Returns (U, p)."""
    u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    return float(u), float(p)


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    """Pooled-SD Cohen's d  (positive = b > a)."""
    na, nb = len(a), len(b)
    pooled = math.sqrt(
        ((na - 1) * np.var(a, ddof=1) + (nb - 1) * np.var(b, ddof=1))
        / (na + nb - 2)
    )
    return (np.mean(b) - np.mean(a)) / pooled if pooled else float("nan")


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """Cliff's delta (non-parametric effect size, positive = b > a).
    
    Uses the Mann-Whitney U relationship: δ = 2*U/(na*nb) - 1
    where U counts pairs where b > a (O(n log n), not O(n*m)).
    """
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return float("nan")
    # mannwhitneyu(b, a) U-stat counts pairs where b[j] > a[i] (ties = 0.5)
    u_b_gt_a, _ = stats.mannwhitneyu(b, a, alternative="greater")
    return float(2 * u_b_gt_a / (na * nb) - 1)


def effect_label_d(d: float) -> str:
    d = abs(d)
    if d < 0.20:  return "negligible"
    if d < 0.50:  return "small"
    if d < 0.80:  return "medium"
    return "large"


def effect_label_cliff(d: float) -> str:
    d = abs(d)
    if d < 0.147: return "negligible"
    if d < 0.330: return "small"
    if d < 0.474: return "medium"
    return "large"


def kruskal_wallis(*groups) -> tuple:
    """Kruskal-Wallis H test across 2+ groups. Returns (H, p)."""
    h, p = stats.kruskal(*groups)
    return float(h), float(p)


def levene_test(a: np.ndarray, b: np.ndarray) -> tuple:
    """Levene test for equality of variances. Returns (W, p)."""
    w, p = stats.levene(a, b)
    return float(w), float(p)


def spearman(x: np.ndarray, y: np.ndarray) -> tuple:
    """Spearman rho and p-value. Returns (rho, p)."""
    r, p = stats.spearmanr(x, y)
    return float(r), float(p)


def theilsen(x: np.ndarray, y: np.ndarray) -> tuple:
    """Theil-Sen slope. Returns (slope, intercept, low_slope, high_slope)."""
    res = stats.theilslopes(y, x)
    return float(res.slope), float(res.intercept), float(res.low_slope), float(res.high_slope)


def normality(arr: np.ndarray) -> tuple:
    """Shapiro-Wilk test (subsample to 5000 if needed). Returns (W, p)."""
    sample = arr if len(arr) <= 5000 else np.random.choice(arr, 5000, replace=False)
    w, p = stats.shapiro(sample)
    return float(w), float(p)


def print_two_group_stats(label_a: str, label_b: str,
                          arr_a: np.ndarray, arr_b: np.ndarray,
                          unit: str = "") -> None:
    """
    Print a complete two-group comparison:
    descriptive stats, CI, Mann-Whitney, effect sizes.
    """
    da = descriptive(arr_a)
    db = descriptive(arr_b)

    print(f"\n  {'Statistic':<22} {label_a:>12}  {label_b:>12}  {'Diff (B-A)':>12}")
    print(f"  {'-'*22} {'-'*12}  {'-'*12}  {'-'*12}")
    for key, label in [
        ("n",      "N"),
        ("mean",   f"Mean {unit}"),
        ("median", f"Median {unit}"),
        ("std",    "Std Dev"),
        ("iqr",    "IQR"),
        ("p10",    "10th pct"),
        ("p90",    "90th pct"),
        ("cv",     "CV"),
    ]:
        va, vb = da[key], db[key]
        if key == "n":
            sa, sb, sd = str(int(va)), str(int(vb)), ""
        else:
            sa = fmt(va)
            sb = fmt(vb)
            sd = fmt(vb - va, decimals=3) if isinstance(va, float) else ""
        print(f"  {label:<22} {sa:>12}  {sb:>12}  {sd:>12}")

    obs, ci_lo, ci_hi = bootstrap_median_ci(arr_a, arr_b)
    print(f"\n  Bootstrap 95% CI for median difference (B - A):")
    print(f"    Observed diff : {obs:+.3f} {unit}")
    print(f"    CI            : [{ci_lo:+.3f}, {ci_hi:+.3f}] {unit}")
    contains_zero = "YES (no significant difference)" if ci_lo <= 0 <= ci_hi else "NO  (significant difference)"
    print(f"    Contains zero : {contains_zero}")

    u, p_mw = mannwhitney(arr_a, arr_b)
    print(f"\n  Mann-Whitney U test:")
    print(f"    U = {u:.1f},  p = {fmt_p(p_mw)} {sig_stars(p_mw)}")

    cd  = cohens_d(arr_a, arr_b)
    cld = cliffs_delta(arr_a, arr_b)
    print(f"\n  Effect sizes (positive = {label_b} > {label_a}):")
    print(f"    Cohen's d    : {cd:+.4f}  [{effect_label_d(cd)}]")
    print(f"    Cliff's delta: {cld:+.4f}  [{effect_label_cliff(cld)}]")
