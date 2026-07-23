"""
05_develop_profile.py
─────────────────────
Prepares everything step 06 needs to render the final JPEGs, without doing any
heavy decoding itself:

  1. Writes the shared neutral RawTherapee develop profile (`develop.pp3`) — see
     pp3_common.build_develop_pp3 and DECISIONS.md D012 (hybrid render path).
  2. Builds `render_manifest.json` — the list of frames to export, each paired
     with the 3×3 CCM its camera group resolved to in step 04.

Which frames make the manifest:
  * KEPT by step 02 (blur_scores.csv decision == "keep"), i.e. sharp and flash-lit.
  * belonging to a group whose normalization status is usable (trusted /
    low_confidence / borrowed). Frames of a "failed" group are dropped with a
    warning — we have no colour correction for them.

Reads:
  config.yaml
  {output_dir}/{run_id}/normalization.json   (step 04)
  {output_dir}/{run_id}/blur_scores.csv      (step 02)
Writes:
  {output_dir}/{run_id}/develop.pp3
  {output_dir}/{run_id}/render_manifest.json
"""

import csv
import json
import sys
from pathlib import Path

import yaml

# Import the shared develop-profile builder (pipeline/ is not a package, so add it
# to the path — same trick used by the tools/ scripts).
sys.path.insert(0, str(Path(__file__).parent))
from pp3_common import build_develop_pp3

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def index_raw_files(input_dir: Path) -> dict:
    """Map filename -> full path for every RAW under input_dir (recursive).
    Step 02's CSV only stores the basename; we need the real path to render."""
    RAW_EXTENSIONS = {".nef", ".dng"}
    return {
        f.name: str(f)
        for f in input_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in RAW_EXTENSIONS
    }


def run(run_id: str):
    config = load_config()
    input_dir = Path(config["input_dir"])
    run_dir = Path(config["output_dir"]) / run_id

    norm_path = run_dir / "normalization.json"
    blur_path = run_dir / "blur_scores.csv"
    if not norm_path.exists():
        print(f"[05][ERROR] {norm_path} not found — run step 04 first.")
        sys.exit(1)
    if not blur_path.exists():
        print(f"[05][ERROR] {blur_path} not found — run step 02 first.")
        sys.exit(1)

    groups = json.load(open(norm_path))["groups"]
    raw_index = index_raw_files(input_dir)

    # Write the neutral develop profile (identical for every file).
    pp3_path = run_dir / "develop.pp3"
    pp3_path.write_text(build_develop_pp3(), encoding="utf-8")
    print(f"[05] Wrote develop profile → {pp3_path.name}")

    # Build the manifest from the keep list.
    manifest = []
    skipped_rejected = 0
    skipped_failed = {}       # group -> count of frames dropped for no correction
    missing_path = 0

    with open(blur_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["decision"] != "keep":
                skipped_rejected += 1
                continue
            group = row["group"]
            entry = groups.get(group)
            if entry is None or entry.get("ccm") is None:
                skipped_failed[group] = skipped_failed.get(group, 0) + 1
                continue
            filepath = raw_index.get(row["filename"])
            if filepath is None:
                print(f"[05] WARNING: {row['filename']} in keep list but not found "
                      f"under {input_dir} — skipped.")
                missing_path += 1
                continue
            manifest.append({
                "filename": row["filename"],
                "filepath": filepath,
                "group": group,
                "status": entry["status"],          # trusted / low_confidence / borrowed
                "borrowed_from": entry.get("borrowed_from"),
                "ccm": entry["ccm"],
            })

    out = {
        "run_id": run_id,
        "develop_pp3": pp3_path.name,
        "jpeg_quality": config.get("jpeg_quality", 95),
        "n_to_render": len(manifest),
        "frames": manifest,
    }
    out_path = run_dir / "render_manifest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    # Report.
    by_group = {}
    for m in manifest:
        by_group[m["group"]] = by_group.get(m["group"], 0) + 1
    print(f"[05] Manifest: {len(manifest)} frame(s) to render")
    for g in sorted(by_group):
        status = groups[g]["status"]
        tag = f" (borrowed from {groups[g]['borrowed_from']})" if status == "borrowed" else f" ({status})"
        print(f"[05]   {g}: {by_group[g]}{tag}")
    if skipped_rejected:
        print(f"[05]   skipped {skipped_rejected} frame(s) rejected by step 02")
    for g, n in skipped_failed.items():
        print(f"[05]   DROPPED {n} frame(s) of group {g} — no usable colour correction")
    print(f"[05] Done → {out_path}")
    print(f"[05] Run ID: {run_id} (use this for step 06)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python pipeline/05_develop_profile.py <run_id>")
        sys.exit(1)
    run(sys.argv[1])
