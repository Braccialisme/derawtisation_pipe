"""
render_common.py — shared hybrid render core (RawTherapee develop → Python CCM)
──────────────────────────────────────────────────────────────────────────────
The two-engine render (D012) in one place, imported by `06_raw_export.py` (batch)
and `tools/verify_render.py` (validation), so the check renders exactly what the
batch produces.

Pipeline per file:
  1. RawTherapee develops the RAW to a 16-bit sRGB TIFF (neutral profile from
     pp3_common.build_develop_pp3 — demosaic, as-shot WB, no colour matrix).
  2. Python linearises that TIFF, applies the group's 3×3 CCM in linear light
     (the exact domain step 04 fitted it in), re-encodes to sRGB.
The CCM already absorbs white balance (fitted on a camera-WB decode), so no
separate WB multiply happens here — that would double-correct.
"""

import subprocess
from pathlib import Path

import cv2
import numpy as np
import colour


def develop_to_tiff(rt_cli: str, raw_path, pp3_path, out_dir) -> Path:
    """
    Develop one RAW to a 16-bit sRGB TIFF with the RawTherapee CLI.
    Flags: -t (TIFF) -b16 (16-bit) -Y (overwrite) -c INPUT (must be last).
    Raises CalledProcessError if RawTherapee fails, so callers can skip that file.
    """
    subprocess.run(
        [rt_cli, "-o", str(out_dir), "-p", str(pp3_path),
         "-t", "-b16", "-Y", "-c", str(raw_path)],
        check=True, capture_output=True,
    )
    return Path(out_dir) / (Path(raw_path).stem + ".tif")


def apply_ccm_to_tiff(tif_path, ccm) -> np.ndarray:
    """
    Load a 16-bit sRGB TIFF, apply the linear CCM, return an 8-bit sRGB RGB array
    (H, W, 3) ready to write as JPEG. Returns None if the TIFF can't be read.

    Convention matches step 04: corrected_row = measured_row @ ccm, in linear sRGB.
    """
    img = cv2.imread(str(tif_path), cv2.IMREAD_UNCHANGED)   # 16-bit BGR
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(float) / 65535.0
    lin = colour.cctf_decoding(rgb)                          # sRGB gamma -> linear
    ccm = np.asarray(ccm, dtype=float)
    corr = np.clip(lin.reshape(-1, 3) @ ccm, 0.0, 1.0).reshape(lin.shape)
    out = colour.cctf_encoding(corr)                         # linear -> sRGB gamma
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def write_jpeg(rgb_uint8: np.ndarray, dest_path, quality: int = 95) -> None:
    """
    Write an RGB uint8 array to JPEG. Uses 4:4:4 chroma (no colour subsampling) —
    this is a colour-accuracy pipeline, so we don't want the encoder throwing away
    half the colour resolution the way default 4:2:0 does.
    """
    bgr = cv2.cvtColor(rgb_uint8, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(dest_path), bgr, [
        int(cv2.IMWRITE_JPEG_QUALITY), quality,
        int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR), int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444),
    ])
