"""
03_checker_detection.py
────────────────────────
Scans RAW files for a visible Calibrite ColorChecker Passport (24-patch chart)
using colour-checker-detection. Designed to run against the FULL dataset
(not just a local sample) since checker visibility is rare and we need at
least one good detection per camera group to compute a correction matrix.

This script runs in two phases:
  Phase 1 — FAST SCAN: decode every RAW at half-size, attempt detection,
            keep only hits above checker_confidence_min from config.yaml.
  Phase 2 — HIGH-QUALITY RE-DECODE: for every hit found in phase 1, re-decode
            that specific file at full quality and re-run detection to get
            accurate patch RGB values for the correction matrix step (04).

Reads:  input_dir from config.yaml (point this at the NAS root for a full scan)
Writes: runs/{run_id}/checker_detections.json

Each entry in checker_detections.json contains:
  filename, group, confidence, patch_values (24 RGB triplets), swatch_centers
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import rawpy
import yaml

try:
    from colour_checker_detection import detect_colour_checkers_segmentation
except ImportError:
    print("[ERROR] colour-checker-detection not installed. Run: uv sync")
    sys.exit(1)


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def detect_group(filename: str, groups: dict) -> str:
    for prefix in groups:
        if filename.upper().startswith(prefix.upper()):
            return prefix
    return "unknown"


def decode_fast(raw_path: Path) -> np.ndarray:
    """Half-size decode for fast scanning — speed over precision."""
    with rawpy.imread(str(raw_path)) as raw:
        return raw.postprocess(
            half_size=True,
            no_auto_bright=True,
            output_bps=8,
            use_camera_wb=True,
        )


def decode_full(raw_path: Path) -> np.ndarray:
    """Full resolution decode for accurate patch color measurement."""
    with rawpy.imread(str(raw_path)) as raw:
        return raw.postprocess(
            half_size=False,
            no_auto_bright=True,
            output_bps=16,
            use_camera_wb=True,
        )


def try_detect(image_array: np.ndarray):
    """
    Run colour-checker-detection on an image array.
    Returns (success: bool, swatches: list or None, confidence: float)
    """
    try:
        # additional_data=True gives us back the detection details
        result = detect_colour_checkers_segmentation(
            image_array, additional_data=True
        )
        if not result:
            return False, None, 0.0
        # result is a list of DataDetectionColourChecker — one per checker found
        checker = result[0]
        swatches = checker.swatch_colours.tolist()
        # crude confidence proxy: did we get exactly 24 patches back?
        confidence = 1.0 if len(swatches) == 24 else 0.5
        return True, swatches, confidence
    except Exception:
        return False, None, 0.0


def run(run_id: str = None):
    config = load_config()
    input_dir = Path(config["input_dir"])
    output_base = Path(config["output_dir"])
    groups = config["camera_groups"]
    confidence_min = config.get("checker_confidence_min", 0.85)

    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Collect RAW files recursively — this is designed to scan the full NAS tree
    RAW_EXTENSIONS = {".nef", ".dng"}
    raw_files = sorted([
        f for f in input_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in RAW_EXTENSIONS
    ])

    if not raw_files:
        print(f"[ERROR] No RAW files found in {input_dir}")
        sys.exit(1)

    print(f"[03] PHASE 1 — fast scan of {len(raw_files)} RAW files")
    print(f"[03] This will take a while on a large dataset. Progress every 50 files.")

    start = time.time()
    candidates = []

    for i, raw_path in enumerate(raw_files):
        filename = raw_path.name
        group = detect_group(filename, groups)

        try:
            img = decode_fast(raw_path)
            found, swatches, confidence = try_detect(img)
        except Exception as e:
            found = False
            confidence = 0.0

        if found and confidence >= confidence_min:
            candidates.append({
                "filename": filename,
                "filepath": str(raw_path),
                "group": group,
                "confidence": confidence,
            })
            print(f"[03] HIT: {filename} (group={group}, confidence={confidence:.2f})")

        if (i + 1) % 50 == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed
            remaining = (len(raw_files) - (i + 1)) / rate if rate > 0 else 0
            print(f"[03]   {i + 1}/{len(raw_files)} scanned "
                  f"({rate:.1f} files/sec, ~{remaining/60:.1f} min remaining) "
                  f"— {len(candidates)} hits so far")

    print(f"[03] PHASE 1 complete — {len(candidates)} candidate frames found")

    by_group = {}
    for c in candidates:
        by_group.setdefault(c["group"], []).append(c)
    for g, hits in by_group.items():
        print(f"[03]   {g}: {len(hits)} hits")

    missing_groups = [g for g in groups if g not in by_group]
    if missing_groups:
        print(f"[03] WARNING: no checker detected for groups: {missing_groups}")
        print(f"[03]   These groups will need a fallback correction strategy (see step 04).")

    # PHASE 2 — high quality re-decode for each candidate
    print(f"[03] PHASE 2 — high-quality re-decode of {len(candidates)} candidates")

    final_results = []
    for i, cand in enumerate(candidates):
        raw_path = Path(cand["filepath"])
        try:
            img_hq = decode_full(raw_path)
            found, swatches, confidence = try_detect(img_hq)
        except Exception as e:
            print(f"[03] WARNING: phase 2 failed for {cand['filename']}: {e}")
            found = False
            swatches = None
            confidence = 0.0

        final_results.append({
            "filename": cand["filename"],
            "filepath": cand["filepath"],
            "group": cand["group"],
            "confidence": confidence,
            "patch_values": swatches,
        })
        print(f"[03]   {i + 1}/{len(candidates)} re-decoded — "
              f"{cand['filename']} confidence={confidence:.2f}")

    # Write output JSON
    out_path = run_dir / "checker_detections.json"
    with open(out_path, "w") as f:
        json.dump({
            "run_id": run_id,
            "total_scanned": len(raw_files),
            "total_candidates": len(candidates),
            "missing_groups": missing_groups,
            "detections": final_results,
        }, f, indent=2)

    print(f"[03] Done → {out_path}")
    print(f"[03] Run ID: {run_id} (use this for subsequent steps)")


if __name__ == "__main__":
    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    run(run_id)