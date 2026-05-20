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


BEACON_COUNT_QUERY = """
SELECT
    s.section          AS device_label,
    s.device_name,
    s.snapshot_id,
    s.datetime_iso,
    COUNT(w.id)        AS beacon_count,
    COUNT(CASE WHEN w.band = '2.4GHz' THEN 1 END) AS cnt_2g,
    COUNT(CASE WHEN w.band = '5GHz'   THEN 1 END) AS cnt_5g,
    COUNT(CASE WHEN w.band = '6GHz'   THEN 1 END) AS cnt_6g
FROM snapshots s
LEFT JOIN wifi_beacons w USING (snapshot_id)
WHERE s.campaign_type = 'empty'
GROUP BY s.section, s.device_name, s.snapshot_id, s.datetime_iso
ORDER BY s.section, s.datetime_iso
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
    snap_rows    = con.execute(SNAPSHOT_QUERY).fetchall()
    wifi_rows    = con.execute(WIFI_QUERY).fetchall()
    ls_rows      = con.execute(LINK_SPEED_QUERY).fetchall()
    beacon_rows  = con.execute(BEACON_COUNT_QUERY).fetchall()
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

        all_device_labels = sorted(devices_seen.keys())
        disp = {lbl: display_name(devices_seen[lbl], lbl, aliases)
            for lbl in all_device_labels}

    # ------------------------------------------------------------------
    # Find overlapping recording window
    dt_by_device = defaultdict(list)
    for m in snap_meta.values():
        dt_by_device[m["device_label"]].append(m["dt"])

    if not dt_by_device:
        print("ERROR: No snapshots could be parsed — check datetime_iso format.")
        return

    # Device intervals and overlap cohort selection.
    # EMPTY tests can include separate sessions/days, so we analyze all maximal
    # overlap cohorts (not just one "best" cohort).
    intervals = {
        lbl: (min(dts), max(dts))
        for lbl, dts in dt_by_device.items() if dts
    }
    candidate_times = sorted({
        t for start_end in intervals.values() for t in start_end
    })
    raw_cohorts = []
    for t in candidate_times:
        cohort = frozenset(
            lbl for lbl, (s, e) in intervals.items()
            if s <= t <= e
        )
        if len(cohort) >= 2:
            raw_cohorts.append(cohort)

    unique_cohorts = []
    seen = set()
    for cohort in raw_cohorts:
        if cohort not in seen:
            unique_cohorts.append(cohort)
            seen.add(cohort)

    # Keep only maximal cohorts (drop strict subsets)
    maximal_cohorts = [
        c for c in unique_cohorts
        if not any(c < other for other in unique_cohorts)
    ]

    if not maximal_cohorts:
        print("Not enough overlapping devices in EMPTY tests for comparison.")
        return

    cohort_specs = []
    for cohort in maximal_cohorts:
        labels = sorted(cohort)
        overlap_start = max(intervals[lbl][0] for lbl in labels)
        overlap_end = min(intervals[lbl][1] for lbl in labels)
        if overlap_end < overlap_start:
            continue
        cohort_specs.append((labels, overlap_start, overlap_end))

    if not cohort_specs:
        print("No valid overlapping window found across selected EMPTY-test devices.")
        return

    cohort_specs.sort(key=lambda x: (x[1], x[2]))

    def run_cohort(device_labels: list[str], overlap_start, overlap_end,
                   cohort_index: int, cohort_total: int) -> None:
        # Filter snaps to overlap window
        snap_in_window = {
            sid: m for sid, m in snap_meta.items()
            if m["device_label"] in device_labels and overlap_start <= m["dt"] <= overlap_end
        }

        # Build WiFi index: snap_id -> [(bssid, rssi, band)]
        wifi_by_snap = defaultdict(list)
        for sid, bssid, rssi, band in wifi_rows:
            if sid in snap_in_window:
                if band_filter and band != band_filter:
                    continue
                wifi_by_snap[sid].append((bssid, rssi, band))

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

        # Compute per-device RSSI bias residuals
        # For each shared (bssid, bin): group_median across all devices present,
        # then each device's bias = its median for that bin - group_median.
        device_bias: dict[str, list] = defaultdict(list)
        device_rssi: dict[str, list] = defaultdict(list)

        for (bssid, tbin), dev_map in shared_bins.items():
            all_meds = []
            dev_meds = {}
            for dlabel, vals in dev_map.items():
                med = float(np.median(vals))
                dev_meds[dlabel] = med
                all_meds.append(med)
                device_rssi[dlabel].extend(vals)
            group_med = float(np.median(all_meds))
            for dlabel, med in dev_meds.items():
                device_bias[dlabel].append(med - group_med)

        header("SECTION 0 — PHONE-TO-PHONE RSSI BASELINE (EMPTY STADIUM TESTS)")
        if cohort_total > 1:
            print(f"\n  Cohort             : {cohort_index}/{cohort_total}")

        # 0.0  Recording window
        subheader("0.0  EMPTY Test Recording Window")
        print(f"\n  Devices found       : {len(all_device_labels)}")
        print(f"  Devices compared    : {len(device_labels)}")
        excluded = [lbl for lbl in all_device_labels if lbl not in device_labels]
        for lbl in all_device_labels:
            dts = dt_by_device[lbl]
            print(f"    {disp[lbl]:<40}  "
                  f"{min(dts).strftime('%Y-%m-%d %H:%M:%S')} UTC  →  "
                  f"{max(dts).strftime('%Y-%m-%d %H:%M:%S')} UTC  "
                  f"({len(dts)} snapshots)")
        if excluded:
            print("\n  Excluded from this comparison (no overlap with this cohort):")
            for lbl in excluded:
                print(f"    {disp[lbl]}")

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

        # 0.4  Pairwise comparisons (Mann-Whitney + Bonferroni)
        subheader("0.4  Pairwise Device Comparisons (Mann-Whitney, Bonferroni corrected)")

        pairs = list(combinations(valid_labels, 2))
        n_pairs = len(pairs)
        if n_pairs == 0:
            print("\n  Not enough device pairs with data for pairwise comparisons.")
        else:
            print(f"\n  {n_pairs} pairs, Bonferroni α = {0.05/n_pairs:.4f}\n")

        pair_headers = ["Device A", "Device B", "Δ median (B−A)",
                        "95% CI", "U", "p (raw)", "p (adj)", "sig", "Cliff's δ", "effect"]
        pair_rows = []
        if n_pairs > 0:
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

        ranked = sorted(
            [(lbl, float(np.median(bias_arrays[lbl])))
             for lbl in valid_labels if len(bias_arrays[lbl]) > 0],
            key=lambda x: x[1]
        )
        if ranked:
            print(f"\n  Devices ranked low→high by median bias (dB vs group median):")
            for lbl, med in ranked:
                print(f"    {disp[lbl]:<40}  {med:+.2f} dB")

    for idx, (labels, overlap_start, overlap_end) in enumerate(cohort_specs, 1):
        if idx > 1:
            print("\n" + "-" * 78)
        run_cohort(labels, overlap_start, overlap_end, idx, len(cohort_specs))

    # ------------------------------------------------------------------
    # 0.5b  All feasible pairwise comparisons (across separate EMPTY sessions)
    subheader("0.5b  All Feasible Pairwise Device Comparisons (uses each pair's own overlap)")
    print("""
  This table compares every device pair that has any overlapping time window,
  even if they are not in the same global cohort. Pairs with no overlap are
  listed as NO OVERLAP.
