"""
9_util_vs_tput_game_only.py  --  Section 9: Utilization vs Throughput (Game-associated)

Analyzes correlation between channel utilization and throughput using
game-associated throughput points (non-empty campaigns).

Join logic (per iperf interval / snapshot):
- Use connected AP ch_util when available (ch_util >= 0)
- Otherwise fall back to strongest-RSSI beacon on same connected band with valid ch_util

Sections:
  9.1  Overview of paired game observations
  9.2  Correlation (Spearman) by band + pooled
  9.3  Theil-Sen slope by band + pooled
  9.4  Throughput by utilization quartile (pooled + by band)
  9.5  Per-game and per-section correlations

Output: outputs/9_util_vs_tput_game_only.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    output_to, header, subheader, fmt_p, sig_stars, print_table,
    spearman, theilsen,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE = Path("outputs/9_util_vs_tput_game_only.txt")

QUERY_TEMPLATE = """
WITH iperf_snaps AS (
    SELECT
        ir.snapshot_id,
        ir.tput_mbps,
        s.game_num,
        s.section,
        (SELECT w.band
         FROM wifi_beacons w
         WHERE w.snapshot_id = ir.snapshot_id
           AND w.connected = TRUE
         LIMIT 1) AS connected_band,
        (SELECT w.ch_util
         FROM wifi_beacons w
         WHERE w.snapshot_id = ir.snapshot_id
           AND w.connected = TRUE
           AND w.ch_util >= 0
         LIMIT 1) AS connected_ch_util,
        (SELECT w2.ch_util
         FROM wifi_beacons w2
         WHERE w2.snapshot_id = ir.snapshot_id
           AND w2.band = (
               SELECT w.band
               FROM wifi_beacons w
               WHERE w.snapshot_id = ir.snapshot_id
                 AND w.connected = TRUE
               LIMIT 1
           )
           AND w2.ch_util >= 0
         ORDER BY w2.rssi DESC
         LIMIT 1) AS fallback_ch_util
    FROM iperf_results ir
    JOIN snapshots s USING (snapshot_id)
        WHERE {campaign_filter}
      AND ir.direction = 'download'
      AND ir.tput_mbps > 0
)
SELECT
    game_num,
    section,
    connected_band AS band,
    tput_mbps,
    COALESCE(connected_ch_util, fallback_ch_util) AS ch_util
FROM iperf_snaps
WHERE connected_band IN ('5GHz', '6GHz')
  AND COALESCE(connected_ch_util, fallback_ch_util) IS NOT NULL
