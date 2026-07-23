"""
pp3_common.py — shared RawTherapee .pp3 develop-profile builder
────────────────────────────────────────────────────────────────
One source of truth for the RawTherapee sidecar used by the hybrid render path
(D012), imported by `05_develop_profile.py`, `06_raw_export.py` and
`tools/verify_render.py` so every stage develops identically.

WHY HYBRID (see DECISIONS.md D012): the colour-correction matrix is a 3×3 fitted
in linear sRGB (step 04). RawTherapee has no module that applies an arbitrary
colorimetric 3×3 — its [Channel Mixer] is a channel-blend / B&W tool and mangles
a CCM (empirically verified: ΔE ~30 vs ~3). So RawTherapee is used only as a
high-quality RAW *developer* (demosaic, as-shot white balance, highlight
handling) that outputs a neutral 16-bit sRGB TIFF; step 06 then applies the CCM
in Python — the exact linear domain the matrix was fitted in — and writes the JPEG.

This profile is deliberately NEUTRAL:
  * White balance = Camera (as-shot). The CCM was fitted on a camera-WB decode and
    absorbs the residual colour error, so we neither re-white-balance nor apply the
    wb_multipliers here — doing so would double-correct.
  * Auto exposure OFF, so every frame sits on the same tonal footing (matches the
    no_auto_bright decode step 03 measured patches on).
  * Working + output profile = sRGB — the pipeline target. Output is written as a
    16-bit TIFF so the CCM is applied with headroom before the final 8-bit JPEG.
"""

# RawTherapee 5.8 procparams identifiers. Sections we don't set fall back to RT
# defaults (see docs/rawtherapee_notes.md).
_APP_VERSION = "5.8"
_PP3_VERSION = 346


def build_develop_pp3(app_version: str = _APP_VERSION,
                      pp3_version: int = _PP3_VERSION,
                      working_profile: str = "sRGB",
                      output_profile: str = "RTv4_sRGB") -> str:
    """
    Return the full neutral-develop .pp3 text. It is identical for every file:
    `Setting=Camera` makes RawTherapee read each RAW's own as-shot white balance,
    so one profile correctly develops all four camera groups.
    """
    return (
        "[Version]\n"
        f"AppVersion={app_version}\n"
        f"Version={pp3_version}\n"
        "\n"
        "[Exposure]\n"
        "Auto=false\n"                             # no automatic exposure lift
        "Compensation=0\n"
        "\n"
        "[White Balance]\n"
        "Enabled=true\n"
        "Setting=Camera\n"                         # as-shot WB, per file
        "\n"
        "[Color Management]\n"
        f"WorkingProfile={working_profile}\n"
        f"OutputProfile={output_profile}\n"        # sRGB-gamma 16-bit TIFF out
    )