""")

    pair_overlap_results = []
    base_names = {lbl: disp[lbl] for lbl in all_device_labels}
    base_counts = defaultdict(int)
    for name in base_names.values():
        base_counts[name] += 1
    pair_disp = {
        lbl: (f"{base_names[lbl]} [{lbl}]" if base_counts[base_names[lbl]] > 1 else base_names[lbl])
        for lbl in all_device_labels
    }

    all_pairs = list(combinations(all_device_labels, 2))
    for la, lb in all_pairs:
        start = max(intervals[la][0], intervals[lb][0])
        end = min(intervals[la][1], intervals[lb][1])
        overlap_min = (end - start).total_seconds() / 60

        if end < start:
            pair_overlap_results.append({
                "a": la,
                "b": lb,
                "overlap_min": 0.0,
                "matched_bins": 0,
                "delta": None,
                "p_raw": None,
                "cliff": None,
                "note": "NO OVERLAP",
            })
            continue

        # Snapshots for the pair within its own overlap window
        pair_snap = {
            sid: m for sid, m in snap_meta.items()
            if m["device_label"] in (la, lb) and start <= m["dt"] <= end
        }
        pair_snap_ids = set(pair_snap.keys())
        if not pair_snap_ids:
            pair_overlap_results.append({
                "a": la,
                "b": lb,
                "overlap_min": overlap_min,
                "matched_bins": 0,
                "delta": None,
                "p_raw": None,
                "cliff": None,
                "note": "No snapshots in overlap",
            })
            continue

        pair_matched = defaultdict(lambda: defaultdict(list))
        for sid, bssid, rssi, band in wifi_rows:
            if sid not in pair_snap_ids:
                continue
            if band_filter and band != band_filter:
                continue
            dlabel = pair_snap[sid]["device_label"]
            tbin = ts_bin(pair_snap[sid]["dt"], bin_sec)
            pair_matched[(bssid, tbin)][dlabel].append(rssi)

        # Keep only bins where BOTH devices are present
        both_present = {
            k: v for k, v in pair_matched.items()
            if la in v and lb in v
        }

        if not both_present:
            pair_overlap_results.append({
                "a": la,
                "b": lb,
                "overlap_min": overlap_min,
                "matched_bins": 0,
                "delta": None,
                "p_raw": None,
                "cliff": None,
                "note": "Overlap but no matched BSSID/bin",
            })
            continue

        a_vals = []
        b_vals = []
        for _, dev_map in both_present.items():
            a_vals.append(float(np.median(dev_map[la])))
            b_vals.append(float(np.median(dev_map[lb])))

        arr_a = np.array(a_vals)
        arr_b = np.array(b_vals)
        delta = float(np.median(arr_b) - np.median(arr_a))
        _, p_raw = mannwhitney(arr_a, arr_b)
        cliff = cliffs_delta(arr_a, arr_b)
        pair_overlap_results.append({
            "a": la,
            "b": lb,
            "overlap_min": overlap_min,
            "matched_bins": len(both_present),
            "delta": delta,
            "p_raw": p_raw,
            "cliff": cliff,
            "note": "OK",
        })

    valid_p = [r for r in pair_overlap_results if r["p_raw"] is not None]
    n_valid_p = len(valid_p)
    for r in pair_overlap_results:
        if r["p_raw"] is None or n_valid_p == 0:
            r["p_adj"] = None
            r["sig"] = "-"
        else:
            p_adj = min(r["p_raw"] * n_valid_p, 1.0)
            r["p_adj"] = p_adj
            r["sig"] = sig_stars(p_adj)

    pair_tbl_headers = [
        "Device A", "Device B", "Overlap (min)", "Matched bins",
        "Δ median (B−A)", "p (adj)", "Cliff's δ", "Sig", "Note"
    ]
    pair_tbl_rows = []
    for r in pair_overlap_results:
        pair_tbl_rows.append([
            pair_disp[r["a"]],
            pair_disp[r["b"]],
            f"{r['overlap_min']:.1f}",
            r["matched_bins"],
            f"{r['delta']:+.2f} dB" if r["delta"] is not None else "N/A",
            fmt_p(r["p_adj"]) if r["p_adj"] is not None else "N/A",
            f"{r['cliff']:+.3f}" if r["cliff"] is not None else "N/A",
            r["sig"],
            r["note"],
        ])
    print()
    print_table(pair_tbl_headers, pair_tbl_rows)

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


    # ------------------------------------------------------------------
    # 0.7  Beacon count analysis
    header("SECTION 0.7 — BEACON COUNT VARIABILITY (EMPTY TESTS)")
    print("""
  Measures how many distinct Wi-Fi beacons each device detected per scan
  snapshot, broken down by band.  A higher count means the device's Wi-Fi
  radio is more sensitive / broader in its scanning behaviour.