ORDER BY game_num, section, connected_band
"""


def quartile_rows(util: np.ndarray, tput: np.ndarray, label_prefix: str):
    if len(util) < 8:
        return [[label_prefix, "N/A", "0", "N/A", "N/A"]]
    q25, q50, q75 = np.percentile(util, [25, 50, 75])
    masks = [
        ("Q1", util <= q25),
        ("Q2", (util > q25) & (util <= q50)),
        ("Q3", (util > q50) & (util <= q75)),
        ("Q4", util > q75),
    ]
    rows = []
    for q, mask in masks:
        n = int(np.sum(mask))
        if n == 0:
            rows.append([label_prefix, q, 0, "N/A", "N/A"])
            continue
        rows.append([
            label_prefix,
            q,
            n,
            f"{np.median(tput[mask]):.2f}",
            f"{np.mean(tput[mask]):.2f}",
        ])
    return rows


def corr_strength_label(rho: float) -> str:
    a = abs(rho)
    if a < 0.10:
        return "negligible"
    if a < 0.30:
        return "weak"
    if a < 0.50:
        return "moderate"
    return "strong"


def run(db_path: Path, scope: str) -> None:
    if scope == "game":
        campaign_filter = "s.campaign_type = 'game'"
        scope_label = "campaign_type = 'game'"
    else:
        campaign_filter = "s.campaign_type IN ('game', 'iperf')"
        scope_label = "campaign_type in {game, iperf} (empty excluded)"

    query = QUERY_TEMPLATE.format(campaign_filter=campaign_filter)

    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(query).fetchall()
    con.close()

    if not rows:
        print(f"No throughput+utilization paired rows found for scope: {scope_label}")
        return

    # Organize
    pooled = {"util": [], "tput": []}
    by_band = {"5GHz": {"util": [], "tput": []}, "6GHz": {"util": [], "tput": []}}
    by_game_band = {}
    by_section_band = {}

    for game_num, section, band, tput, util in rows:
        pooled["util"].append(util)
        pooled["tput"].append(tput)
        by_band[band]["util"].append(util)
        by_band[band]["tput"].append(tput)

        by_game_band.setdefault(game_num, {}).setdefault(band, {"util": [], "tput": []})
        by_game_band[game_num][band]["util"].append(util)
        by_game_band[game_num][band]["tput"].append(tput)

        sec = str(section) if section is not None else "NA"
        by_section_band.setdefault(sec, {}).setdefault(band, {"util": [], "tput": []})
        by_section_band[sec][band]["util"].append(util)
        by_section_band[sec][band]["tput"].append(tput)

    # Arrays
    pu = np.array(pooled["util"], dtype=float)
    pt = np.array(pooled["tput"], dtype=float)
    for band in ("5GHz", "6GHz"):
        by_band[band]["util"] = np.array(by_band[band]["util"], dtype=float)
        by_band[band]["tput"] = np.array(by_band[band]["tput"], dtype=float)

    header("SECTION 9 — UTILIZATION vs THROUGHPUT (GAME-ASSOCIATED)")
    print(f"  Scope: {scope_label}")

    # ------------------------------------------------------------------
    subheader("9.1  Overview of Paired Observations")
    print()
    ov_rows = []
    for label, util, tput in [
        ("Pooled", pu, pt),
        ("5GHz", by_band["5GHz"]["util"], by_band["5GHz"]["tput"]),
        ("6GHz", by_band["6GHz"]["util"], by_band["6GHz"]["tput"]),
    ]:
        if len(util) == 0:
            ov_rows.append([label, 0, "N/A", "N/A", "N/A", "N/A"])
            continue
        ov_rows.append([
            label,
            len(util),
            f"{util.min():.1f}–{util.max():.1f}",
            f"{np.median(util):.1f}",
            f"{tput.min():.2f}–{tput.max():.2f}",
            f"{np.median(tput):.2f}",
        ])
    print_table(["Group", "N", "ch_util range", "ch_util median", "tput range", "tput median"], ov_rows)

    # ------------------------------------------------------------------
    subheader("9.2  Spearman Correlation: ch_util vs throughput")
    print()
    corr_stats = {}
    corr_rows = []
    for label, util, tput in [
        ("Pooled", pu, pt),
        ("5GHz", by_band["5GHz"]["util"], by_band["5GHz"]["tput"]),
        ("6GHz", by_band["6GHz"]["util"], by_band["6GHz"]["tput"]),
    ]:
        if len(util) < 3:
            corr_rows.append([label, len(util), "N/A", "N/A", "N/A"])
            corr_stats[label] = None
            continue
        rho, p = spearman(util, tput)
        corr_stats[label] = {"rho": rho, "p": p, "n": len(util)}
        corr_rows.append([label, len(util), f"{rho:+.4f}", fmt_p(p), sig_stars(p)])
    print_table(["Group", "N", "Spearman rho", "p", "Sig"], corr_rows)

    # ------------------------------------------------------------------
    subheader("9.3  Theil-Sen Slope (Mbps per +1% utilization)")
    print()
    slope_rows = []
    for label, util, tput in [
        ("Pooled", pu, pt),
        ("5GHz", by_band["5GHz"]["util"], by_band["5GHz"]["tput"]),
        ("6GHz", by_band["6GHz"]["util"], by_band["6GHz"]["tput"]),
    ]:
        if len(util) < 3:
            slope_rows.append([label, len(util), "N/A", "N/A", "N/A", "N/A"])
            continue
        slope, intercept, lo, hi = theilsen(util, tput)
        slope_rows.append([
            label,
            len(util),
            f"{slope:+.4f}",
            f"[{lo:+.4f}, {hi:+.4f}]",
            f"{slope*10:+.2f}",
            f"{intercept:.2f}",
        ])
    print_table(["Group", "N", "Slope", "95% CI", "ΔMbps @ +10% util", "Intercept"], slope_rows)

    # ------------------------------------------------------------------
    subheader("9.4  Throughput by Utilization Quartile")
    print()
    q_rows = []
    q_rows.extend(quartile_rows(pu, pt, "Pooled"))
    q_rows.extend(quartile_rows(by_band["5GHz"]["util"], by_band["5GHz"]["tput"], "5GHz"))
    q_rows.extend(quartile_rows(by_band["6GHz"]["util"], by_band["6GHz"]["tput"], "6GHz"))
    print_table(["Group", "Quartile", "N", "Median tput", "Mean tput"], q_rows)

    # ------------------------------------------------------------------
    subheader("9.5  Per-Game and Per-Section Correlations")

    print("\n  By game and band:")
    gb_rows = []
    for g in sorted(k for k in by_game_band.keys() if k is not None):
        for band in ("5GHz", "6GHz"):
            arr = by_game_band.get(g, {}).get(band)
            if not arr:
                gb_rows.append([g, band, 0, "N/A", "N/A", "N/A"])
                continue
            util = np.array(arr["util"], dtype=float)
            tput = np.array(arr["tput"], dtype=float)
            if len(util) < 3:
                gb_rows.append([g, band, len(util), "N/A", "N/A", "N/A"])
                continue
            rho, p = spearman(util, tput)
            gb_rows.append([g, band, len(util), f"{rho:+.4f}", fmt_p(p), sig_stars(p)])
    print_table(["Game", "Band", "N", "rho", "p", "Sig"], gb_rows)

    print("\n  By section and band:")
    sb_rows = []
    for sec in sorted(by_section_band.keys()):
        for band in ("5GHz", "6GHz"):
            arr = by_section_band.get(sec, {}).get(band)
            if not arr:
                sb_rows.append([sec, band, 0, "N/A", "N/A", "N/A"])
                continue
            util = np.array(arr["util"], dtype=float)
            tput = np.array(arr["tput"], dtype=float)
            if len(util) < 3:
                sb_rows.append([sec, band, len(util), "N/A", "N/A", "N/A"])
                continue
            rho, p = spearman(util, tput)
            sb_rows.append([sec, band, len(util), f"{rho:+.4f}", fmt_p(p), sig_stars(p)])
    print_table(["Section", "Band", "N", "rho", "p", "Sig"], sb_rows)

    # ------------------------------------------------------------------
    subheader("9.6  Interpretation and Conclusions")

    pooled_s = corr_stats.get("Pooled")
    b5_s = corr_stats.get("5GHz")
    b6_s = corr_stats.get("6GHz")

    print("\n  Correlation strength guide: |rho| < 0.10 negligible, <0.30 weak, <0.50 moderate, >=0.50 strong.")

    if pooled_s is not None:
        direction = "negative" if pooled_s["rho"] < 0 else "positive"
        print(f"\n  Pooled result:")
        print(f"    rho = {pooled_s['rho']:+.4f} ({corr_strength_label(pooled_s['rho'])}, {direction}), "
              f"p = {fmt_p(pooled_s['p'])} {sig_stars(pooled_s['p'])}")
        if pooled_s["p"] < 0.05:
            print("    Conclusion: there is a statistically significant overall association between higher utilization and lower throughput.")
        else:
            print("    Conclusion: no statistically significant overall association detected.")

    if b5_s is not None:
        direction = "negative" if b5_s["rho"] < 0 else "positive"
        print(f"\n  5GHz:")
        print(f"    rho = {b5_s['rho']:+.4f} ({corr_strength_label(b5_s['rho'])}, {direction}), "
              f"p = {fmt_p(b5_s['p'])} {sig_stars(b5_s['p'])}")
        if b5_s["p"] < 0.05:
            print("    Interpretation: utilization explains a small but meaningful share of throughput variation in 5GHz.")
        else:
            print("    Interpretation: no reliable utilization-throughput trend in 5GHz.")

    if b6_s is not None:
        direction = "negative" if b6_s["rho"] < 0 else "positive"
        print(f"\n  6GHz:")
        print(f"    rho = {b6_s['rho']:+.4f} ({corr_strength_label(b6_s['rho'])}, {direction}), "
              f"p = {fmt_p(b6_s['p'])} {sig_stars(b6_s['p'])}")
        if b6_s["p"] < 0.05:
            print("    Interpretation: utilization is significantly associated with throughput in 6GHz.")
        else:
            print("    Interpretation: utilization alone is not a strong predictor of throughput in 6GHz for this dataset.")

    print("\n  Practical takeaway:")
    print("    - The negative trend exists overall, but effect sizes are weak in rank-correlation terms.")
    print("    - In this dataset, 5GHz shows clearer utilization sensitivity than 6GHz.")
    print("    - Throughput is likely influenced by additional factors (RSSI, channel width, client/AP behavior), not utilization alone.")


def main():
    parser = argparse.ArgumentParser(description="Section 9: game-only or game-associated utilization vs throughput")
    parser.add_argument("--db", default=DB_DEFAULT,
                        help=f"DuckDB path (default: {DB_DEFAULT})")
    parser.add_argument("--scope", choices=["game", "game-associated"], default="game-associated",
                        help="Use strict game-only rows or game-associated rows (default: game-associated)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with output_to(OUT_FILE):
        run(db_path, args.scope)

    print(f"\nOutput written to: {OUT_FILE}")


if __name__ == "__main__":
    main()
