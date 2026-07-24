"""
gui/schema.py — single source of truth for the GUI
───────────────────────────────────────────────────
Every tunable the GUI exposes is described ONCE here: its config key, type,
range, help text, and which group it belongs to. The backend serves this as JSON
and the frontend renders the controls automatically from it.

To add a new control: add one dict to FIELDS (and the key to config.yaml). The GUI
picks it up with no other change — this is the "adding an asset shouldn't block
what we have" guarantee.

`key` uses dotted paths into config.yaml, e.g. "normalization.residual_deltaE_warn"
or "blur_thresholds.N02". The backend reads/writes those paths with a
comment-preserving YAML round-trip, so editing values never destroys the config's
explanatory comments.

Field types the frontend knows how to render:
  path    — text input (a file/folder path)
  text    — text input
  int     — integer input (min/max/step)
  number  — float input (min/max/step)
  bool    — toggle
  select  — dropdown (options)
"""

# ── Config controls, grouped for display ────────────────────────────────────
FIELDS = [
    # Paths
    {"key": "input_dir", "label": "Input folder (RAWs)", "type": "path", "group": "Paths",
     "help": "Folder of RAW files to process. Searches sub-folders too."},
    {"key": "output_dir", "label": "Output folder", "type": "path", "group": "Paths",
     "help": "Where each timestamped run folder is created."},
    {"key": "rawtherapee_cli", "label": "RawTherapee CLI path", "type": "path", "group": "Paths",
     "help": "Full path to rawtherapee-cli.exe."},

    # Camera groups
    {"key": "reference_group", "label": "Reference group", "type": "select", "group": "Camera groups",
     "options": ["N02", "R004", "R100", "R105"],
     "help": "The anchor group all others are compared against (Nikon N02)."},

    # Blur culling
    {"key": "blur_thresholds.N02", "label": "Blur threshold — N02 (Nikon)", "type": "int",
     "min": 0, "max": 400, "step": 5, "group": "Blur culling",
     "help": "Sharpness cutoff for the Nikon. Lower = keep more frames."},
    {"key": "blur_thresholds.R004", "label": "Blur threshold — R004", "type": "int",
     "min": 0, "max": 400, "step": 5, "group": "Blur culling", "help": "Ricoh card 100_0512."},
    {"key": "blur_thresholds.R100", "label": "Blur threshold — R100", "type": "int",
     "min": 0, "max": 400, "step": 5, "group": "Blur culling", "help": "Ricoh card 104RICOH."},
    {"key": "blur_thresholds.R105", "label": "Blur threshold — R105", "type": "int",
     "min": 0, "max": 400, "step": 5, "group": "Blur culling", "help": "Ricoh card 111RICOH."},
    {"key": "blur_threshold_default", "label": "Blur threshold — default", "type": "int",
     "min": 0, "max": 400, "step": 5, "group": "Blur culling",
     "help": "Fallback for any group not listed above."},
    {"key": "reject_no_flash", "label": "Reject no-flash frames", "type": "bool", "group": "Blur culling",
     "help": "Drop frames where the flash did not fire (Ricoh interval misfires)."},

    # ColorChecker detection
    {"key": "checker_confidence_min", "label": "Checker confidence min", "type": "number",
     "min": 0.0, "max": 1.0, "step": 0.05, "group": "ColorChecker",
     "help": "Minimum detection confidence to accept a chart hit."},
    {"key": "scan_workers", "label": "Scan workers (step 03)", "type": "int",
     "min": 1, "max": 32, "step": 1, "group": "ColorChecker",
     "help": "Parallel decode+detect processes for the full scan."},

    # Normalization
    {"key": "normalization.residual_deltaE_warn", "label": "ΔE warn", "type": "number",
     "min": 0.0, "max": 30.0, "step": 0.5, "group": "Normalization",
     "help": "Above this fit error a detection is flagged low_confidence."},
    {"key": "normalization.residual_deltaE_reject", "label": "ΔE reject", "type": "number",
     "min": 0.0, "max": 40.0, "step": 0.5, "group": "Normalization",
     "help": "Above this fit error a detection is thrown out entirely."},

    # Export
    {"key": "jpeg_quality", "label": "JPEG quality", "type": "int",
     "min": 60, "max": 100, "step": 1, "group": "Export",
     "help": "100 = least aggressive (largest files). 95 is the usual standard."},
    {"key": "render_workers", "label": "Render workers (step 06)", "type": "int",
     "min": 1, "max": 16, "step": 1, "group": "Export",
     "help": "Parallel RawTherapee-develop + correct processes."},
    {"key": "export_tiff", "label": "Also export TIFF", "type": "bool", "group": "Export",
     "help": "Reserved — JPEG is the current deliverable."},

    # QC
    {"key": "qc_luminance_tolerance", "label": "QC luminance tolerance", "type": "number",
     "min": 0.0, "max": 1.0, "step": 0.05, "group": "QC",
     "help": "Flag a frame if its brightness deviates this fraction from its group median."},
]

# Fixed display order of groups (any group not listed falls to the end).
GROUP_ORDER = ["Paths", "Camera groups", "Blur culling", "ColorChecker",
               "Normalization", "Export", "QC"]

# ── Pipeline steps for the Run panel ─────────────────────────────────────────
# `needs_run_id`: step takes a run_id argument. Step 01 creates the run.
# `optional_run_id`: step 03 resumes if given a run_id, else starts a new scan.
STEPS = [
    {"id": "01", "script": "pipeline/01_exif_audit.py", "name": "01 · EXIF audit",
     "needs_run_id": False, "desc": "Read metadata, map camera groups, flag anomalies. Creates the run."},
    {"id": "02", "script": "pipeline/02_blur_culling.py", "name": "02 · Blur culling",
     "needs_run_id": True, "desc": "Score sharpness, reject blurry / no-flash frames."},
    {"id": "03", "script": "pipeline/03_checker_detection.py", "name": "03 · Checker detection",
     "needs_run_id": True, "optional_run_id": True,
     "desc": "Find the ColorChecker (parallel, resumable). Same run_id resumes."},
    {"id": "04", "script": "pipeline/04_normalization.py", "name": "04 · Normalization",
     "needs_run_id": True, "desc": "Fit per-group colour-correction matrices."},
    {"id": "05", "script": "pipeline/05_develop_profile.py", "name": "05 · Develop profile",
     "needs_run_id": True, "desc": "Write RawTherapee profile + render manifest."},
    {"id": "06", "script": "pipeline/06_raw_export.py", "name": "06 · RAW export",
     "needs_run_id": True, "desc": "Develop + correct → JPEGs (parallel, resumable)."},
    {"id": "07", "script": "pipeline/07_qc_report.py", "name": "07 · QC report",
     "needs_run_id": True, "desc": "Per-group homogeneity report + histograms."},
]

# Artifact files a run folder may contain, in step order — used by the Runs panel
# to show how far a run has progressed.
RUN_ARTIFACTS = [
    ("01", "exif_audit.csv"),
    ("02", "blur_scores.csv"),
    ("03", "checker_detections.json"),
    ("04", "normalization.json"),
    ("05", "render_manifest.json"),
    ("06", "export_jpegs"),
    ("07", "qc_report.html"),
]
