"""
0_rssi_baseline.py  --  Phone-to-Phone RSSI Baseline Comparison

Analyses the EMPTY stadium test data to determine whether different phones
report systematically different RSSI values when scanning the same APs from
the same location at the same time.

Method
------
1. Pull all snapshots from campaign_type = 'empty' and find the overlapping
   window where all devices were recording simultaneously (~1 PM, 10-15 min).
2. Bin snapshots into 30-second windows.  For each (BSSID, time-bin) seen by
   2+ different devices, collect one RSSI observation per device  → a
   "matched scan".
3. Per-device RSSI bias: for every matched scan, subtract the group median
   RSSI for that (BSSID, bin) from each device's reading.  The distribution
   of those residuals is the device's measurement bias.
4. Kruskal-Wallis across all devices, then pairwise Mann-Whitney U with
   Bonferroni correction.

Output: outputs/0_rssi_baseline.txt

Usage:
  python 0_rssi_baseline.py [--db PATH] [--bin-sec N] [--band BAND] [--aliases PATH]
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    output_to, header, subheader, fmt, fmt_p, sig_stars, print_table,
    descriptive, bootstrap_median_ci, mannwhitney, cohens_d, cliffs_delta,
    effect_label_d, effect_label_cliff, kruskal_wallis,
)

DB_DEFAULT      = "stadium.duckdb"
ALIASES_DEFAULT = "device_aliases.json"
OUT_FILE        = Path("outputs/0_rssi_baseline.txt")
DEFAULT_BIN_SEC = 30


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

SNAPSHOT_QUERY = """
SELECT
    s.snapshot_id,
    s.section          AS device_label,
    s.device_name,
    s.uuid,
    s.datetime_iso,
    s.latitude,
    s.longitude
FROM snapshots s
WHERE s.campaign_type = 'empty'
  AND s.datetime_iso IS NOT NULL
ORDER BY s.datetime_iso
"""

WIFI_QUERY = """
SELECT
    w.snapshot_id,
    w.bssid,
    w.rssi,
    w.band
FROM wifi_beacons w
WHERE w.rssi IS NOT NULL
  AND w.bssid IS NOT NULL
  AND w.snapshot_id IN (
      SELECT snapshot_id FROM snapshots WHERE campaign_type = 'empty'
  )
"""

LINK_SPEED_QUERY = """
SELECT
    s.section          AS device_label,
    s.device_name,
    w.link_speed,
    w.tx_link_speed,
    w.rx_link_speed,
    w.band,
    s.datetime_iso
FROM wifi_beacons w
JOIN snapshots s USING (snapshot_id)
WHERE s.campaign_type = 'empty'
  AND w.connected = true
  AND w.link_speed IS NOT NULL