""")

    if not beacon_rows:
        print("  No beacon data found for empty campaign.")
    else:
        # Organise per-device lists of (total, 2g, 5g, 6g) counts per snapshot
        bc_by_device: dict[str, dict] = defaultdict(lambda: {
            "dname": "", "total": [], "cnt_2g": [], "cnt_5g": [], "cnt_6g": []
        })
        for dlabel, dname, sid, dt_iso, total, c2, c5, c6 in beacon_rows:
            d = bc_by_device[dlabel]
            d["dname"] = dname
            d["total"].append(total)
            d["cnt_2g"].append(c2)
            d["cnt_5g"].append(c5)
            d["cnt_6g"].append(c6)

        bc_labels = sorted(bc_by_device.keys())
        bc_disp = {
            lbl: display_name(bc_by_device[lbl]["dname"], lbl, aliases)
            for lbl in bc_labels
        }

        # 0.7a  Per-device descriptive stats on total beacons/snapshot
        subheader("0.7a  Beacons per Snapshot — Descriptive Statistics")
        hdr = ["Device", "Snaps", "Total", "Median/snap", "Mean/snap",
               "Std", "Min", "Max", "CV"]
        tbl = []
        bc_arrays: dict[str, np.ndarray] = {}
        for lbl in bc_labels:
            arr = np.array(bc_by_device[lbl]["total"], dtype=float)
            bc_arrays[lbl] = arr
            d = descriptive(arr)
            tbl.append([
                bc_disp[lbl],
                len(arr),
                int(arr.sum()),
                fmt(d["median"], 1),
                fmt(d["mean"], 1),
                fmt(d["std"], 2),
                int(d["min"]),
                int(d["max"]),
                fmt(d["cv"], 3),
            ])
        print()
        print_table(hdr, tbl)

        # 0.7b  Band breakdown
        subheader("0.7b  Band Breakdown (total beacons across all snapshots)")
        band_hdr = ["Device", "Total", "2.4GHz", "2.4 %", "5GHz", "5 %", "6GHz", "6 %"]
        band_tbl = []
        for lbl in bc_labels:
            d   = bc_by_device[lbl]
            tot = int(sum(d["total"]))
            g2  = int(sum(d["cnt_2g"]))
            g5  = int(sum(d["cnt_5g"]))
            g6  = int(sum(d["cnt_6g"]))
            pct = lambda n: f"{100*n/tot:.1f}%" if tot else "N/A"
            band_tbl.append([bc_disp[lbl], tot,
                             g2, pct(g2),
                             g5, pct(g5),
                             g6, pct(g6)])
        print()
        print_table(band_hdr, band_tbl)

        # 0.7c  Kruskal-Wallis + pairwise on total beacons/snapshot
        subheader("0.7c  Statistical Comparison of Beacons/Snapshot Across Devices")
        valid_bc = [lbl for lbl in bc_labels if len(bc_arrays.get(lbl, [])) > 1]
        if len(valid_bc) >= 2:
            H_bc, p_kw_bc = kruskal_wallis(*[bc_arrays[lbl] for lbl in valid_bc])
            print(f"\n  Kruskal-Wallis: H = {H_bc:.4f},  p = {fmt_p(p_kw_bc)} {sig_stars(p_kw_bc)}")
            if p_kw_bc < 0.05:
                print("  → Significant differences in beacon detection across devices.")
            else:
                print("  → No significant difference detected.")

            bc_pairs    = list(combinations(valid_bc, 2))
            n_bc_pairs  = len(bc_pairs)
            print(f"\n  Pairwise comparisons (Bonferroni α = {0.05/n_bc_pairs:.4f}):\n")
            pw_hdr = ["Device A", "Device B", "Δ median",
                      "95% CI", "p (raw)", "p (adj)", "sig", "Cliff's δ", "effect"]
            pw_rows = []
            for la, lb in bc_pairs:
                obs, ci_lo, ci_hi = bootstrap_median_ci(bc_arrays[la], bc_arrays[lb])
                _, p_raw = mannwhitney(bc_arrays[la], bc_arrays[lb])
                p_adj   = min(p_raw * n_bc_pairs, 1.0)
                cd      = cliffs_delta(bc_arrays[la], bc_arrays[lb])
                pw_rows.append([
                    bc_disp[la], bc_disp[lb],
                    f"{obs:+.1f}",
                    f"[{ci_lo:+.1f}, {ci_hi:+.1f}]",
                    fmt_p(p_raw), fmt_p(p_adj),
                    sig_stars(p_adj),
                    f"{cd:+.3f}",
                    effect_label_cliff(cd),
                ])
            print_table(pw_hdr, pw_rows)

        # 0.7d  Ranking
        subheader("0.7d  Device Ranking by Median Beacons/Snapshot")
        print()
        ranked_bc = sorted(
            [(lbl, float(np.median(bc_arrays[lbl]))) for lbl in valid_bc],
            key=lambda x: x[1], reverse=True
        )
        rank_hdr = ["Rank", "Device", "Median beacons/snap", "6 GHz beacons", "6 GHz %"]
        rank_tbl = []
        for rank, (lbl, med) in enumerate(ranked_bc, 1):
            d   = bc_by_device[lbl]
            tot = int(sum(d["total"]))
            g6  = int(sum(d["cnt_6g"]))
            pct6 = f"{100*g6/tot:.1f}%" if tot else "N/A"
            rank_tbl.append([rank, bc_disp[lbl], f"{med:.1f}", g6, pct6])
        print_table(rank_hdr, rank_tbl)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db",      default=DB_DEFAULT,
                        help=f"DuckDB path (default: {DB_DEFAULT})")
    parser.add_argument("--bin-sec", type=int, default=DEFAULT_BIN_SEC,
                        help=f"Time-bin width in seconds (default: {DEFAULT_BIN_SEC})")
    parser.add_argument("--band",    default=None,
                        choices=["all", "2.4GHz", "5GHz", "6GHz"],
                        help="Band mode: default runs 5GHz then 6GHz separately; use 'all' to combine")
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
        if args.band is None:
            print("Running separate RSSI analyses for 5GHz and 6GHz to avoid mixed-band skew.\n")
            print("#" * 78)
            print("# PASS 1: 5GHz only")
            print("#" * 78)
            run(db_path, args.bin_sec, "5GHz", aliases)
            print("\n" + "#" * 78)
            print("# PASS 2: 6GHz only")
            print("#" * 78)
            run(db_path, args.bin_sec, "6GHz", aliases)
        elif args.band == "all":
            run(db_path, args.bin_sec, None, aliases)
        else:
            run(db_path, args.bin_sec, args.band, aliases)

    print(f"\nOutput saved to: {OUT_FILE}")


if __name__ == "__main__":
    main()
