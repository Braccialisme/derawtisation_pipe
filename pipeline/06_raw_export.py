"""
06_raw_export.py
────────────────
Renders the final, colour-corrected JPEGs — the pipeline's deliverable for
RealityCapture / Metashape. This is the hybrid render path (DECISIONS.md D012):

  for each frame in render_manifest.json (step 05):
    RawTherapee develops the RAW to a neutral 16-bit sRGB TIFF (develop.pp3)
    → Python applies the group's linear CCM and writes JPEG (quality from config).

Runs `render_workers` frames in parallel. It is resumable: a frame whose output
JPEG already exists is skipped, so a killed or interrupted batch continues where
it left off just by re-running the same command. The intermediate 16-bit TIFFs
are large and are deleted immediately after their JPEG is written.

Reads:
  config.yaml
  {output_dir}/{run_id}/render_manifest.json   (step 05)
  {output_dir}/{run_id}/develop.pp3            (step 05)
Writes:
  {output_dir}/{run_id}/export_jpegs/*.jpg
"""

import json
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from render_common import develop_to_tiff, apply_ccm_to_tiff, write_jpeg

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def render_one(frame, rt_cli, pp3_path, jpeg_dir, tiff_dir, quality):
    """
    Develop + correct one frame. Returns (filename, status_string).
    status: "rendered" | "skipped" | "error: ..."
    """
    filename = frame["filename"]
    dest = jpeg_dir / (Path(filename).stem + ".jpg")
    if dest.exists():
        return filename, "skipped"

    raw_path = Path(frame["filepath"])
    tif_path = None
    try:
        tif_path = develop_to_tiff(rt_cli, raw_path, pp3_path, tiff_dir)
        rgb = apply_ccm_to_tiff(tif_path, frame["ccm"])
        if rgb is None:
            return filename, "error: TIFF unreadable"
        write_jpeg(rgb, dest, quality)
        return filename, "rendered"
    except Exception as e:
        return filename, f"error: {e}"
    finally:
        # The 16-bit TIFF is large — drop it as soon as the JPEG is written.
        if tif_path is not None and Path(tif_path).exists():
            try:
                Path(tif_path).unlink()
            except OSError:
                pass


def run(run_id: str):
    config = load_config()
    run_dir = Path(config["output_dir"]) / run_id
    rt_cli = config["rawtherapee_cli"]
    quality = config.get("jpeg_quality", 95)
    workers = int(config.get("render_workers", 6))

    manifest_path = run_dir / "render_manifest.json"
    pp3_path = run_dir / "develop.pp3"
    if not manifest_path.exists():
        print(f"[06][ERROR] {manifest_path} not found — run step 05 first.")
        sys.exit(1)
    if not Path(rt_cli).exists():
        print(f"[06][ERROR] RawTherapee CLI not found at {rt_cli} "
              f"— fix rawtherapee_cli in config.yaml.")
        sys.exit(1)

    frames = json.load(open(manifest_path))["frames"]
    jpeg_dir = run_dir / "export_jpegs"
    jpeg_dir.mkdir(parents=True, exist_ok=True)

    already = sum(1 for fr in frames if (jpeg_dir / (Path(fr["filename"]).stem + ".jpg")).exists())
    todo = len(frames) - already
    print(f"[06] {len(frames)} frame(s) in manifest, {already} already rendered, "
          f"{todo} to render ({workers} parallel workers)")

    start = time.time()
    counts = {"rendered": 0, "skipped": 0, "error": 0}
    # Processes, not threads: OpenCV + rawtherapee-cli subprocesses run concurrently
    # and segfault under a thread pool on Windows. Separate processes isolate them
    # (same reason step 03 uses ProcessPoolExecutor). render_one is module-level so
    # it pickles cleanly to the workers.
    with tempfile.TemporaryDirectory() as td, \
            ProcessPoolExecutor(max_workers=workers) as pool:
        tiff_dir = Path(td)
        futures = [
            pool.submit(render_one, fr, rt_cli, pp3_path, jpeg_dir, tiff_dir, quality)
            for fr in frames
        ]
        done = 0
        for fut in as_completed(futures):
            filename, status = fut.result()
            done += 1
            if status == "rendered":
                counts["rendered"] += 1
            elif status == "skipped":
                counts["skipped"] += 1
            else:
                counts["error"] += 1
                print(f"[06] ERROR rendering {filename}: {status[7:]}")
            if done % 25 == 0 or done == len(frames):
                elapsed = time.time() - start
                rate = counts["rendered"] / elapsed if elapsed > 0 else 0
                print(f"[06]   {done}/{len(frames)} processed "
                      f"({counts['rendered']} rendered, {rate:.2f}/sec)")

    print(f"[06] Export complete → {jpeg_dir}")
    print(f"[06]   rendered {counts['rendered']}, skipped {counts['skipped']} "
          f"(already existed), errors {counts['error']}")
    print(f"[06] Run ID: {run_id} (use this for step 07)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python pipeline/06_raw_export.py <run_id>")
        sys.exit(1)
    run(sys.argv[1])
