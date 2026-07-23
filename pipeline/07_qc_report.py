"""
07_qc_report.py
───────────────
Quality-control pass over the exported JPEGs (step 06), producing a standalone
HTML report to eyeball BEFORE handing the set to photogrammetry. It answers the
question the whole pipeline exists for: is the set actually homogeneous now?

For every exported JPEG it measures mean luminance and mean R/G/B (on a fast
downscaled read). Then, per camera group, it:
  * reports the group's luminance median and colour spread,
  * flags any frame whose luminance deviates from its GROUP median by more than
    qc_luminance_tolerance (config) — the likely exposure/flash outliers,
  * renders a luminance histogram per group so cross-group consistency is visible
    at a glance (the groups should overlap if normalization worked).

Frames are compared against their OWN group median, not a global one: a residual
per-group offset is expected and acceptable; what we hunt for is the odd frame
inside a group, and gross group-to-group mismatch in the histograms.

Reads:  config.yaml, {output_dir}/{run_id}/export_jpegs/*.jpg
        {output_dir}/{run_id}/render_manifest.json  (for group labels)
Writes: {output_dir}/{run_id}/qc_report.html
        {output_dir}/{run_id}/qc_stats.csv
"""

import base64
import csv
import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

import matplotlib
matplotlib.use("Agg")            # headless — no display needed
import matplotlib.pyplot as plt

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Rec.709 luma weights (sRGB primaries) — perceptual luminance from RGB.
_LUMA = np.array([0.2126, 0.7152, 0.0722])


def load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def group_of(filename: str, manifest_index: dict, groups_cfg: dict) -> str:
    """Prefer the manifest's group label; fall back to filename prefix."""
    if filename in manifest_index:
        return manifest_index[filename]
    for prefix in groups_cfg:
        if filename.upper().startswith(prefix.upper()):
            return prefix
    return "unknown"


def measure_jpeg(path: Path):
    """Return (mean_luminance, mean_R, mean_G, mean_B) in 0..255, or None."""
    # REDUCED_COLOR_4 decodes at 1/4 size — plenty for a mean, much faster.
    img = cv2.imread(str(path), cv2.IMREAD_REDUCED_COLOR_4)
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(float).reshape(-1, 3)
    mean_rgb = rgb.mean(axis=0)
    lum = float(mean_rgb @ _LUMA)
    return lum, float(mean_rgb[0]), float(mean_rgb[1]), float(mean_rgb[2])


