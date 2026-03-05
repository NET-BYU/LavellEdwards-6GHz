#!/usr/bin/env python3
"""
device_identify.py

Queries the stadium.duckdb database to map device identifiers (UUID and
device_name from SigCap) back to the physical phone type recorded in the
EMPTY_ folder names.

Usage:
  python device_identify.py [--db DB_PATH] [--all] [--format json|yaml]

Options:
  --db  PATH         Path to DuckDB file (default: ./stadium.duckdb)
  --all              Show devices from ALL campaign types, not just 'empty'
  --format FORMAT    Output file format: json or yaml (default: json)
"""

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

import duckdb

GREEN = '\033[0;32m'
BLUE  = '\033[0;34m'
YELLOW = '\033[1;33m'
NC    = '\033[0m'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', default='./stadium.duckdb',
                        help='Path to DuckDB file (default: ./stadium.duckdb)')
    parser.add_argument('--all', action='store_true',
                        help='Show devices from all campaign types, not just empty')
    parser.add_argument('--format', choices=['json', 'yaml'], default='json',
                        help='Output file format (default: json)')
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    con = duckdb.connect(str(db_path), read_only=True)

    campaign_filter = "" if args.all else "WHERE campaign_type = 'empty'"

    query = f"""
        SELECT
            folder_name,
            section         AS phone_label,
            uuid,
            device_name,
            COUNT(*)        AS snapshot_count
        FROM snapshots
        {campaign_filter}
        GROUP BY folder_name, section, uuid, device_name
        ORDER BY folder_name, uuid
    """

    rows = con.execute(query).fetchall()
    con.close()

    if not rows:
        label = "any campaign" if args.all else "EMPTY_ folders"
        print(f"{YELLOW}No device records found for {label}.{NC}")
        print("Make sure ingest_to_duckdb.py has been run first.")
        sys.exit(0)

    # Column widths
    folder_w  = max(len(r[0]) for r in rows)
    label_w   = max(len(str(r[1])) for r in rows)
    uuid_w    = max(len(str(r[2])) for r in rows)
    device_w  = max(len(str(r[3])) for r in rows)

    header = (f"{'FOLDER':<{folder_w}}  {'LABEL':<{label_w}}  "
              f"{'UUID':<{uuid_w}}  {'DEVICE NAME':<{device_w}}  SNAPSHOTS")
    print(f"\n{BLUE}{header}{NC}")
    print("-" * len(header))

    current_folder = None
    for folder_name, phone_label, uuid, device_name, count in rows:
        if folder_name != current_folder:
            if current_folder is not None:
                print()
            current_folder = folder_name

        print(f"{GREEN}{folder_name:<{folder_w}}{NC}  "
              f"{str(phone_label):<{label_w}}  "
              f"{str(uuid):<{uuid_w}}  "
              f"{str(device_name):<{device_w}}  "
              f"{count}")

    print()

    # Build structured map: { folder_name: [ {label, uuid, device_name, snapshots} ] }
    device_map = defaultdict(list)
    for folder_name, phone_label, uuid, device_name, count in rows:
        device_map[folder_name].append({
            'label':          phone_label,
            'uuid':           uuid,
            'device_name':    device_name,
            'custom_name':    '',
            'snapshot_count': count,
        })

    out_data = dict(device_map)
    out_path = Path(args.db).parent / f"device_map.{args.format}"

    if args.format == 'yaml':
        try:
            import yaml
        except ImportError:
            print(f"{YELLOW}PyYAML not installed. Run: pip install pyyaml{NC}")
            sys.exit(1)
        with open(out_path, 'w') as f:
            yaml.dump(out_data, f, default_flow_style=False, sort_keys=True)
    else:
        with open(out_path, 'w') as f:
            json.dump(out_data, f, indent=2)

    print(f"{GREEN}Device map written to: {out_path}{NC}")

    # --- Secondary aliases file: unique device_name -> custom_name ---
    # Collect all unique device names across all rows
    unique_device_names = sorted({r[3] for r in rows if r[3]})

    aliases_path = Path(args.db).parent / "device_aliases.json"

    # Preserve any custom_names the user has already set
    existing_aliases = {}
    if aliases_path.exists():
        try:
            with open(aliases_path) as f:
                existing_aliases = json.load(f)
        except Exception:
            pass

    aliases = {
        name: existing_aliases.get(name, '') for name in unique_device_names
    }

    with open(aliases_path, 'w') as f:
        json.dump(aliases, f, indent=2)

    print(f"{GREEN}Device aliases written to: {aliases_path}{NC}\n")


if __name__ == '__main__':
    main()
