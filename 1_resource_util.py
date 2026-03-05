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

QUERY_LOAD_VS_EMPTY = """
SELECT
    s.campaign_type,
    w.band,
    w.ch_util
FROM wifi_beacons w
JOIN snapshots s ON w.snapshot_id = s.snapshot_id
WHERE w.ch_util >= 0
  AND w.band IN ('5GHz', '6GHz')
  AND s.campaign_type IN ('game', 'empty')
"""


def load(con) -> dict:
    rows = con.execute(QUERY_OVERALL).fetchall()
    data = {}   # band -> {game_num -> [ch_util values]}
    for band, ch_util, game_num in rows:
        data.setdefault(band, {}).setdefault(game_num, []).append(ch_util)
    return {b: {g: np.array(v) for g, v in games.items()}
            for b, games in data.items()}


def load_load_vs_empty(con) -> dict:
    """Returns {campaign_type: {band: np.array}}."""
    rows = con.execute(QUERY_LOAD_VS_EMPTY).fetchall()
    data = {}
    for campaign, band, val in rows:
        data.setdefault(campaign, {}).setdefault(band, []).append(val)
    return {c: {b: np.array(v) for b, v in bands.items()}
            for c, bands in data.items()}


def all_values(data: dict, band: str) -> np.ndarray:
    arrays = list(data[band].values())
    return np.concatenate(arrays)


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    data          = load(con)
    arr24         = np.array([r[0] for r in con.execute(QUERY_2_4).fetchall()])
    lve           = load_load_vs_empty(con)
    con.close()

    arr5 = all_values(data, "5GHz")
    arr6 = all_values(data, "6GHz")
    games = sorted((g for g in set(data["5GHz"].keys()) | set(data["6GHz"].keys())
                    if g is not None))

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

    # ------------------------------------------------------------------
    subheader("1.7  Utilization Under Load vs Empty Stadium")
    print("""
  Compares channel utilization observed during live games ('game' campaign)
  against the empty-stadium baseline ('empty' campaign) for 5 GHz and 6 GHz.
  The interaction effect tests whether the 6GHz advantage over 5GHz grows
  or shrinks under stadium load relative to idle conditions.
""")

    BANDS = ("5GHz", "6GHz")
    CONDITIONS = (("game", "Game-day (loaded)"), ("empty", "Empty stadium (idle)"))

    # --- Descriptive table ---
    desc_hdr = ["Condition", "Band", "N", "Median", "Mean", "Std", "IQR", "P25", "P75"]
    desc_tbl = []
    for ctype, clabel in CONDITIONS:
        for band in BANDS:
            arr = lve.get(ctype, {}).get(band, np.array([]))
            if len(arr) == 0:
                desc_tbl.append([clabel, band, 0] + ["N/A"] * 6)
                continue
            d = descriptive(arr)
            desc_tbl.append([
                clabel, band, f"{d['n']:,}",
                f"{d['median']:.4f}", f"{d['mean']:.4f}",
                f"{d['std']:.4f}",   f"{d['iqr']:.4f}",
                f"{d['p25']:.4f}",   f"{d['p75']:.4f}",
            ])
    print()
    print_table(desc_hdr, desc_tbl)

    # --- Per-band: game vs empty ---
    print()
    for band in BANDS:
        arr_game  = lve.get("game",  {}).get(band, np.array([]))
        arr_empty = lve.get("empty", {}).get(band, np.array([]))
        if len(arr_game) == 0 or len(arr_empty) == 0:
            print(f"  Insufficient data for {band}.")
            continue

        subheader(f"1.7  {band}: Game-Day vs Empty Stadium")
        m_game  = np.median(arr_game)
        m_empty = np.median(arr_empty)
        diff    = m_game - m_empty
        pct     = diff / m_empty * 100 if m_empty != 0 else float("nan")
        print(f"\n  Median utilization  — game-day : {m_game:.4f}")
        print(f"  Median utilization  — empty    : {m_empty:.4f}")
        print(f"  Absolute difference (game - empty) : {diff:+.4f} ({diff*100:+.2f} pp)")
        print(f"  Relative change                    : {pct:+.1f}%")

        obs, ci_lo, ci_hi = bootstrap_median_ci(arr_empty, arr_game)
        print(f"\n  Bootstrap 95% CI for median diff (game - empty):")
        print(f"    Observed : {obs:+.4f}")
        print(f"    CI       : [{ci_lo:+.4f}, {ci_hi:+.4f}]")
        zero_note = "Contains zero (NOT significant)" if ci_lo <= 0 <= ci_hi \
                    else "Does not contain zero (SIGNIFICANT)"
        print(f"    {zero_note}")

        u, p_mw = mannwhitney(arr_empty, arr_game)
        print(f"\n  Mann-Whitney U test:")
        print(f"    U = {u:.1f},  p = {fmt_p(p_mw)} {sig_stars(p_mw)}")

        cd  = cohens_d(arr_empty, arr_game)
        cld = cliffs_delta(arr_empty, arr_game)
        print(f"\n  Effect sizes (positive = game-day > empty):")
        print(f"    Cohen's d    : {cd:+.4f}  [{effect_label_d(cd)}]")
        print(f"    Cliff's delta: {cld:+.4f}  [{effect_label_cliff(cld)}]")

    # --- Interaction effect: (6GHz - 5GHz) gap under load vs idle ---
    subheader("1.7  Interaction Effect: 6GHz − 5GHz Gap Under Load vs Idle")
    print("""
  If 6 GHz channels absorb load more efficiently than 5 GHz, the utilization
  gap (6GHz - 5GHz) should widen under stadium load relative to empty.
  A more negative interaction means 6 GHz stays comparatively cleaner
  as crowd density increases — strengthening the density argument.
""")
    results = {}
    for ctype, clabel in CONDITIONS:
        a5 = lve.get(ctype, {}).get("5GHz", np.array([]))
        a6 = lve.get(ctype, {}).get("6GHz", np.array([]))
        if len(a5) == 0 or len(a6) == 0:
            print(f"  No data for {clabel}.")
            continue
        gap = np.median(a6) - np.median(a5)
        results[ctype] = (gap, clabel, a5, a6)
        print(f"  {clabel:<35}  6GHz median - 5GHz median = {gap:+.4f} ({gap*100:+.2f} pp)")

    if "game" in results and "empty" in results:
        gap_game  = results["game"][0]
        gap_empty = results["empty"][0]
        interaction = gap_game - gap_empty
        print(f"\n  Interaction (gap_game − gap_empty) = {interaction:+.4f} ({interaction*100:+.2f} pp)")
        if interaction < 0:
            print("  → The 6GHz advantage INCREASES under load: 6 GHz stays comparatively")
            print("    cleaner than 5 GHz as stadium density rises.")
        elif interaction > 0:
            print("  → The 6GHz advantage DECREASES under load: 6 GHz and 5 GHz converge")
            print("    as stadium density rises.")
        else:
            print("  → No change in the 6GHz vs 5GHz gap between conditions.")

        # Bootstrap CI for the interaction
        # interaction = (median(6G_game) - median(5G_game)) - (median(6G_empty) - median(5G_empty))
        # Bootstrap by resampling within each group independently
        rng = np.random.default_rng(42)
        n_boot = 5000
        boot_interactions = np.empty(n_boot)
        a5g = results["game"][2];  a6g = results["game"][3]
        a5e = results["empty"][2]; a6e = results["empty"][3]
        for i in range(n_boot):
            s5g = np.median(rng.choice(a5g, len(a5g), replace=True))
            s6g = np.median(rng.choice(a6g, len(a6g), replace=True))
            s5e = np.median(rng.choice(a5e, len(a5e), replace=True))
            s6e = np.median(rng.choice(a6e, len(a6e), replace=True))
            boot_interactions[i] = (s6g - s5g) - (s6e - s5e)
        ci_lo = float(np.percentile(boot_interactions, 2.5))
        ci_hi = float(np.percentile(boot_interactions, 97.5))
        print(f"\n  Bootstrap 95% CI for interaction: [{ci_lo:+.4f}, {ci_hi:+.4f}]")
        zero_note = "Contains zero (interaction NOT significant)" \
                    if ci_lo <= 0 <= ci_hi else "Does not contain zero (SIGNIFICANT interaction)"
        print(f"  {zero_note}")


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
