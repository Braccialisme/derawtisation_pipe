"""
04_normalization.py
────────────────────
Turns the ColorChecker detections from step 03 into a single colour-correction
specification per camera group: a 3×3 colour-correction matrix (CCM) plus
white-balance multipliers. Step 05 reads this and writes RawTherapee .pp3
sidecars; step 06 renders the JPEGs.

The maths (see docs/color_correction.md):
  For each group we have the 24 measured patch colours of a ColorChecker (from
  step 03) and the 24 known reference colours of that chart. We solve, by
  least squares, the matrix that best maps measured -> reference:
      CCM = argmin || measured · CCM - reference ||²
  Applying that matrix to every pixel of the group's images pulls the group's
  colour onto the common reference, so all four groups end up consistent with
  each other (and colorimetrically accurate). The Nikon group is the reference
  anchor for the *fallback* logic (D004/D010), not a separate maths path —
  every group is corrected to the same absolute chart reference.

Trust and fallback (see DECISIONS.md D010):
  Step 03's "confidence" only means "24 patches were found", not "the colours
  are clean". So the real quality signal here is the fit residual: after fitting
  the CCM we re-measure the patches and compute the mean ΔE2000 against the
  reference. A glare-blown, shadowed, or wrongly-oriented detection produces a
  large residual and is thrown out. Groups left with no usable detection borrow
  a matrix from the most physically similar group (config: normalization.fallback_order).

Reads:
  config.yaml
  {output_dir}/{run_id}/checker_detections.json   (written by step 03)

Writes:
  {output_dir}/{run_id}/normalization.json

normalization.json schema — one entry per camera group:
  status            "trusted" | "low_confidence" | "borrowed" | "failed"
  source            "own" | "borrowed"
  borrowed_from     donor group key, or null
  n_detections_total / n_detections_used
  thin              true if the matrix rests on a single detection (handle with care)
  residual_deltaE_mean / _max
  ccm               3×3, ROW-VECTOR convention: corrected = measured_rowvec @ ccm
  wb_multipliers    {"R":.., "G":1.0, "B":..}
  detections_used   list of filenames that fed the matrix
All colour values are linear sRGB in 0..1 (NOT the gamma-encoded 16-bit values
that step 03 stored — see linearize_patches()).
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

try:
    import colour
except ImportError:
    print("[ERROR] colour-science not installed. Run: uv sync")
    sys.exit(1)

# Force UTF-8 on stdout/stderr so status lines with ΔE / → / × don't crash on a
# legacy Windows console codepage (cp1252) or when output is piped/redirected.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # already utf-8, or a stream that doesn't support reconfigure


# ── Config / IO helpers (same pattern as the other pipeline steps) ───────────

def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def find_latest_run(output_base: Path) -> str:
    """
    If the user doesn't pass a run_id, pick the most recent run folder that
    actually contains a checker_detections.json (i.e. step 03 has been run).
    """
    candidates = [
        d for d in output_base.iterdir()
        if d.is_dir() and (d / "checker_detections.json").exists()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime).name


# ── Colour maths ─────────────────────────────────────────────────────────────

# The working/target space for the whole pipeline is sRGB.
_SRGB = colour.models.RGB_COLOURSPACE_sRGB


def load_reference_patches(config: dict):
    """
    Load the 24 reference patch colours for the configured ColorChecker standard
    and return them as LINEAR sRGB (0..1), shape (24, 3), in standard chart order.

    The reference data ships as xyY under the chart's measurement illuminant
    (the post-2014 ColorChecker is measured under D50), so we convert
    xyY -> XYZ -> linear sRGB with Bradford chromatic adaptation to sRGB's D65
    whitepoint. We keep it LINEAR (apply_cctf_encoding=False) because a CCM must
    be fitted in linear light.
    """
    standard = config["normalization"]["checker_standard"]
    if standard not in colour.CCS_COLOURCHECKERS:
        available = [k for k in colour.CCS_COLOURCHECKERS if "ColorChecker24" in k]
        print(f"[04][ERROR] checker_standard '{standard}' not found in colour-science.")
        print(f"[04]        Available 24-patch options: {available}")
        sys.exit(1)

    cc = colour.CCS_COLOURCHECKERS[standard]
    xyY = np.array(list(cc.data.values()))            # (24, 3) in chart order
    XYZ = colour.xyY_to_XYZ(xyY)
    ref_lin = colour.XYZ_to_RGB(
        XYZ, _SRGB,
        illuminant=cc.illuminant,                     # source whitepoint (chart's)
        chromatic_adaptation_transform="Bradford",
        apply_cctf_encoding=False,                    # keep LINEAR for fitting
    )
    return ref_lin


def linearize_patches(patch_values) -> np.ndarray:
    """
    Convert one detection's 24 patch values into linear sRGB (0..1).

    Step 03 decodes with rawpy's default sRGB gamma, but colour-checker-detection
    ALWAYS normalises its swatch output to the 0..1 range regardless of the input
    bit depth — so what step 03 stores in checker_detections.json is already
    0..1, sRGB-gamma-encoded. We only need to undo the sRGB transfer function to
    reach linear light; a CCM must be fitted in linear.

    (Guard: if a file ever carries raw 0..65535 values instead — a different
    decode path — normalise it first. Getting this scale wrong is the classic
    silent bug: the fit stays self-consistent so the residual ΔE still looks
    reasonable, but the resulting matrix is off by orders of magnitude and is
    unusable in any renderer.)

    Returns shape (24, 3), or None if the detection is unusable.
    """
    if patch_values is None:
        return None
    arr = np.asarray(patch_values, dtype=float)
    if arr.shape != (24, 3):
        return None
    if arr.max() > 1.0:                               # legacy 16-bit scale guard
        arr = arr / 65535.0
    arr = np.clip(arr, 0.0, 1.0)
    return colour.cctf_decoding(arr)                  # sRGB gamma -> linear


def fit_ccm(measured_lin: np.ndarray, ref_lin: np.ndarray) -> np.ndarray:
    """
    Least-squares 3×3 CCM in ROW-VECTOR convention: measured_lin @ CCM ≈ ref_lin.
    `measured_lin` may be (24,3) for one detection or (N*24,3) for several pooled
    detections — least squares handles the over-determined stack directly.
    """
    ccm, *_ = np.linalg.lstsq(measured_lin, ref_lin, rcond=None)
    return ccm


def _lin_to_lab(lin: np.ndarray) -> np.ndarray:
    XYZ = colour.RGB_to_XYZ(lin, _SRGB, apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(XYZ, illuminant=_SRGB.whitepoint)


def residual_delta_e(measured_lin: np.ndarray, ccm: np.ndarray, ref_lin: np.ndarray):
    """
    Apply the CCM to the measured patches and report how far they land from the
    reference, as ΔE2000 (a perceptual colour-distance: <1 imperceptible, ~2–3
    just noticeable, >10 clearly wrong). Returns (mean, max).
    """
    corrected = measured_lin @ ccm
    d_e = colour.delta_E(_lin_to_lab(corrected), _lin_to_lab(ref_lin), method="CIE 2000")
    return float(np.mean(d_e)), float(np.max(d_e))


def wb_multipliers(measured_lin: np.ndarray, neutral_indices) -> dict:
    """
    Per-channel multipliers that neutralise the grey ramp (so the average neutral
    patch becomes achromatic), normalised so green = 1.0. This is the white
    balance correction step 05 will translate into RawTherapee's WB controls.
    """
    neutral = measured_lin[neutral_indices].mean(axis=0)
    neutral = np.where(neutral <= 0, 1e-6, neutral)   # guard against black/clip
    mult = neutral[1] / neutral
    return {"R": float(mult[0]), "G": float(mult[1]), "B": float(mult[2])}


# ── Per-group matrix computation ─────────────────────────────────────────────

def compute_own_matrix(detections, ref_lin, cfg):
    """
    Build a group's OWN correction from its detections.

    Strategy: fit each detection on its own first and measure its residual. Drop
    any detection whose residual exceeds the reject gate (glare / shadow / bad
    orientation). Pool the survivors and fit one matrix on the combined patches —
    pooling averages out per-frame noise and is more robust than averaging
    separate matrices. Returns a result dict, or None if nothing survives.
    """
    warn = cfg["residual_deltaE_warn"]
    reject = cfg["residual_deltaE_reject"]
    neutral_idx = cfg["wb_neutral_indices"]

    survivors = []          # (filename, measured_lin) that pass the reject gate
    for det in detections:
        measured = linearize_patches(det.get("patch_values"))
        if measured is None:
            print(f"[04]   skip {det['filename']}: no usable 24-patch data")
            continue
        ccm = fit_ccm(measured, ref_lin)
        mean_de, _ = residual_delta_e(measured, ccm, ref_lin)
        if mean_de > reject:
            print(f"[04]   reject {det['filename']}: residual ΔE {mean_de:.1f} "
                  f"> {reject} (likely glare/shadow/orientation)")
            continue
        survivors.append((det["filename"], measured))

    if not survivors:
        return None

    pooled = np.vstack([m for _, m in survivors])             # (N*24, 3)
    ref_tiled = np.vstack([ref_lin] * len(survivors))         # (N*24, 3)
    ccm = fit_ccm(pooled, ref_tiled)
    mean_de, max_de = residual_delta_e(pooled, ccm, ref_tiled)

    if mean_de <= warn:
        status = "trusted"
    else:
        status = "low_confidence"

    return {
        "status": status,
        "source": "own",
        "borrowed_from": None,
        "n_detections_total": len(detections),
        "n_detections_used": len(survivors),
        "thin": len(survivors) == 1,
        "residual_deltaE_mean": round(mean_de, 3),
        "residual_deltaE_max": round(max_de, 3),
        "ccm": ccm.tolist(),
        "wb_multipliers": wb_multipliers(pooled.reshape(-1, 24, 3).mean(axis=0), neutral_idx),
        "detections_used": [name for name, _ in survivors],
    }


def resolve_fallbacks(results, groups, fallback_order, reference_group):
    """
    For every configured group with no usable OWN matrix, borrow the matrix of
    the first group in its fallback_order that has a usable one (own or already
    resolved). Borrowed entries copy the donor's ccm + wb but keep their own
    identity and a clear borrowed_from trail. See DECISIONS.md D010.
    """
    def usable(entry):
        return entry is not None and entry["status"] in ("trusted", "low_confidence")

    for group in groups:
        if usable(results.get(group)):
            continue

        donor = None
        for candidate in fallback_order.get(group, []):
            if usable(results.get(candidate)):
                donor = candidate
                break

        if donor is None:
            results[group] = {
                "status": "failed", "source": None, "borrowed_from": None,
                "n_detections_total": 0, "n_detections_used": 0, "thin": False,
                "residual_deltaE_mean": None, "residual_deltaE_max": None,
                "ccm": None, "wb_multipliers": None, "detections_used": [],
            }
            print(f"[04] FAILED: group {group} has no usable detection and no "
                  f"donor available — cannot correct this group.")
            continue

        d = results[donor]
        results[group] = {
            "status": "borrowed", "source": "borrowed", "borrowed_from": donor,
            "n_detections_total": (results.get(group) or {}).get("n_detections_total", 0),
            "n_detections_used": 0, "thin": False,
            "residual_deltaE_mean": None, "residual_deltaE_max": None,
            "ccm": d["ccm"], "wb_multipliers": d["wb_multipliers"],
            "detections_used": [],
        }
        note = " (EMERGENCY: reference group borrowing — consider a re-shoot)" \
            if group == reference_group else ""
        print(f"[04] BORROW: group {group} <- {donor}{note}")


# ── Main ─────────────────────────────────────────────────────────────────────

def run(run_id: str = None):
    config = load_config()
    output_base = Path(config["output_dir"])
    groups = list(config["camera_groups"].keys())
    reference_group = config.get("reference_group")
    norm_cfg = config["normalization"]

    if run_id is None:
        run_id = find_latest_run(output_base)
        if run_id is None:
            print(f"[04][ERROR] No run with checker_detections.json found under "
                  f"{output_base}. Run step 03 first.")
            sys.exit(1)
        print(f"[04] No run_id given — using most recent run: {run_id}")

    run_dir = output_base / run_id
    detections_path = run_dir / "checker_detections.json"
    if not detections_path.exists():
        print(f"[04][ERROR] {detections_path} not found. Run step 03 for this run first.")
        sys.exit(1)

    with open(detections_path) as f:
        det_data = json.load(f)

    # Group the detections by camera group.
    by_group = {g: [] for g in groups}
    for det in det_data.get("detections", []):
        g = det.get("group")
        if g in by_group:
            by_group[g].append(det)
        else:
            print(f"[04] WARNING: detection {det.get('filename')} has unknown "
                  f"group '{g}' — ignored.")

    print(f"[04] Loading reference patches: {norm_cfg['checker_standard']}")
    ref_lin = load_reference_patches(config)

    # Phase A — every group with detections gets its OWN matrix (if usable).
    results = {}
    for group in groups:
        dets = by_group[group]
        print(f"[04] Group {group}: {len(dets)} detection(s)")
        results[group] = compute_own_matrix(dets, ref_lin, norm_cfg) if dets else None
        if results[group] is not None:
            r = results[group]
            print(f"[04]   {group} OWN matrix: {r['status']} "
                  f"(used {r['n_detections_used']}/{r['n_detections_total']}, "
                  f"mean ΔE {r['residual_deltaE_mean']}"
                  f"{', THIN' if r['thin'] else ''})")

    # Phase B — groups with no usable own matrix borrow one.
    resolve_fallbacks(results, groups, norm_cfg["fallback_order"], reference_group)

    # Loud, separate warning if the reference group itself is weak.
    ref_entry = results.get(reference_group)
    if ref_entry and (ref_entry["status"] == "borrowed" or ref_entry.get("thin")):
        print(f"[04] *** Reference group {reference_group} is "
              f"{'borrowed' if ref_entry['status'] == 'borrowed' else 'THIN (1 detection)'}. "
              f"The whole normalization is anchored on it — strongly consider "
              f"re-shooting dedicated checker frames for it. ***")

    out = {
        "run_id": run_id,
        "reference_group": reference_group,
        "checker_standard": norm_cfg["checker_standard"],
        "ccm_convention": "corrected_rowvec = measured_rowvec @ ccm  (values = linear sRGB, 0..1)",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "groups": results,
    }
    out_path = run_dir / "normalization.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)

    # Summary table.
    print(f"\n[04] ── Normalization summary ──")
    for group in groups:
        r = results[group]
        src = r["borrowed_from"] if r["status"] == "borrowed" else r["source"]
        de = r["residual_deltaE_mean"]
        de_str = f"ΔE {de}" if de is not None else "—"
        print(f"[04]   {group:5} {r['status']:14} source={src or '—':10} {de_str}")
    print(f"[04] Done → {out_path}")
    print(f"[04] Run ID: {run_id} (use this for step 05)")


if __name__ == "__main__":
    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    run(run_id)