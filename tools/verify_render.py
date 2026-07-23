"""
tools/verify_render.py  —  empirical check of the hybrid render path (D012)
─────────────────────────────────────────────────────────────────────────────
The CCM is FITTED in rawpy-decoded space (steps 03/04) but the final JPEGs are
produced by the hybrid path (step 06): RawTherapee develops a neutral 16-bit TIFF,
then Python applies the CCM. This tool proves the round-trip reproduces step 04's
colours instead of trusting it — the same check that caught RawTherapee's
ChannelMixer mangling the matrix (ΔE ~30) and confirmed the hybrid path (ΔE ~3).

For each detected ColorChecker frame it renders through render_common (exactly
what step 06 does), re-detects the 24 patches on the rendered image, and reports
mean/max ΔE2000 vs the chart reference. It also renders WITHOUT the CCM as a
baseline, so you can see how much the correction helped.

Reading: 'corrected ΔE' should land close to step 04's fit residual. If it's far
worse, the CCM isn't being applied as intended (suspect render_common / the CCM
convention). 'no-corr ΔE' shows the uncorrected develop for contrast.

Usage (from repo root):
    uv run python tools/verify_render.py            # newest run with normalization
    uv run python tools/verify_render.py 20260723_123506
"""
import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import yaml
import colour

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline"))
from pp3_common import build_develop_pp3
from render_common import develop_to_tiff, apply_ccm_to_tiff

from colour_checker_detection import detect_colour_checkers_segmentation

SRGB = colour.models.RGB_COLOURSPACE_sRGB
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def load_config():
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def reference_lin(cfg):
    cc = colour.CCS_COLOURCHECKERS[cfg["normalization"]["checker_standard"]]
    XYZ = colour.xyY_to_XYZ(np.array(list(cc.data.values())))
    return colour.XYZ_to_RGB(XYZ, SRGB, illuminant=cc.illuminant,
                             chromatic_adaptation_transform="Bradford",
                             apply_cctf_encoding=False)


def lin_to_lab(lin):
    return colour.XYZ_to_Lab(colour.RGB_to_XYZ(lin, SRGB, apply_cctf_decoding=False),
                             illuminant=SRGB.whitepoint)


def measure(rgb_uint8, ref_lin):
    """Detect the chart on a rendered 8-bit RGB image; return (mean, max) ΔE or None."""
    if rgb_uint8 is None:
        return None
    result = detect_colour_checkers_segmentation(rgb_uint8.astype(float) / 255.0,
                                                 additional_data=True)
    if not result:
        return None
    sw = np.asarray(result[0].swatch_colours, dtype=float)
    if sw.shape != (24, 3):
        return None
    meas_lin = colour.cctf_decoding(np.clip(sw, 0.0, 1.0))
    d_e = colour.delta_E(lin_to_lab(meas_lin), lin_to_lab(ref_lin), method="CIE 2000")
    return float(np.mean(d_e)), float(np.max(d_e))


def main():
    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = load_config()
    base = Path(cfg["output_dir"])
    rt_cli = cfg["rawtherapee_cli"]
    if not Path(rt_cli).exists():
        print(f"[verify][ERROR] RawTherapee CLI not found at {rt_cli}")
        sys.exit(1)

    if run_id is None:
        runs = [d for d in base.iterdir() if (d / "normalization.json").exists()]
        if not runs:
            print("[verify][ERROR] no run with normalization.json found")
            sys.exit(1)
        run_id = max(runs, key=lambda d: d.stat().st_mtime).name
    run_dir = base / run_id
    print(f"[verify] run_id: {run_id}")

    norm = json.load(open(run_dir / "normalization.json"))["groups"]
    dets = json.load(open(run_dir / "checker_detections.json"))["detections"]
    ref_lin = reference_lin(cfg)

    print(f"[verify] Rendering {len(dets)} checker frame(s) through the hybrid path "
          f"and re-measuring...\n")
    print(f"{'frame':16} {'group':5} {'fit ΔE':>7} {'no-corr ΔE':>11} {'corrected ΔE':>13}  verdict")

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        pp3 = work / "develop.pp3"
        pp3.write_text(build_develop_pp3(), encoding="utf-8")
        for d in dets:
            grp = d["group"]
            raw_path = Path(d["filepath"])
            entry = norm.get(grp, {})
            ccm = entry.get("ccm")
            fit_de = entry.get("residual_deltaE_mean")
            if ccm is None:
                print(f"{raw_path.stem:16} {grp:5}  (no CCM for this group — skipped)")
                continue

            tif = develop_to_tiff(rt_cli, raw_path, pp3, work)
            base_m = measure(apply_ccm_to_tiff(tif, IDENTITY), ref_lin)
            corr_m = measure(apply_ccm_to_tiff(tif, ccm), ref_lin)
            Path(tif).unlink(missing_ok=True)
            if corr_m is None:
                print(f"{raw_path.stem:16} {grp:5}  (chart not re-detected on render)")
                continue

            base_de = f"{base_m[0]:.2f}" if base_m else "—"
            fit_str = f"{fit_de:.2f}" if fit_de is not None else "—"
            ok = fit_de is not None and corr_m[0] <= fit_de + 3.0
            verdict = "OK" if ok else "CHECK"
            print(f"{raw_path.stem:16} {grp:5} {fit_str:>7} {base_de:>11} "
                  f"{corr_m[0]:>13.2f}  {verdict}")

    print("\n[verify] 'corrected ΔE' ≈ 'fit ΔE' -> hybrid render path sound.")


if __name__ == "__main__":
    main()