def histogram_png(group_lums: dict) -> str:
    """Overlaid per-group luminance histograms as a base64 PNG data URI."""
    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=100)
    for group, lums in sorted(group_lums.items()):
        if lums:
            ax.hist(lums, bins=30, range=(0, 255), alpha=0.5, label=f"{group} (n={len(lums)})")
    ax.set_xlabel("mean luminance (0–255)")
    ax.set_ylabel("frames")
    ax.set_title("Per-group luminance distribution (overlap = consistent)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def run(run_id: str):
    config = load_config()
    run_dir = Path(config["output_dir"]) / run_id
    jpeg_dir = run_dir / "export_jpegs"
    tolerance = float(config.get("qc_luminance_tolerance", 0.3))
    groups_cfg = config["camera_groups"]

    if not jpeg_dir.exists():
        print(f"[07][ERROR] {jpeg_dir} not found — run step 06 first.")
        sys.exit(1)

    # Map filename -> group from the manifest, if present.
    manifest_index = {}
    manifest_path = run_dir / "render_manifest.json"
    if manifest_path.exists():
        for fr in json.load(open(manifest_path))["frames"]:
            manifest_index[Path(fr["filename"]).stem + ".jpg"] = fr["group"]

    jpegs = sorted(jpeg_dir.glob("*.jpg"))
    if not jpegs:
        print(f"[07][ERROR] no JPEGs in {jpeg_dir}")
        sys.exit(1)
    print(f"[07] Measuring {len(jpegs)} exported JPEG(s)...")

    rows = []
    group_lums = {}
    for i, jp in enumerate(jpegs):
        m = measure_jpeg(jp)
        if m is None:
            print(f"[07] WARNING: could not read {jp.name}")
            continue
        lum, r, g, b = m
        grp = group_of(jp.name, manifest_index, groups_cfg)
        rows.append({"filename": jp.name, "group": grp, "luminance": lum,
                     "mean_r": r, "mean_g": g, "mean_b": b})
        group_lums.setdefault(grp, []).append(lum)
        if (i + 1) % 100 == 0:
            print(f"[07]   {i + 1}/{len(jpegs)} measured...")

    # Per-group medians + flag outliers against the group's own median.
    group_median = {g: float(np.median(v)) for g, v in group_lums.items() if v}
    for row in rows:
        med = group_median.get(row["group"], 0.0)
        dev = abs(row["luminance"] - med) / med if med > 0 else 0.0
        row["group_median"] = round(med, 1)
        row["deviation"] = round(dev, 3)
        row["flag"] = "OUTLIER" if dev > tolerance else ""

    outliers = [r for r in rows if r["flag"]]

    # Write stats CSV.
    csv_path = run_dir / "qc_stats.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "group", "luminance",
                                          "group_median", "deviation", "flag",
                                          "mean_r", "mean_g", "mean_b"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in w.fieldnames})

    # Build the HTML report.
    hist_uri = histogram_png(group_lums)
    group_rows_html = ""
    for g in sorted(group_lums):
        lums = group_lums[g]
        rgbs = np.array([[r["mean_r"], r["mean_g"], r["mean_b"]] for r in rows if r["group"] == g])
        colour_spread = float(np.mean(np.std(rgbs, axis=0))) if len(rgbs) else 0.0
        n_out = sum(1 for r in rows if r["group"] == g and r["flag"])
        group_rows_html += (
            f"<tr><td>{g}</td><td>{len(lums)}</td>"
            f"<td>{group_median[g]:.1f}</td>"
            f"<td>{np.std(lums):.1f}</td>"
            f"<td>{colour_spread:.1f}</td>"
            f"<td>{n_out}</td></tr>"
        )

    outlier_rows_html = "".join(
        f"<tr><td>{r['filename']}</td><td>{r['group']}</td>"
        f"<td>{r['luminance']:.1f}</td><td>{r['group_median']:.1f}</td>"
        f"<td>{r['deviation']*100:.0f}%</td></tr>"
        for r in sorted(outliers, key=lambda r: -r["deviation"])
    ) or "<tr><td colspan='5'>None — every frame is within tolerance of its group median.</td></tr>"

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>QC report — {run_id}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
 h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 table {{ border-collapse: collapse; margin: 0.5rem 0; }}
 th, td {{ border: 1px solid #ccc; padding: 4px 10px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 img {{ max-width: 100%; height: auto; }}
 .meta {{ color: #666; font-size: 0.9rem; }}
</style></head><body>
<h1>QC report — colour homogeneity</h1>
<p class="meta">Run {run_id} · {len(rows)} exported frames ·
 luminance tolerance ±{tolerance*100:.0f}% of group median</p>

<h2>Per-group summary</h2>
<table>
 <tr><th>group</th><th>frames</th><th>lum median</th><th>lum std</th>
     <th>colour spread</th><th>outliers</th></tr>
 {group_rows_html}
</table>
<p class="meta">Lower std / colour spread = more consistent within the group.
 The histogram below should show the groups overlapping if normalization worked.</p>

<h2>Luminance distribution</h2>
<img src="{hist_uri}" alt="per-group luminance histogram">

<h2>Outlier frames ({len(outliers)})</h2>
<table>
 <tr><th>filename</th><th>group</th><th>luminance</th><th>group median</th><th>deviation</th></tr>
 {outlier_rows_html}
</table>
</body></html>"""

    report_path = run_dir / "qc_report.html"
    report_path.write_text(html, encoding="utf-8")

    print(f"[07] QC complete → {report_path}")
    print(f"[07]   {len(rows)} frames measured, {len(outliers)} luminance outlier(s)")
    for g in sorted(group_lums):
        print(f"[07]   {g}: median luminance {group_median[g]:.1f}, "
              f"{sum(1 for r in rows if r['group'] == g and r['flag'])} outlier(s)")
    print(f"[07]   stats CSV → {csv_path.name}")
    print(f"[07] Run ID: {run_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python pipeline/07_qc_report.py <run_id>")
        sys.exit(1)
    run(sys.argv[1])
