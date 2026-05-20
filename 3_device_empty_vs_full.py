"""
3_device_empty_vs_full.py  --  Section 3: Per-Device Empty vs Full Stadium

Compares each device's measurements between:
  - empty stadium baseline  (campaign_type = 'empty')
  - full stadium game load  (campaign_type = 'game')

This script is device-centric (not aggregate): each phone is analyzed
individually so you can build per-device performance tables.

Metrics included:
  3.1 RSSI by band (5GHz, 6GHz)
  3.2 Channel utilization by band (5GHz, 6GHz)
  3.3 Beacon count per snapshot
  3.4 Connected link speeds (legacy/TX/RX)
  3.5 Per-device interaction effect:
      (6GHz - 5GHz) under GAME minus (6GHz - 5GHz) under EMPTY

Output: outputs/3_device_empty_vs_full.txt
"""

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    output_to, header, subheader, fmt, fmt_p, sig_stars, print_table,
    descriptive, bootstrap_median_ci, mannwhitney, cliffs_delta,
    effect_label_cliff,
)

DB_DEFAULT = "stadium.duckdb"
OUT_FILE = Path("outputs/3_device_empty_vs_full.txt")

QUERY_WIFI = """
SELECT
    s.uuid,
    s.section AS device_label,
    s.device_name,
    s.campaign_type,
    w.band,
    w.rssi,
    w.ch_util,
    w.connected,
    w.link_speed,
    w.tx_link_speed,
    w.rx_link_speed
FROM snapshots s
JOIN wifi_beacons w USING (snapshot_id)
WHERE s.campaign_type IN ('empty', 'game')
"""

QUERY_BEACON_COUNTS = """
SELECT
    s.uuid,
    s.section AS device_label,
    s.device_name,
    s.campaign_type,
    s.snapshot_id,
    COUNT(w.id) AS beacon_count
FROM snapshots s
LEFT JOIN wifi_beacons w USING (snapshot_id)
WHERE s.campaign_type IN ('empty', 'game')
GROUP BY s.section, s.device_name, s.campaign_type, s.snapshot_id
    , s.uuid
"""


def device_id(uuid: str | None, device_name: str | None, device_label: str | None) -> str:
    """Stable per-phone identity: prefer UUID, fallback to readable labels."""
    if uuid:
        return f"uuid:{uuid}"
    return f"fallback:{device_name or device_label or 'UNKNOWN'}"


def device_label_pretty(dev_id: str, meta: dict) -> str:
    """Human-friendly label for tables."""
    if dev_id.startswith("uuid:"):
        short = dev_id.split(":", 1)[1][:8]
        name = meta.get(dev_id, {}).get("device_name") or "UNKNOWN"
        return f"{name} [{short}]"
    return meta.get(dev_id, {}).get("device_name") or dev_id.split(":", 1)[1]


def safe_compare(a: np.ndarray, b: np.ndarray) -> dict:
    """Compare two arrays (game vs empty). Returns dict with stats or N/A markers."""
    if len(a) < 3 or len(b) < 3:
        return {
            "n_empty": len(a), "n_game": len(b),
            "med_empty": None, "med_game": None,
            "diff": None, "ci": None,
            "p": None, "cliff": None, "effect": "N/A", "sig": "N/A"
        }

    med_empty = float(np.median(a))
    med_game = float(np.median(b))
    obs, ci_lo, ci_hi = bootstrap_median_ci(a, b)
    _, p = mannwhitney(a, b)
    cld = cliffs_delta(a, b)

    return {
        "n_empty": len(a), "n_game": len(b),
        "med_empty": med_empty, "med_game": med_game,
        "diff": obs, "ci": (ci_lo, ci_hi),
        "p": p, "cliff": cld,
        "effect": effect_label_cliff(cld),
        "sig": sig_stars(p),
    }


def arr_from(store: dict, dev: str, cond: str, metric: str, band: str | None = None) -> np.ndarray:
    """Read array from nested dict; return empty array if missing."""
    if band is None:
        return np.array(store.get(dev, {}).get(cond, {}).get(metric, []), dtype=float)
    return np.array(store.get(dev, {}).get(cond, {}).get(band, {}).get(metric, []), dtype=float)


