"""
01_exif_audit.py
────────────────
Reads EXIF metadata from all RAW files in input_dir, maps each file to its
camera group (based on filename prefix), flags anomalies (no-flash frames,
unexpected exposure settings), and writes a full audit CSV to the run folder.

This is the first step in the pipeline and has no dependencies on other steps.
Run it first to understand what you're working with before processing anything.

Output: runs/YYYYMMDD_HHMMSS/exif_audit.csv
"""

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import exiftool
import yaml


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def detect_group(filename: str, groups: dict) -> str:
    """Detect camera group from filename prefix. Returns 'unknown' if no match."""
    for prefix in groups:
        if filename.upper().startswith(prefix.upper()):
            return prefix
    return "unknown"


def flag_anomalies(row: dict, config: dict) -> list[str]:
    """Return a list of anomaly flags for a given EXIF row."""
    flags = []
    flash = str(row.get("FlashMode", "")).lower()
    if "did not fire" in flash:
        flags.append("NO_FLASH")
    if row.get("group") == "unknown":
        flags.append("UNKNOWN_GROUP")
    return flags


def run():
    config = load_config()
    input_dir = Path(config["input_dir"])
    output_base = Path(config["output_dir"])

    # Create timestamped run folder
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write run metadata
    with open(run_dir / "run_info.json", "w") as f:
        json.dump({"run_id": run_id, "input_dir": str(input_dir), "step": "01_exif_audit"}, f, indent=2)

    # Collect all RAW files
    raw_files = sorted(
        list(input_dir.glob("*.NEF")) +
        list(input_dir.glob("*.nef")) +
        list(input_dir.glob("*.DNG")) +
        list(input_dir.glob("*.dng"))
    )

    if not raw_files:
        print(f"[ERROR] No RAW files found in {input_dir}")
        sys.exit(1)

    print(f"[01] Found {len(raw_files)} RAW files in {input_dir}")

    # Extract EXIF with exiftool
    tags = [
        "FileName", "Model", "LensInfo", "FocalLength",
        "ISO", "ExposureTime", "FNumber",
        "WhiteBalance", "ColorSpace", "FlashMode", "CreateDate"
    ]

    rows = []
    with exiftool.ExifToolHelper() as et:
        metadata = et.get_tags([str(f) for f in raw_files], tags=tags)

    groups = config["camera_groups"]

    for meta in metadata:
        filename = Path(meta.get("File:FileName", "")).name
        group = detect_group(filename, groups)
        row = {
            "filename":      filename,
            "group":         group,
            "camera":        groups.get(group, {}).get("camera", "unknown"),
            "card":          groups.get(group, {}).get("card", "unknown"),
            "model":         meta.get("EXIF:Model", ""),
            "focal_length":  meta.get("EXIF:FocalLength", ""),
            "iso":           meta.get("EXIF:ISO", ""),
            "exposure_time": meta.get("EXIF:ExposureTime", ""),
            "fnumber":       meta.get("EXIF:FNumber", ""),
            "white_balance": meta.get("EXIF:WhiteBalance", ""),
            "flash_mode":    meta.get("EXIF:FlashMode", ""),
            "create_date":   meta.get("EXIF:CreateDate", ""),
        }
        row["anomalies"] = "|".join(flag_anomalies(row, config))
        rows.append(row)

    # Write audit CSV
    out_path = run_dir / "exif_audit.csv"
    fieldnames = ["filename", "group", "camera", "card", "model", "focal_length",
                  "iso", "exposure_time", "fnumber", "white_balance", "flash_mode",
                  "create_date", "anomalies"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    total = len(rows)
    no_flash = sum(1 for r in rows if "NO_FLASH" in r["anomalies"])
    unknown = sum(1 for r in rows if "UNKNOWN_GROUP" in r["anomalies"])
    by_group = {}
    for r in rows:
        by_group[r["group"]] = by_group.get(r["group"], 0) + 1

    print(f"[01] Audit complete → {out_path}")
    print(f"[01] Total files : {total}")
    print(f"[01] By group    : {by_group}")
    print(f"[01] No-flash    : {no_flash} frames flagged")
    print(f"[01] Unknown grp : {unknown} frames flagged")
    print(f"[01] Run ID      : {run_id}  (use this for subsequent steps)")


if __name__ == "__main__":
    run()
