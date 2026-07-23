"""
tools/inspect_detection.py  —  diagnostic helper (NOT part of the pipeline)
───────────────────────────────────────────────────────────────────────────
Explains a group's color-fit residual by breaking it down per patch. Use it
when step 04 flags a group as low_confidence and you want to know whether a
couple of bad patches (glare / clipping) are dragging the average up, or the
whole detection is soft.

Usage (from repo root):
    uv run python tools/inspect_detection.py            # newest run, all groups
    uv run python tools/inspect_detection.py R105       # newest run, one group
    uv run python tools/inspect_detection.py R105 20260630_105932   # specific run

Reads config.yaml + {output_dir}/{run_id}/checker_detections.json. Writes nothing.
"""
import json, sys
from pathlib import Path
import numpy as np
import yaml
import colour

# Force UTF-8 so ΔE in the output doesn't crash a legacy Windows console codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SRGB = colour.models.RGB_COLOURSPACE_sRGB

def load_config():
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)

def reference(cfg):
    cc = colour.CCS_COLOURCHECKERS[cfg["normalization"]["checker_standard"]]
    names = list(cc.data.keys())
    XYZ = colour.xyY_to_XYZ(np.array(list(cc.data.values())))
    lin = colour.XYZ_to_RGB(XYZ, SRGB, illuminant=cc.illuminant,
                            chromatic_adaptation_transform="Bradford",
                            apply_cctf_encoding=False)
    return names, lin

def lin_to_lab(lin):
    return colour.XYZ_to_Lab(colour.RGB_to_XYZ(lin, SRGB, apply_cctf_decoding=False),
                             illuminant=SRGB.whitepoint)

def main():
    group = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1][0].isdigit() else None
    run_id = next((a for a in sys.argv[1:] if a[0].isdigit()), None)
    cfg = load_config()
    base = Path(cfg["output_dir"])
    if run_id is None:
        runs = [d for d in base.iterdir() if (d / "checker_detections.json").exists()]
        run_id = max(runs, key=lambda d: d.stat().st_mtime).name
    det = json.load(open(base / run_id / "checker_detections.json"))["detections"]
    names, ref = reference(cfg)

    for d in det:
        if group and d["group"] != group:
            continue
        pv = d.get("patch_values")
        if pv is None or np.asarray(pv).shape != (24, 3):
            print(f"\n{d['filename']} ({d['group']}): no usable 24-patch data"); continue
        raw = np.asarray(pv, float)
        # step 03 stores swatches already normalised to 0..1 (see 04's
        # linearize_patches); guard against a legacy 16-bit scale just in case.
        if raw.max() > 1.0:
            raw = raw / 65535.0
        meas = colour.cctf_decoding(np.clip(raw, 0, 1))
        ccm, *_ = np.linalg.lstsq(meas, ref, rcond=None)
        dE = colour.delta_E(lin_to_lab(meas @ ccm), lin_to_lab(ref), method="CIE 2000")
        clip = (raw >= 65000).any(axis=1)          # any channel essentially blown
        print(f"\n{d['filename']} ({d['group']}) — mean ΔE {dE.mean():.2f}, max {dE.max():.2f}")
        print(f"{'patch':22} {'ΔE':>6}  flag")
        for i in np.argsort(dE)[::-1]:             # worst patches first
            flag = "CLIPPED" if clip[i] else ""
            print(f"{names[i]:22} {dE[i]:6.2f}  {flag}")

if __name__ == "__main__":
    main()