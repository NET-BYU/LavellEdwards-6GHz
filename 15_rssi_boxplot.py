"""
15_rssi_boxplot.py

Create a box plot of RSSI values with one box for 5 GHz and one for 6 GHz.
Default scope is game snapshots only.

Output:
  outputs/15_rssi_boxplot.png

Usage:
  python 15_rssi_boxplot.py [--db stadium.duckdb] [--scope game|all]
"""

import argparse
import sys
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

DB_DEFAULT = "stadium.duckdb"
OUT_PNG = Path("outputs/15_rssi_boxplot.png")


def load_rssi(db_path: Path, scope: str) -> tuple[np.ndarray, np.ndarray]:
    campaign_clause = "s.campaign_type = 'game'" if scope == "game" else "1=1"
    query = f"""
    SELECT w.band, w.rssi
    FROM wifi_beacons w
    JOIN snapshots s USING (snapshot_id)
    WHERE {campaign_clause}
      AND w.band IN ('5GHz', '6GHz')
      AND w.rssi IS NOT NULL
    """

    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(query).fetchall()
    con.close()

    rssi_5 = [rssi for band, rssi in rows if band == "5GHz"]
    rssi_6 = [rssi for band, rssi in rows if band == "6GHz"]

    return np.array(rssi_5, dtype=float), np.array(rssi_6, dtype=float)


def make_plot(rssi_5: np.ndarray, rssi_6: np.ndarray, out_path: Path, scope: str) -> None:
    # White background style with blue/orange colors
    plt.style.use("default")

    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    colors = ["tab:blue", "tab:orange"]
    bp = ax.boxplot(
        [rssi_5, rssi_6],
        tick_labels=["5 GHz", "6 GHz"],
        widths=0.55,
        showfliers=False,
        patch_artist=True,
        medianprops={"color": "black", "linewidth": 1.8},
        whiskerprops={"linewidth": 1.5},
        capprops={"linewidth": 1.5},
    )

    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.45)
        patch.set_edgecolor(color)
        patch.set_linewidth(1.8)

    for whisker, color in zip(bp["whiskers"], [colors[0], colors[0], colors[1], colors[1]]):
        whisker.set_color(color)
    for cap, color in zip(bp["caps"], [colors[0], colors[0], colors[1], colors[1]]):
        cap.set_color(color)

    ax.set_xlabel("Band", fontsize=18)
    ax.set_ylabel("RSSI (dBm)", fontsize=18)
    title_scope = "Game Data" if scope == "game" else "All Data"
    # ax.set_title(f"RSSI Distribution by Band ({title_scope})", fontsize=18)
    ax.tick_params(axis="both", labelsize=14)

    legend_handles = [
        Patch(facecolor="tab:blue", edgecolor="tab:blue", alpha=0.45, label="5 GHz"),
        Patch(facecolor="tab:orange", edgecolor="tab:orange", alpha=0.45, label="6 GHz"),
    ]
    # ax.legend(handles=legend_handles, loc="lower right", frameon=True, fontsize=14)

    ax.grid(axis="y", color="#D0D0D0", alpha=0.7)
    fig.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Make 5GHz vs 6GHz RSSI box plot")
    parser.add_argument("--db", default=DB_DEFAULT, help=f"DuckDB path (default: {DB_DEFAULT})")
    parser.add_argument("--scope", choices=["game", "all"], default="game",
                        help="Data scope: game-only (default) or all campaigns")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    rssi_5, rssi_6 = load_rssi(db_path, args.scope)
    if len(rssi_5) == 0 or len(rssi_6) == 0:
        print("Not enough RSSI data to draw both 5 GHz and 6 GHz boxes.")
        sys.exit(1)

    make_plot(rssi_5, rssi_6, OUT_PNG, args.scope)

    print(f"Saved box plot to: {OUT_PNG}")
    print(f"5 GHz RSSI points: {len(rssi_5):,}")
    print(f"6 GHz RSSI points: {len(rssi_6):,}")


if __name__ == "__main__":
    main()