ORDER BY s.datetime_iso
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_dt(iso: str) -> datetime | None:
    """Parse an ISO-8601 string to a UTC-aware datetime."""
    if not iso:
        return None
    for fmt_str in (
        "%Y-%m-%dT%H:%M:%S.%f%z",   # 2026-03-04T12:53:27.160-0700
        "%Y-%m-%dT%H:%M:%S%z",       # 2026-03-04T12:53:27-0700
        "%Y%m%dT%H%M%S%fZ",          # 20251003T204327030Z
        "%Y-%m-%dT%H:%M:%S.%fZ",     # 2026-03-04T12:53:27.160Z
        "%Y-%m-%dT%H:%M:%SZ",        # 2026-03-04T12:53:27Z
        "%Y-%m-%dT%H:%M:%S",         # no tz
    ):
        try:
            dt = datetime.strptime(iso, fmt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def ts_bin(dt: datetime, bin_sec: int) -> int:
    """Floor a datetime to the nearest bin_sec boundary, return as Unix ts."""
    ts = int(dt.timestamp())
    return ts - (ts % bin_sec)


def load_aliases(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def display_name(device_name: str, device_label: str, aliases: dict) -> str:
    """Return custom_name if set, else 'LABEL (device_name)'."""
    custom = aliases.get(device_name, "").strip()
    if custom:
        return custom
    return f"{device_label} ({device_name})" if device_name else device_label


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def run(db_path: Path, bin_sec: int, band_filter: str | None,
        aliases: dict) -> None:

    con = duckdb.connect(str(db_path), read_only=True)
    snap_rows = con.execute(SNAPSHOT_QUERY).fetchall()
    wifi_rows = con.execute(WIFI_QUERY).fetchall()
    ls_rows   = con.execute(LINK_SPEED_QUERY).fetchall()
    con.close()

    if not snap_rows:
        print("No EMPTY campaign snapshots found in the database.")
        return

    # ------------------------------------------------------------------
    # Build snapshot metadata
    # snap_meta: snapshot_id -> {device_label, device_name, dt, lat, lon}
    snap_meta = {}
    for sid, dlabel, dname, uuid, dt_iso, lat, lon in snap_rows:
        dt = parse_dt(dt_iso)
        if dt is None:
            continue
        snap_meta[sid] = dict(
            device_label=dlabel or "?",
            device_name=dname or "?",
            uuid=uuid or "?",
            dt=dt,
            lat=lat,
            lon=lon,
        )

    # Gather unique devices (key by label+name combo)
    devices_seen = {}   # label -> device_name (first seen)
    for m in snap_meta.values():
        devices_seen.setdefault(m["device_label"], m["device_name"])

    device_labels = sorted(devices_seen.keys())
    disp = {lbl: display_name(devices_seen[lbl], lbl, aliases)
            for lbl in device_labels}

    # ------------------------------------------------------------------
    # Find overlapping recording window
    dt_by_device = defaultdict(list)
    for m in snap_meta.values():
        dt_by_device[m["device_label"]].append(m["dt"])

    if not dt_by_device:
        print("ERROR: No snapshots could be parsed — check datetime_iso format.")
        return

    overlap_start = max(min(dts) for dts in dt_by_device.values())
    overlap_end   = min(max(dts) for dts in dt_by_device.values())

    # Filter snaps to overlap window
    snap_in_window = {
        sid: m for sid, m in snap_meta.items()
        if overlap_start <= m["dt"] <= overlap_end
    }

    # ------------------------------------------------------------------
    # Build WiFi index: snap_id -> [(bssid, rssi, band)]
    wifi_by_snap = defaultdict(list)
    for sid, bssid, rssi, band in wifi_rows:
        if sid in snap_in_window:
            if band_filter and band != band_filter:
                continue
            wifi_by_snap[sid].append((bssid, rssi, band))

    # ------------------------------------------------------------------
    # Build matched scans:
    # key: (bssid, time_bin) -> {device_label: [rssi, ...]}
    matched: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for sid, m in snap_in_window.items():
        tbin = ts_bin(m["dt"], bin_sec)
        dlabel = m["device_label"]
        for bssid, rssi, band in wifi_by_snap.get(sid, []):
            matched[(bssid, tbin)][dlabel].append(rssi)

    # Keep only bins where 2+ devices are present
    shared_bins = {
        k: v for k, v in matched.items()
        if len(v) >= 2
    }

    # ------------------------------------------------------------------
    # Compute per-device RSSI bias residuals
    # For each shared (bssid, bin): group_median across all devices present,
    # then each device's bias = its median for that bin - group_median.
    device_bias: dict[str, list] = defaultdict(list)
    device_rssi: dict[str, list] = defaultdict(list)   # raw matched RSSI

    for (bssid, tbin), dev_map in shared_bins.items():
        all_meds = []
        dev_meds = {}
        for dlabel, vals in dev_map.items():
            m = float(np.median(vals))
            dev_meds[dlabel] = m
            all_meds.append(m)
            device_rssi[dlabel].extend(vals)
        group_med = float(np.median(all_meds))
        for dlabel, med in dev_meds.items():
            device_bias[dlabel].append(med - group_med)

    # ------------------------------------------------------------------
    header("SECTION 0 — PHONE-TO-PHONE RSSI BASELINE (EMPTY STADIUM TESTS)")

    # 0.0  Recording window
    subheader("0.0  EMPTY Test Recording Window")
    print(f"\n  Devices found       : {len(device_labels)}")
    for lbl in device_labels:
        dts = dt_by_device[lbl]
        print(f"    {disp[lbl]:<40}  "
              f"{min(dts).strftime('%Y-%m-%d %H:%M:%S')} UTC  →  "
              f"{max(dts).strftime('%Y-%m-%d %H:%M:%S')} UTC  "
              f"({len(dts)} snapshots)")

    dur_min = (overlap_end - overlap_start).total_seconds() / 60
    print(f"\n  Overlapping window  : {overlap_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"                        {overlap_end.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Duration            : {dur_min:.1f} minutes")
    print(f"  Bin size            : {bin_sec}s")
    band_note = band_filter if band_filter else "all bands"
    print(f"  Band filter         : {band_note}")

    snap_counts = defaultdict(int)
    for m in snap_in_window.values():
        snap_counts[m["device_label"]] += 1
    print(f"\n  Snapshots in window per device:")
    for lbl in device_labels:
        print(f"    {disp[lbl]:<40}  {snap_counts[lbl]}")

    print(f"\n  Shared (BSSID, time-bin) pairs with 2+ devices : {len(shared_bins)}")

    # ------------------------------------------------------------------
    # 0.1  Per-device descriptive RSSI statistics (matched scans only)
    subheader("0.1  Per-Device RSSI Descriptive Statistics (matched scans)")

    desc_headers = ["Device", "N", "Median", "Mean", "Std", "IQR", "P10", "P90"]
    desc_rows = []
    for lbl in device_labels:
        arr = np.array(device_rssi.get(lbl, []))
        if len(arr) == 0:
            desc_rows.append([disp[lbl], 0, *["N/A"] * 6])
            continue
        d = descriptive(arr)
        desc_rows.append([
            disp[lbl],
            d["n"],
            fmt(d["median"]),
            fmt(d["mean"]),
            fmt(d["std"]),
            fmt(d["iqr"]),
            fmt(d["p10"]),
            fmt(d["p90"]),
        ])
    print()
    print_table(desc_headers, desc_rows)

    # ------------------------------------------------------------------
    # 0.2  Per-device RSSI bias (relative to group median per BSSID/bin)
    subheader("0.2  Per-Device RSSI Bias (dB relative to per-bin group median)")
    print("""
  A positive bias means the device tends to REPORT HIGHER RSSI than the
  median of all co-located devices for the same AP in the same time window.
  A negative bias means it reports LOWER RSSI.
""")

    bias_headers = ["Device", "N pairs", "Median bias", "Mean bias",
                    "Std", "P10", "P90", "Interpretation"]
    bias_rows = []
    bias_arrays = {}
    for lbl in device_labels:
        arr = np.array(device_bias.get(lbl, []))
        bias_arrays[lbl] = arr
        if len(arr) == 0:
            bias_rows.append([disp[lbl], 0, *["N/A"] * 5, "no data"])
            continue
        d = descriptive(arr)
        med = d["median"]
        if abs(med) < 0.5:
            interp = "near-neutral"
        elif med > 0:
            interp = f"reports ~{abs(med):.1f} dB HIGHER"
        else:
            interp = f"reports ~{abs(med):.1f} dB lower"
        bias_rows.append([
            disp[lbl],
            d["n"],
            fmt(d["median"], 2),
            fmt(d["mean"], 2),
            fmt(d["std"], 2),
            fmt(d["p10"], 2),
            fmt(d["p90"], 2),
            interp,
        ])
    print()
    print_table(bias_headers, bias_rows)

    # ------------------------------------------------------------------
    # 0.3  Kruskal-Wallis across all devices
    subheader("0.3  Kruskal-Wallis Test (are any devices significantly different?)")

    valid_labels = [lbl for lbl in device_labels
                    if len(bias_arrays.get(lbl, [])) > 0]
    groups = [bias_arrays[lbl] for lbl in valid_labels]

    if len(groups) < 2:
        print("\n  Not enough devices with data for Kruskal-Wallis.")
    else:
        H, p_kw = kruskal_wallis(*groups)
        print(f"\n  H = {H:.4f},  p = {fmt_p(p_kw)} {sig_stars(p_kw)}")
        if p_kw < 0.05:
            print("  → At least one device reports significantly different RSSI "
                  "from the others (p < 0.05).")
        else:
            print("  → No statistically significant difference detected across "
                  "devices (p ≥ 0.05).")

    # ------------------------------------------------------------------
    # 0.4  Pairwise comparisons (Mann-Whitney + Bonferroni)
    subheader("0.4  Pairwise Device Comparisons (Mann-Whitney, Bonferroni corrected)")

    pairs = list(combinations(valid_labels, 2))
    n_pairs = len(pairs)
    print(f"\n  {n_pairs} pairs, Bonferroni α = {0.05/n_pairs:.4f}\n")

    pair_headers = ["Device A", "Device B", "Δ median (B−A)",
                    "95% CI", "U", "p (raw)", "p (adj)", "sig", "Cliff's δ", "effect"]
    pair_rows = []
    for la, lb in pairs:
        aa = bias_arrays[la]
        ab = bias_arrays[lb]
        obs, ci_lo, ci_hi = bootstrap_median_ci(aa, ab)
        U, p_raw = mannwhitney(aa, ab)
        p_adj = min(p_raw * n_pairs, 1.0)
        cd = cliffs_delta(aa, ab)
        pair_rows.append([
            disp[la],
            disp[lb],
            f"{obs:+.2f} dB",
            f"[{ci_lo:+.2f}, {ci_hi:+.2f}]",
            f"{U:.0f}",
            fmt_p(p_raw),
            fmt_p(p_adj),
            sig_stars(p_adj),
            f"{cd:+.3f}",
            effect_label_cliff(cd),
        ])
    print_table(pair_headers, pair_rows)

    # ------------------------------------------------------------------
    # 0.5  Summary / interpretation
    subheader("0.5  Summary")

    sig_pairs = [(la, lb, row) for (la, lb), row in zip(pairs, pair_rows)
                 if row[7] != "ns"]
    print()
    if not sig_pairs:
        print("  No pairwise differences survive Bonferroni correction.")
        print("  Devices appear to report consistent RSSI for the same APs.")
    else:
        print(f"  {len(sig_pairs)} pairwise difference(s) survive Bonferroni correction:")
        for la, lb, row in sig_pairs:
            print(f"    {disp[la]}  vs  {disp[lb]}")
            print(f"      Δ median = {row[2]}  CI = {row[3]}  "
                  f"p_adj = {row[6]}  Cliff's δ = {row[8]} ({row[9]})")

    # Rank devices by median bias (reference = device with bias closest to 0)
    ranked = sorted(
        [(lbl, float(np.median(bias_arrays[lbl])))
         for lbl in valid_labels if len(bias_arrays[lbl]) > 0],
        key=lambda x: x[1]
    )
    if ranked:
        print(f"\n  Devices ranked low→high by median bias (dB vs group median):")
        for lbl, med in ranked:
            print(f"    {disp[lbl]:<40}  {med:+.2f} dB")

    # ------------------------------------------------------------------
    # 0.6  Link speed comparison (connected AP only)
    header("SECTION 0.6 — PER-DEVICE LINK SPEED COMPARISON (CONNECTED, EMPTY TESTS)")

    if not ls_rows:
        print("\n  No connected link-speed records found for empty campaign.")
    else:
        # Organise by device
        ls_by_device: dict[str, dict] = defaultdict(lambda: {"link": [], "tx": [], "rx": [], "bands": []})
        for dlabel, dname, ls, tx, rx, band, dt_iso in ls_rows:
            d = ls_by_device[dlabel]
            d["dname"] = dname
            d["link"].append(ls)
            d["tx"].append(tx)
            d["rx"].append(rx)
            d["bands"].append(band)

        ls_device_labels = sorted(ls_by_device.keys())
        ls_disp = {
            lbl: display_name(ls_by_device[lbl].get("dname", lbl), lbl, aliases)
            for lbl in ls_device_labels
        }

        # Timestamp range for link-speed data
        subheader("0.6a  Link-Speed Recording Window")
        ls_dts_by_device = defaultdict(list)
        for dlabel, dname, ls, tx, rx, band, dt_iso in ls_rows:
            dt = parse_dt(dt_iso)
            if dt:
                ls_dts_by_device[dlabel].append(dt)
        print()
        for lbl in ls_device_labels:
            dts = ls_dts_by_device[lbl]
            if dts:
                print(f"  {ls_disp[lbl]:<40}  "
                      f"{min(dts).strftime('%Y-%m-%d %H:%M:%S')} UTC  →  "
                      f"{max(dts).strftime('%Y-%m-%d %H:%M:%S')} UTC  "
                      f"({len(dts)} connected records)")
        if ls_dts_by_device:
            all_dts = [dt for dts in ls_dts_by_device.values() for dt in dts]
            print(f"\n  Full linked-speed span: "
                  f"{min(all_dts).strftime('%Y-%m-%d %H:%M:%S')} UTC  →  "
                  f"{max(all_dts).strftime('%Y-%m-%d %H:%M:%S')} UTC")
            dur = (max(all_dts) - min(all_dts)).total_seconds() / 60
            print(f"  Duration: {dur:.1f} minutes")

        # Band breakdown
        subheader("0.6b  Band Distribution (connected records)")
        print()
        band_hdr = ["Device", "Total", "2.4GHz", "5GHz", "6GHz", "other"]
        band_tbl = []
        for lbl in ls_device_labels:
            bands = ls_by_device[lbl]["bands"]
            total = len(bands)
            def bc(b): return sum(1 for x in bands if x == b)
            other = total - bc("2.4GHz") - bc("5GHz") - bc("6GHz")
            band_tbl.append([ls_disp[lbl], total,
                             bc("2.4GHz"), bc("5GHz"), bc("6GHz"), other])
        print_table(band_hdr, band_tbl)

        # Descriptive stats for each metric
        for metric_key, metric_label, unit in [
            ("link",  "Link Speed (legacy)",  "Mbps"),
            ("tx",    "TX Link Speed",         "Mbps"),
            ("rx",    "RX Link Speed",         "Mbps"),
        ]:
            subheader(f"0.6c  {metric_label} — Descriptive Stats")
            d_hdr = ["Device", "N", "Median", "Mean", "Std", "IQR", "P10", "P90", "Max"]
            d_tbl = []
            arrs = {}
            for lbl in ls_device_labels:
                arr = np.array(ls_by_device[lbl][metric_key], dtype=float)
                arrs[lbl] = arr
                if len(arr) == 0:
                    d_tbl.append([ls_disp[lbl], 0] + ["N/A"] * 7)
                    continue
                d = descriptive(arr)
                d_tbl.append([
                    ls_disp[lbl], d["n"],
                    fmt(d["median"], 1), fmt(d["mean"], 1),
                    fmt(d["std"], 1), fmt(d["iqr"], 1),
                    fmt(d["p10"], 1), fmt(d["p90"], 1),
                    fmt(d["max"], 1),
                ])
            print()
            print_table(d_hdr, d_tbl)

            # Kruskal-Wallis
            valid_ls = [lbl for lbl in ls_device_labels if len(arrs.get(lbl, [])) > 1]
            if len(valid_ls) >= 2:
                groups_ls = [arrs[lbl] for lbl in valid_ls]
                H_ls, p_kw_ls = kruskal_wallis(*groups_ls)
                print(f"\n  Kruskal-Wallis: H = {H_ls:.4f},  p = {fmt_p(p_kw_ls)} {sig_stars(p_kw_ls)}")
                if p_kw_ls < 0.05:
                    print("  → Significant difference in reported link speeds across devices.")
                else:
                    print("  → No significant difference across devices.")

                # Pairwise
                ls_pairs = list(combinations(valid_ls, 2))
                n_ls_pairs = len(ls_pairs)
                print(f"\n  Pairwise comparisons (Bonferroni α = {0.05/n_ls_pairs:.4f}):")
                pw_hdr = ["Device A", "Device B", f"Δ median ({unit})",
                          "95% CI", "p (raw)", "p (adj)", "sig", "Cliff's δ", "effect"]
                pw_rows = []
                for la, lb in ls_pairs:
                    obs, ci_lo, ci_hi = bootstrap_median_ci(arrs[la], arrs[lb])
                    _, p_raw = mannwhitney(arrs[la], arrs[lb])
                    p_adj = min(p_raw * n_ls_pairs, 1.0)
                    cd = cliffs_delta(arrs[la], arrs[lb])
                    pw_rows.append([
                        ls_disp[la], ls_disp[lb],
                        f"{obs:+.1f}",
                        f"[{ci_lo:+.1f}, {ci_hi:+.1f}]",
                        fmt_p(p_raw), fmt_p(p_adj),
                        sig_stars(p_adj),
                        f"{cd:+.3f}",
                        effect_label_cliff(cd),
                    ])
                print()
                print_table(pw_hdr, pw_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db",      default=DB_DEFAULT,
                        help=f"DuckDB path (default: {DB_DEFAULT})")
    parser.add_argument("--bin-sec", type=int, default=DEFAULT_BIN_SEC,
                        help=f"Time-bin width in seconds (default: {DEFAULT_BIN_SEC})")
    parser.add_argument("--band",    default=None,
                        choices=["2.4GHz", "5GHz", "6GHz"],
                        help="Restrict to a single Wi-Fi band (default: all)")
    parser.add_argument("--aliases", default=ALIASES_DEFAULT,
                        help=f"device_aliases.json path (default: {ALIASES_DEFAULT})")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    aliases = load_aliases(Path(args.aliases))
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with output_to(OUT_FILE):
        run(db_path, args.bin_sec, args.band, aliases)

    print(f"\nOutput saved to: {OUT_FILE}")


if __name__ == "__main__":
    main()
