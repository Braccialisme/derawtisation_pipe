"""
02_blur_culling.py
──────────────────
Scores every RAW file for sharpness using Laplacian variance on a fast
half-size rawpy decode. Frames below the blur_threshold in config.yaml,
or flagged NO_FLASH in the exif audit, are marked for rejection.

Reads:  runs/{run_id}/exif_audit.csv
Writes: runs/{run_id}/blur_scores.csv

Each row in blur_scores.csv contains:
  filename, group, laplacian_score, flash_ok, decision (keep/reject), reason
"""

import csv
import sys
from pathlib import Path

import cv2
import numpy as np
import rawpy
import yaml


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def laplacian_score(image_array: np.ndarray) -> float:
    """
    Compute sharpness as variance of Laplacian on a grayscale image.
    Higher = sharper. This is the standard blur detection metric.
    """
    gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def decode_fast(raw_path: Path) -> np.ndarray:
    """
    Decode RAW file at half resolution for fast sharpness scoring.
    No color correction applied — we only need luminance structure.
    """
    with rawpy.imread(str(raw_path)) as raw:
        return raw.postprocess(
            half_size=True,
            no_auto_bright=True,
            output_bps=8,
            use_camera_wb=True,
        )


def run(run_id: str):
    config = load_config()
    input_dir = Path(config["input_dir"])
    run_dir = Path(config["output_dir"]) / run_id
    audit_path = run_dir / "exif_audit.csv"

    if not audit_path.exists():
        print(f"[ERROR] exif_audit.csv not found in {run_dir}")
        print("[ERROR] Run 01_exif_audit.py first.")
        sys.exit(1)

    # Load audit to get anomaly flags
    audit = {}
    with open(audit_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            audit[row["filename"]] = row

    blur_threshold = config["blur_threshold"]
    reject_no_flash = config["reject_no_flash"]

    results = []
    raw_files = sorted(
        list(input_dir.glob("*.NEF")) +
        list(input_dir.glob("*.nef")) +
        list(input_dir.glob("*.DNG")) +
        list(input_dir.glob("*.dng"))
    )

    print(f"[02] Scoring {len(raw_files)} files (blur threshold: {blur_threshold})")

    for i, raw_path in enumerate(raw_files):
        filename = raw_path.name
        meta = audit.get(filename, {})
        anomalies = meta.get("anomalies", "")
        group = meta.get("group", "unknown")

        # Check no-flash flag from audit
        flash_ok = "NO_FLASH" not in anomalies

        # Compute sharpness score
        try:
            img = decode_fast(raw_path)
            score = laplacian_score(img)
        except Exception as e:
            print(f"[02] WARNING: could not decode {filename}: {e}")
            score = 0.0

        # Decision logic
        if reject_no_flash and not flash_ok:
            decision = "reject"
            reason = "no_flash"
        elif score < blur_threshold:
            decision = "reject"
            reason = f"blurry (score={score:.1f})"
        else:
            decision = "keep"
            reason = f"ok (score={score:.1f})"

        results.append({
            "filename":        filename,
            "group":           group,
            "laplacian_score": round(score, 2),
            "flash_ok":        flash_ok,
            "decision":        decision,
            "reason":          reason,
        })

        if (i + 1) % 10 == 0:
            print(f"[02]   {i + 1}/{len(raw_files)} scored...")

    # Write results
    out_path = run_dir / "blur_scores.csv"
    fieldnames = ["filename", "group", "laplacian_score", "flash_ok", "decision", "reason"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Summary
    kept = sum(1 for r in results if r["decision"] == "keep")
    rejected = len(results) - kept
    no_flash_rej = sum(1 for r in results if r["reason"] == "no_flash")
    blur_rej = rejected - no_flash_rej

    print(f"[02] Culling complete → {out_path}")
    print(f"[02] Kept     : {kept}/{len(results)}")
    print(f"[02] Rejected : {rejected} ({blur_rej} blurry, {no_flash_rej} no-flash)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python pipeline/02_blur_culling.py <run_id>")
        print("  run_id is the YYYYMMDD_HHMMSS folder name from step 01")
        sys.exit(1)
    run(sys.argv[1])
