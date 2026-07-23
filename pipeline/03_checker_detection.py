"""
03_checker_detection.py
────────────────────────
Scans RAW files for a visible Calibrite ColorChecker Passport (24-patch chart)
using colour-checker-detection. Designed to run against the FULL dataset
(not just a local sample) since checker visibility is rare and we need at
least one good detection per camera group to compute a correction matrix.

Two phases:
  Phase 1 — FAST SCAN: decode every RAW at half-size, attempt detection, keep
            hits above checker_confidence_min. This is the slow part on the full
            dataset, so it runs in PARALLEL across scan_workers processes.
  Phase 2 — HIGH-QUALITY RE-DECODE: for each phase-1 hit, re-decode that file at
            full resolution / 16-bit and re-run detection to get accurate patch
            RGB values for the correction matrix (step 04).

Resumability (the reason this step is safe to run on ~2500 files over a slow
link — see the project brief's "full NAS checker scan"):
  Every phase-1 result and every phase-2 patch set is appended to a cache file
  in the run folder AS IT COMPLETES:
    scan_cache.jsonl     — one line per file scanned in phase 1
    patches_cache.jsonl  — one line per hit re-decoded in phase 2
  Re-running with the SAME run_id skips any file already in the cache whose
  path + size + mtime still match. So a crash, network drop, or Ctrl-C loses at
  most the handful of files in flight — just re-run the same command to continue.

Usage:
  uv run python pipeline/03_checker_detection.py            # start a NEW run
  uv run python pipeline/03_checker_detection.py 20260723_143000   # RESUME that run

Reads:  input_dir from config.yaml (point at the full dataset for a full scan)
Writes: {output_dir}/{run_id}/checker_detections.json
        {output_dir}/{run_id}/scan_cache.jsonl     (resumable phase-1 cache)
        {output_dir}/{run_id}/patches_cache.jsonl  (resumable phase-2 cache)

Each entry in checker_detections.json contains:
  filename, filepath, group, confidence, patch_values (24 RGB triplets)
"""

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
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

# Force UTF-8 on stdout/stderr so status lines with ΔE / → / × don't crash on a
# legacy Windows console codepage (cp1252) or when output is piped/redirected.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # already utf-8, or a stream that doesn't support reconfigure


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
        # crude confidence proxy: did we get exactly 24 patches back? The REAL
        # quality gate is the fit residual computed in step 04, not this number.
        confidence = 1.0 if len(swatches) == 24 else 0.5
        return True, swatches, confidence
    except Exception:
        return False, None, 0.0


# ── Resumable cache helpers ──────────────────────────────────────────────────

def stat_key(path: Path):
    """Cheap identity of a file's current state: (size, mtime). If either changes
    the cached result is treated as stale and the file is re-scanned."""
    st = path.stat()
    return st.st_size, int(st.st_mtime)


def load_cache(cache_path: Path) -> dict:
    """Read a JSONL cache into {filepath: record}. Tolerates a truncated final
    line (a crash mid-write) by skipping any line that won't parse."""
    done = {}
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # partial trailing line from an interrupted run
                done[rec["filepath"]] = rec
    return done


def is_fresh(rec: dict, path: Path) -> bool:
    """True if a cached record still matches the file on disk."""
    try:
        size, mtime = stat_key(path)
    except OSError:
        return False
    return rec.get("size") == size and rec.get("mtime") == mtime


# ── Phase-1 worker (runs in its own process — must be module-level & picklable) ─

