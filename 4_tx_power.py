"""
4_tx_power.py  --  Section 4: Reported AP TX Power by Band and Space

Analyzes reported AP transmit power (`tx_power`) from SigCap data:
    4.1 Overall TX power by band (5GHz vs 6GHz)
    4.1b Game vs empty TX power comparison
    4.2 TX power by space/section (game campaigns)
    4.3 TX power by space × band (game campaigns)
    4.4 Spatial variability tests per band

Output: outputs/4_tx_power.txt
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
OUT_FILE = Path("outputs/4_tx_power.txt")

QUERY_TX = """
SELECT
    s.campaign_type,
    s.game_num,
    s.section,
    w.band,
    w.tx_power
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE w.tx_power > 0
  AND w.tx_power < 2147483647
    AND w.width = 20
  AND w.band IN ('5GHz', '6GHz')
"""


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(QUERY_TX).fetchall()
    con.close()

    if not rows:
        print("No valid tx_power data found.")
        return

    # ------------------------------------------------------------------
    # Organize data
    by_band = {"5GHz": [], "6GHz": []}
    by_campaign = {"game": [], "empty": []}
    by_campaign_band = {
        "game": {"5GHz": [], "6GHz": []},
        "empty": {"5GHz": [], "6GHz": []},
    }
    by_space = {}          # section -> [tx_power]
    by_space_band = {}     # section -> {band -> [tx_power]}

    for campaign, game_num, section, band, txp in rows:
        if band in by_band:
            by_band[band].append(txp)

        if campaign in by_campaign:
            by_campaign[campaign].append(txp)
            if band in by_campaign_band[campaign]:
                by_campaign_band[campaign][band].append(txp)

        # Space analysis is game-only to represent stadium sections
        if campaign != "game" or section is None:
            continue

        sec = str(section)
        by_space.setdefault(sec, []).append(txp)
        by_space_band.setdefault(sec, {}).setdefault(band, []).append(txp)

    band_arr = {b: np.array(v, dtype=float) for b, v in by_band.items()}
    sections = sorted(by_space.keys())

    header("SECTION 4 — REPORTED AP TX POWER BY BAND AND SPACE")
    print("  Metric: wifi_beacons.tx_power (dBm, reported by scan records)")
    print("  Valid rows filter: tx_power > 0 and tx_power < 2147483647 and width = 20 MHz")
    print("  Spatial analysis scope: campaign_type='game' sections")

    # ------------------------------------------------------------------
    subheader("4.1  Overall TX Power by Band")

    rows_out = []
    for band in ("5GHz", "6GHz"):
        arr = band_arr[band]
        d = descriptive(arr)
        rows_out.append([
            band,
            f"{d['n']:,}",
            fmt(d['median'], 2),
            fmt(d['mean'], 2),
            fmt(d['std'], 2),
            fmt(d['iqr'], 2),
            fmt(d['p10'], 2),
            fmt(d['p90'], 2),
            fmt(d['min'], 0),
            fmt(d['max'], 0),
        ])
    print()
    print_table(["Band", "N", "Median", "Mean", "Std", "IQR", "P10", "P90", "Min", "Max"], rows_out)

    a5 = band_arr["5GHz"]
    a6 = band_arr["6GHz"]
    obs, ci_lo, ci_hi = bootstrap_median_ci(a5, a6)
    u, p = mannwhitney(a5, a6)
    cd = cohens_d(a5, a6)
    cld = cliffs_delta(a5, a6)

    print(f"\n  Median diff (6GHz - 5GHz): {obs:+.2f} dBm")
    print(f"  Bootstrap 95% CI: [{ci_lo:+.2f}, {ci_hi:+.2f}] dBm")
    print(f"  Mann-Whitney U: {u:.1f}, p={fmt_p(p)} {sig_stars(p)}")
    print(f"  Cohen's d: {cd:+.4f} [{effect_label_d(cd)}]")
    print(f"  Cliff's delta: {cld:+.4f} [{effect_label_cliff(cld)}]")

    # ------------------------------------------------------------------
    subheader("4.1b  Game vs Empty TX Power Comparison")

    cmp_rows = []
    cmp_specs = [
        ("All bands", np.array(by_campaign["game"], dtype=float), np.array(by_campaign["empty"], dtype=float)),
        ("5GHz", np.array(by_campaign_band["game"]["5GHz"], dtype=float), np.array(by_campaign_band["empty"]["5GHz"], dtype=float)),
        ("6GHz", np.array(by_campaign_band["game"]["6GHz"], dtype=float), np.array(by_campaign_band["empty"]["6GHz"], dtype=float)),
    ]

    for label, arr_game, arr_empty in cmp_specs:
        if len(arr_game) == 0 or len(arr_empty) == 0:
            cmp_rows.append([label, len(arr_game), len(arr_empty), "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"])
            continue
        d_game = descriptive(arr_game)
        d_empty = descriptive(arr_empty)
        obs, ci_lo, ci_hi = bootstrap_median_ci(arr_empty, arr_game)
        u_ge, p_ge = mannwhitney(arr_empty, arr_game)
        cld_ge = cliffs_delta(arr_empty, arr_game)
        cmp_rows.append([
            label,
            f"{d_game['n']:,}",
            f"{d_empty['n']:,}",
            fmt(d_game['median'], 2),
            fmt(d_empty['median'], 2),
            f"{obs:+.2f}",
            f"[{ci_lo:+.2f}, {ci_hi:+.2f}]",
            fmt_p(p_ge),
            sig_stars(p_ge),
        ])

    print()
    print_table(
        ["Group", "Game N", "Empty N", "Game med", "Empty med", "Δ median (G-E)", "95% CI", "p", "Sig"],
        cmp_rows,
    )

    print("\n  Effect size note: positive Δ median means reported TX power is higher during games.")
    for label, arr_game, arr_empty in cmp_specs:
        if len(arr_game) == 0 or len(arr_empty) == 0:
            continue
        cld_ge = cliffs_delta(arr_empty, arr_game)
        print(f"  {label:<8} Cliff's δ (game vs empty): {cld_ge:+.4f} [{effect_label_cliff(cld_ge)}]")

    # ------------------------------------------------------------------
    subheader("4.2  TX Power by Space (game sections, all bands combined)")

    if not sections:
        print("\n  No game section data found.")
    else:
        rows_space = []
        for sec in sections:
            arr = np.array(by_space[sec], dtype=float)
            d = descriptive(arr)
            rows_space.append([
                sec,
                f"{d['n']:,}",
                fmt(d['median'], 2),
                fmt(d['mean'], 2),
                fmt(d['std'], 2),
                fmt(d['iqr'], 2),
            ])
        print()
        print_table(["Section", "N", "Median", "Mean", "Std", "IQR"], rows_space)

    # ------------------------------------------------------------------
    subheader("4.3  TX Power by Space × Band")

    if not sections:
        print("\n  No game section data found.")
    else:
        rows_sb = []
        for sec in sections:
            for band in ("5GHz", "6GHz"):
                arr = np.array(by_space_band.get(sec, {}).get(band, []), dtype=float)
                if len(arr) == 0:
                    rows_sb.append([sec, band, "0", "N/A", "N/A", "N/A", "N/A"])
                    continue
                d = descriptive(arr)
                rows_sb.append([
                    sec,
                    band,
                    f"{d['n']:,}",
                    fmt(d['median'], 2),
                    fmt(d['mean'], 2),
                    fmt(d['std'], 2),
                    fmt(d['iqr'], 2),
                ])
        print()
        print_table(["Section", "Band", "N", "Median", "Mean", "Std", "IQR"], rows_sb)

    # ------------------------------------------------------------------
    subheader("4.4  Spatial Variability Tests per Band")

    if not sections:
        print("\n  No game section data found.")
    else:
        for band in ("5GHz", "6GHz"):
            groups = []
            labels = []
            for sec in sections:
                arr = np.array(by_space_band.get(sec, {}).get(band, []), dtype=float)
                if len(arr) >= 3:
                    groups.append(arr)
                    labels.append(sec)

            print(f"\n  {band}:")
            if len(groups) < 2:
                print("    Not enough sections with data for Kruskal-Wallis.")
                continue

            h, p_kw = kruskal_wallis(*groups)
            print(f"    Kruskal-Wallis H={h:.3f}, p={fmt_p(p_kw)} {sig_stars(p_kw)}")

            # quick high/low ranking by median
            med_rank = sorted(
                [(sec, float(np.median(np.array(by_space_band.get(sec, {}).get(band, []), dtype=float))))
                 for sec in sections if len(by_space_band.get(sec, {}).get(band, [])) > 0],
                key=lambda x: x[1]
            )
            print("    Median TX power ranking (low -> high):")
            for sec, med in med_rank:
                print(f"      {sec:<4}  {med:+.2f} dBm")


def main():
    parser = argparse.ArgumentParser(description="TX power by band and space")
    parser.add_argument("--db", default=DB_DEFAULT,
                        help=f"DuckDB path (default: {DB_DEFAULT})")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with output_to(OUT_FILE):
        run(db_path)

    print(f"\nOutput written to: {OUT_FILE}")


if __name__ == "__main__":
    main()