def fmt_ci(ci, unit=""):
    if ci is None:
        return "N/A"
    lo, hi = ci
    suffix = f" {unit}" if unit else ""
    return f"[{lo:+.2f}, {hi:+.2f}]{suffix}"


def run(db_path: Path) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    wifi_rows = con.execute(QUERY_WIFI).fetchall()
    beacon_rows = con.execute(QUERY_BEACON_COUNTS).fetchall()
    con.close()

    if not wifi_rows:
        print("No game/empty wifi data found.")
        return

    # ------------------------------------------------------------------
    # Organize metrics per device/condition/band
    # metrics_band[device][condition][band][metric] -> list
    metrics_band: dict = {}
    # metrics_plain[device][condition][metric] -> list
    metrics_plain: dict = {}

    device_meta: dict = {}

    for uuid, dev_label, dev_name, campaign, band, rssi, ch_util, connected, ls, tx, rx in wifi_rows:
        dev = device_id(uuid, dev_name, dev_label)
        device_meta.setdefault(dev, {
            "device_name": dev_name or dev_label or "UNKNOWN",
            "uuid": uuid,
        })

        metrics_band.setdefault(dev, {}).setdefault(campaign, {}).setdefault(band, {
            "rssi": [], "ch_util": [], "link": [], "tx": [], "rx": []
        })
        metrics_plain.setdefault(dev, {}).setdefault(campaign, {
            "link": [], "tx": [], "rx": []
        })

        # RSSI / utilization by band
        if rssi is not None and band in ("5GHz", "6GHz"):
            metrics_band[dev][campaign][band]["rssi"].append(rssi)
        if ch_util is not None and ch_util >= 0 and band in ("5GHz", "6GHz"):
            metrics_band[dev][campaign][band]["ch_util"].append(ch_util)

        # Link speed only when connected + non-null
        if connected:
            if ls is not None:
                metrics_band[dev][campaign].setdefault(band, {"rssi": [], "ch_util": [], "link": [], "tx": [], "rx": []})
                metrics_band[dev][campaign][band]["link"].append(ls)
                metrics_plain[dev][campaign]["link"].append(ls)
            if tx is not None:
                metrics_band[dev][campaign].setdefault(band, {"rssi": [], "ch_util": [], "link": [], "tx": [], "rx": []})
                metrics_band[dev][campaign][band]["tx"].append(tx)
                metrics_plain[dev][campaign]["tx"].append(tx)
            if rx is not None:
                metrics_band[dev][campaign].setdefault(band, {"rssi": [], "ch_util": [], "link": [], "tx": [], "rx": []})
                metrics_band[dev][campaign][band]["rx"].append(rx)
                metrics_plain[dev][campaign]["rx"].append(rx)

    # Beacon counts per snapshot
    beacon_store: dict = {}
    for uuid, dev_label, dev_name, campaign, snap_id, beacon_count in beacon_rows:
        dev = device_id(uuid, dev_name, dev_label)
        device_meta.setdefault(dev, {
            "device_name": dev_name or dev_label or "UNKNOWN",
            "uuid": uuid,
        })
        beacon_store.setdefault(dev, {}).setdefault(campaign, []).append(beacon_count)

    all_devices = sorted(set(metrics_band.keys()) | set(beacon_store.keys()))

    # Keep devices that have at least some data in BOTH empty and game campaigns
    devices = []
    for dev in all_devices:
        has_empty = (
            "empty" in metrics_plain.get(dev, {}) or
            "empty" in beacon_store.get(dev, {}) or
            "empty" in metrics_band.get(dev, {})
        )
        has_game = (
            "game" in metrics_plain.get(dev, {}) or
            "game" in beacon_store.get(dev, {}) or
            "game" in metrics_band.get(dev, {})
        )
        if has_empty and has_game:
            devices.append(dev)

    header("SECTION 3 — PER-DEVICE EMPTY VS FULL STADIUM COMPARISON")
    print("  Comparison: game (loaded stadium) vs empty (idle baseline)")
    print("  Analysis level: individual device (phone-by-phone)")
    print(f"  Devices with both conditions: {len(devices)}")
    print()
    for dev in devices:
        print(f"    - {device_label_pretty(dev, device_meta)}")

    # ------------------------------------------------------------------
    subheader("3.1  RSSI Comparison by Device and Band (game - empty)")
    rssi_hdr = [
        "Device", "Band", "N empty", "N game",
        "Median empty", "Median game", "Δ median", "95% CI", "p", "Cliff's δ", "Sig"
    ]
    rssi_tbl = []
    for dev in devices:
        for band in ("5GHz", "6GHz"):
            a = arr_from(metrics_band, dev, "empty", "rssi", band)
            b = arr_from(metrics_band, dev, "game", "rssi", band)
            c = safe_compare(a, b)
            rssi_tbl.append([
                device_label_pretty(dev, device_meta),
                band,
                c["n_empty"], c["n_game"],
                fmt(c["med_empty"], 2) if c["med_empty"] is not None else "N/A",
                fmt(c["med_game"], 2) if c["med_game"] is not None else "N/A",
                f"{c['diff']:+.2f} dB" if c["diff"] is not None else "N/A",
                fmt_ci(c["ci"], "dB"),
                fmt_p(c["p"]) if c["p"] is not None else "N/A",
                f"{c['cliff']:+.3f}" if c["cliff"] is not None else "N/A",
                c["sig"],
            ])
    print()
    print_table(rssi_hdr, rssi_tbl)

    # ------------------------------------------------------------------
    subheader("3.2  Channel Utilization Comparison by Device and Band (game - empty)")
    util_hdr = [
        "Device", "Band", "N empty", "N game",
        "Median empty", "Median game", "Δ median", "95% CI", "p", "Cliff's δ", "Sig"
    ]
    util_tbl = []
    for dev in devices:
        for band in ("5GHz", "6GHz"):
            a = arr_from(metrics_band, dev, "empty", "ch_util", band)
            b = arr_from(metrics_band, dev, "game", "ch_util", band)
            c = safe_compare(a, b)
            util_tbl.append([
                device_label_pretty(dev, device_meta),
                band,
                c["n_empty"], c["n_game"],
                fmt(c["med_empty"], 4) if c["med_empty"] is not None else "N/A",
                fmt(c["med_game"], 4) if c["med_game"] is not None else "N/A",
                f"{c['diff']:+.4f}" if c["diff"] is not None else "N/A",
                fmt_ci(c["ci"]),
                fmt_p(c["p"]) if c["p"] is not None else "N/A",
                f"{c['cliff']:+.3f}" if c["cliff"] is not None else "N/A",
                c["sig"],
            ])
    print()
    print_table(util_hdr, util_tbl)

    # ------------------------------------------------------------------
    subheader("3.3  Beacon Count per Snapshot (game - empty)")
    beacon_hdr = [
        "Device", "N empty", "N game",
        "Median empty", "Median game", "Δ median", "95% CI", "p", "Cliff's δ", "Sig"
    ]
    beacon_tbl = []
    for dev in devices:
        a = np.array(beacon_store.get(dev, {}).get("empty", []), dtype=float)
        b = np.array(beacon_store.get(dev, {}).get("game", []), dtype=float)
        c = safe_compare(a, b)
        beacon_tbl.append([
            device_label_pretty(dev, device_meta),
            c["n_empty"], c["n_game"],
            fmt(c["med_empty"], 1) if c["med_empty"] is not None else "N/A",
            fmt(c["med_game"], 1) if c["med_game"] is not None else "N/A",
            f"{c['diff']:+.1f}" if c["diff"] is not None else "N/A",
            fmt_ci(c["ci"]),
            fmt_p(c["p"]) if c["p"] is not None else "N/A",
            f"{c['cliff']:+.3f}" if c["cliff"] is not None else "N/A",
            c["sig"],
        ])
    print()
    print_table(beacon_hdr, beacon_tbl)

    # ------------------------------------------------------------------
    # 3.3b  Cumulative weak-signal statistics (attenuation proxy)
    subheader("3.3b  Cumulative Weak-Signal Statistics (proxy for far-away radios)")
    print("""
  Uses cumulative RSSI tails to estimate distant-beacon bleed-through.
  More very-weak beacons (e.g., <= -85 dBm) typically indicate more
  far-away APs being visible. Lower tails under game load support stronger
  attenuation / spatial isolation.
""")

    def all_rssi_for(dev: str, cond: str) -> np.ndarray:
        vals = []
        for band_map in metrics_band.get(dev, {}).get(cond, {}).values():
            vals.extend(band_map.get("rssi", []))
        return np.array(vals, dtype=float)

    tail_hdr = [
        "Device", "N empty", "N game",
        "%<=-85 empty", "%<=-85 game", "Δ pp",
        "%<=-80 empty", "%<=-80 game", "Δ pp",
        "Median empty", "Median game", "Δ median"
    ]
    tail_tbl = []

    # pooled row first
    pooled_empty = np.concatenate([all_rssi_for(dev, "empty") for dev in devices])
    pooled_game = np.concatenate([all_rssi_for(dev, "game") for dev in devices])

    def tail_row(label: str, arr_empty: np.ndarray, arr_game: np.ndarray):
        if len(arr_empty) < 3 or len(arr_game) < 3:
            return [label, len(arr_empty), len(arr_game)] + ["N/A"] * 9
        e85 = np.mean(arr_empty <= -85) * 100
        g85 = np.mean(arr_game <= -85) * 100
        e80 = np.mean(arr_empty <= -80) * 100
        g80 = np.mean(arr_game <= -80) * 100
        me = float(np.median(arr_empty))
        mg = float(np.median(arr_game))
        return [
            label,
            len(arr_empty), len(arr_game),
            f"{e85:.1f}%", f"{g85:.1f}%", f"{g85-e85:+.1f}",
            f"{e80:.1f}%", f"{g80:.1f}%", f"{g80-e80:+.1f}",
            f"{me:.2f}", f"{mg:.2f}", f"{mg-me:+.2f}",
        ]

    tail_tbl.append(tail_row("ALL DEVICES", pooled_empty, pooled_game))

    for dev in devices:
        arr_empty = all_rssi_for(dev, "empty")
        arr_game = all_rssi_for(dev, "game")
        tail_tbl.append(tail_row(device_label_pretty(dev, device_meta), arr_empty, arr_game))

    print()
    print_table(tail_hdr, tail_tbl)

    # ------------------------------------------------------------------
    # 3.3c  Cumulative beacon-count summary (audible beacons)
    subheader("3.3c  Cumulative Audible Beacon Count Summary (empty vs full)")
    print("""
  Beacon counts are per snapshot and represent how many AP beacons the phone
  could hear at that moment. Higher values imply more radios being audible.
""")

    def beacon_row(label: str, arr_empty: np.ndarray, arr_game: np.ndarray):
        if len(arr_empty) == 0 or len(arr_game) == 0:
            return [label, len(arr_empty), len(arr_game)] + ["N/A"] * 7

        mean_e = float(np.mean(arr_empty))
        mean_g = float(np.mean(arr_game))
        med_e = float(np.median(arr_empty))
        med_g = float(np.median(arr_game))
        pct_mean = ((mean_g - mean_e) / mean_e * 100) if mean_e != 0 else float("nan")
        pct_med = ((med_g - med_e) / med_e * 100) if med_e != 0 else float("nan")

        return [
            label,
            len(arr_empty),
            len(arr_game),
            f"{mean_e:.1f}",
            f"{mean_g:.1f}",
            f"{mean_g-mean_e:+.1f}",
            f"{pct_mean:+.1f}%",
            f"{med_e:.1f}",
            f"{med_g:.1f}",
            f"{med_g-med_e:+.1f}",
            f"{pct_med:+.1f}%",
        ]

    pooled_beacon_empty = np.concatenate([
        np.array(beacon_store.get(dev, {}).get("empty", []), dtype=float)
        for dev in devices
    ])
    pooled_beacon_game = np.concatenate([
        np.array(beacon_store.get(dev, {}).get("game", []), dtype=float)
        for dev in devices
    ])

    beacon_sum_hdr = [
        "Device",
        "N empty", "N game",
        "Mean empty", "Mean game", "Δ mean", "%Δ mean",
        "Median empty", "Median game", "Δ median", "%Δ median",
    ]
    beacon_sum_tbl = [
        beacon_row("ALL DEVICES", pooled_beacon_empty, pooled_beacon_game)
    ]

    for dev in devices:
        arr_empty = np.array(beacon_store.get(dev, {}).get("empty", []), dtype=float)
        arr_game = np.array(beacon_store.get(dev, {}).get("game", []), dtype=float)
        beacon_sum_tbl.append(beacon_row(device_label_pretty(dev, device_meta), arr_empty, arr_game))

    print()
    print_table(beacon_sum_hdr, beacon_sum_tbl)

    # ------------------------------------------------------------------
    subheader("3.4  Connected Link Speed Metrics (game - empty)")
    for metric_key, metric_label in [
        ("link", "Link Speed"),
        ("tx", "TX Link Speed"),
        ("rx", "RX Link Speed"),
    ]:
        print(f"\n  {metric_label} (Mbps)")
        ls_hdr = [
            "Device", "N empty", "N game",
            "Median empty", "Median game", "Δ median", "95% CI", "p", "Cliff's δ", "Sig"
        ]
        ls_tbl = []
        for dev in devices:
            a = arr_from(metrics_plain, dev, "empty", metric_key)
            b = arr_from(metrics_plain, dev, "game", metric_key)
            c = safe_compare(a, b)
            ls_tbl.append([
                device_label_pretty(dev, device_meta),
                c["n_empty"], c["n_game"],
                fmt(c["med_empty"], 1) if c["med_empty"] is not None else "N/A",
                fmt(c["med_game"], 1) if c["med_game"] is not None else "N/A",
                f"{c['diff']:+.1f}" if c["diff"] is not None else "N/A",
                fmt_ci(c["ci"], "Mbps"),
                fmt_p(c["p"]) if c["p"] is not None else "N/A",
                f"{c['cliff']:+.3f}" if c["cliff"] is not None else "N/A",
                c["sig"],
            ])
        print_table(ls_hdr, ls_tbl)

    # ------------------------------------------------------------------
    subheader("3.5  Per-Device Interaction Effect: (6GHz - 5GHz)_game - (6GHz - 5GHz)_empty")
    print("""
  Negative interaction means 6GHz gains an additional advantage over 5GHz
  under load (game) compared with empty-stadium baseline.
""")

    inter_hdr = ["Device", "6-5 (empty)", "6-5 (game)", "Interaction", "Interpretation"]
    inter_tbl = []
    for dev in devices:
        e5 = arr_from(metrics_band, dev, "empty", "rssi", "5GHz")
        e6 = arr_from(metrics_band, dev, "empty", "rssi", "6GHz")
        g5 = arr_from(metrics_band, dev, "game", "rssi", "5GHz")
        g6 = arr_from(metrics_band, dev, "game", "rssi", "6GHz")

        if min(len(e5), len(e6), len(g5), len(g6)) < 3:
            inter_tbl.append([device_label_pretty(dev, device_meta), "N/A", "N/A", "N/A", "insufficient data"])
            continue

        d_empty = float(np.median(e6) - np.median(e5))
        d_game = float(np.median(g6) - np.median(g5))
        interaction = d_game - d_empty

        if interaction < -1:
            note = "6GHz advantage increases under load"
        elif interaction > 1:
            note = "6GHz advantage decreases under load"
        else:
            note = "little interaction"

        inter_tbl.append([
            device_label_pretty(dev, device_meta),
            f"{d_empty:+.2f} dB",
            f"{d_game:+.2f} dB",
            f"{interaction:+.2f} dB",
            note,
        ])

    print()
    print_table(inter_hdr, inter_tbl)


def main():
    parser = argparse.ArgumentParser(description="Per-device empty-vs-game comparison")
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