def scan_one(task):
    """
    Decode one RAW at half-size and attempt checker detection. Returns a cache
    record. Any decode/detection error is swallowed into found=False so one bad
    file never kills the pool.
    """
    filepath, groups, confidence_min = task
    path = Path(filepath)
    try:
        size, mtime = stat_key(path)
    except OSError:
        size, mtime = -1, -1
    try:
        img = decode_fast(path)
        found, _swatches, confidence = try_detect(img)
    except Exception:
        found, confidence = False, 0.0
    return {
        "filepath": filepath,
        "filename": path.name,
        "group": detect_group(path.name, groups),
        "size": size,
        "mtime": mtime,
        "confidence": float(confidence),
        # a "hit" is a successful detection at/above the configured confidence
        "hit": bool(found and confidence >= confidence_min),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def run(run_id: str = None):
    config = load_config()
    input_dir = Path(config["input_dir"])
    output_base = Path(config["output_dir"])
    groups = config["camera_groups"]
    confidence_min = config.get("checker_confidence_min", 0.85)
    workers = int(config.get("scan_workers", 8))
    progress_every = int(config.get("scan_progress_every", 50))

    # No run_id -> new run. A run_id that already exists -> resume into it.
    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_base / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    scan_cache_path = run_dir / "scan_cache.jsonl"
    patches_cache_path = run_dir / "patches_cache.jsonl"

    # Collect RAW files recursively — designed to scan the full nested dataset.
    RAW_EXTENSIONS = {".nef", ".dng"}
    raw_files = sorted([
        f for f in input_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in RAW_EXTENSIONS
    ])
    if not raw_files:
        print(f"[ERROR] No RAW files found in {input_dir}")
        sys.exit(1)

    # ── PHASE 1 ──────────────────────────────────────────────────────────────
    scan_done = load_cache(scan_cache_path)
    to_scan, skipped = [], 0
    for f in raw_files:
        rec = scan_done.get(str(f))
        if rec is not None and is_fresh(rec, f):
            skipped += 1
        else:
            to_scan.append(f)

    print(f"[03] PHASE 1 — fast scan")
    print(f"[03]   input_dir : {input_dir}")
    print(f"[03]   run_id    : {run_id}")
    print(f"[03]   {len(raw_files)} RAW files found, "
          f"{skipped} already cached, {len(to_scan)} to scan "
          f"({workers} parallel workers)")

    start = time.time()
    n_new_hits = 0
    if to_scan:
        # Append each result the moment it lands — this is what makes the run
        # crash-safe. flush() forces it to disk so a kill loses nothing prior.
        with open(scan_cache_path, "a", encoding="utf-8") as cache_f, \
                ProcessPoolExecutor(max_workers=workers) as pool:
            tasks = [(str(f), groups, confidence_min) for f in to_scan]
            futures = {pool.submit(scan_one, t): t[0] for t in tasks}
            for i, fut in enumerate(as_completed(futures)):
                rec = fut.result()
                scan_done[rec["filepath"]] = rec
                cache_f.write(json.dumps(rec) + "\n")
                cache_f.flush()
                if rec["hit"]:
                    n_new_hits += 1
                    print(f"[03] HIT: {rec['filename']} "
                          f"(group={rec['group']}, confidence={rec['confidence']:.2f})")
                if (i + 1) % progress_every == 0:
                    elapsed = time.time() - start
                    rate = (i + 1) / elapsed if elapsed > 0 else 0
                    remaining = (len(to_scan) - (i + 1)) / rate if rate > 0 else 0
                    print(f"[03]   {i + 1}/{len(to_scan)} scanned "
                          f"({rate:.1f} files/sec, ~{remaining/60:.1f} min remaining)")

    # Assemble candidates from the FULL cache (cached + freshly scanned), in a
    # stable order matching the file list.
    candidates = []
    for f in raw_files:
        rec = scan_done.get(str(f))
        if rec is not None and rec.get("hit"):
            candidates.append(rec)

    print(f"[03] PHASE 1 complete — {len(candidates)} candidate frame(s) "
          f"({n_new_hits} new this run)")

    by_group = {}
    for c in candidates:
        by_group.setdefault(c["group"], []).append(c)
    for g, hits in sorted(by_group.items()):
        print(f"[03]   {g}: {len(hits)} hit(s)")

    missing_groups = [g for g in groups if g not in by_group]
    if missing_groups:
        print(f"[03] WARNING: no checker detected for groups: {missing_groups}")
        print(f"[03]   These groups will need a fallback correction (see step 04).")

    # ── PHASE 2 ──────────────────────────────────────────────────────────────
    # Full re-decode of each hit for accurate patch values. Also cached, so a
    # crash partway through the (potentially slow) full decodes doesn't restart.
    print(f"[03] PHASE 2 — high-quality re-decode of {len(candidates)} candidate(s)")

    patch_done = load_cache(patches_cache_path)
    final_results = []
    with open(patches_cache_path, "a", encoding="utf-8") as pcache_f:
        for i, cand in enumerate(candidates):
            fp = cand["filepath"]
            path = Path(fp)

            cached = patch_done.get(fp)
            if cached is not None and is_fresh(cached, path):
                final_results.append({
                    "filename": cached["filename"],
                    "filepath": fp,
                    "group": cached["group"],
                    "confidence": cached["confidence"],
                    "patch_values": cached["patch_values"],
                })
                print(f"[03]   {i + 1}/{len(candidates)} cached — {cached['filename']}")
                continue

            try:
                img_hq = decode_full(path)
                found, swatches, confidence = try_detect(img_hq)
            except Exception as e:
                print(f"[03] WARNING: phase 2 failed for {cand['filename']}: {e}")
                swatches, confidence = None, 0.0

            try:
                size, mtime = stat_key(path)
            except OSError:
                size, mtime = -1, -1

            rec = {
                "filepath": fp,
                "filename": cand["filename"],
                "group": cand["group"],
                "size": size,
                "mtime": mtime,
                "confidence": float(confidence),
                "patch_values": swatches,
            }
            pcache_f.write(json.dumps(rec) + "\n")
            pcache_f.flush()
            final_results.append({
                "filename": cand["filename"],
                "filepath": fp,
                "group": cand["group"],
                "confidence": float(confidence),
                "patch_values": swatches,
            })
            print(f"[03]   {i + 1}/{len(candidates)} re-decoded — "
                  f"{cand['filename']} confidence={confidence:.2f}")

    # Write the consolidated output step 04 reads.
    out_path = run_dir / "checker_detections.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id,
            "input_dir": str(input_dir),
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
