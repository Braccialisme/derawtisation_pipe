# derawtisation_pipe

A reproducible pipeline for processing RAW photogrammetry images shot across multiple cameras, normalizing color, white balance and exposure before alignment in RealityCapture or Metashape.

Built for the **Musée des Plans-Reliefs — Strasbourg** project (May 2026) and designed to be reused across future photogrammetry shoots at Iconem.

---

## Context

We shot a hand-made 18th century city model (~2×2m) in a dark archive room using:

- **Nikon D850** with a 12–70mm lens and a Godox rectangle flash (external, consistent)
- **3× Ricoh GR II** bodies shooting on 2-second intervals with native flash (variable, per-body)

This produced ~2500 RAW files (NEF + DNG) across 4 memory cards, with inconsistent white balance between Ricoh bodies and sporadic no-flash frames from autofocus timeouts on interval shooting.

The goal of this pipeline is to produce a homogeneous, color-accurate set of JPEGs ready for photogrammetric alignment — without manual per-image editing.

---

## Pipeline overview

```
RAW files (NEF + DNG)
        │
        ▼
[01] EXIF AUDIT         → reads metadata, maps camera/card groups, flags anomalies
        │
        ▼
[02] BLUR CULLING       → scores sharpness, outputs keep/reject list
        │
        ▼
[03] CHECKER DETECTION  → finds ColorChecker in frames, extracts patch values
        │
        ▼
[04] NORMALIZATION      → computes per-group correction matrices from checker data
        │
        ▼
[05] DEVELOP PROFILE    → writes the neutral RawTherapee profile + render manifest
        │
        ▼
[06] RAW EXPORT         → RawTherapee develops → Python applies the CCM → JPEGs
        │
        ▼
[07] QC REPORT          → per-group luminance/colour homogeneity, HTML report
```

Each step produces a traceable output in the `runs/YYYYMMDD_HHMMSS/` folder.

---

## Camera groups

Correction matrices are computed **per card**, not per camera model, because the three Ricoh bodies had different white balance configurations:

| Group key   | Camera      | Card folder  | WB mode      |
|-------------|-------------|--------------|--------------|
| `N02`       | Nikon D850  | 146ND850     | Flash        |
| `R004`      | Ricoh GR II | 100_0512     | Multi-P Auto |
| `R100`      | Ricoh GR II | 104RICOH     | Auto         |
| `R105`      | Ricoh GR II | 111RICOH     | Multi-P Auto |

Group is detected from filename prefix, which maps 1:1 to card of origin.

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for environment management
- [RawTherapee](https://rawtherapee.com/) installed with CLI accessible in PATH
- [ExifTool](https://exiftool.org/) installed and accessible in PATH

---

## Setup

```bash
# Clone the repo
git clone https://github.com/Braccialisme/derawtisation_pipe.git
cd derawtisation_pipe

# Install all dependencies (creates .venv automatically)
uv sync

# Verify
uv run python -c "import rawpy, cv2, colour; print('all good')"
```

---

## Usage

Edit `config.yaml` to set your input folder and output folder, then run each step:

```bash
uv run python pipeline/01_exif_audit.py
uv run python pipeline/02_blur_culling.py    <run_id>
uv run python pipeline/03_checker_detection.py    [run_id]   # [run_id] resumes a scan
uv run python pipeline/04_normalization.py    <run_id>
uv run python pipeline/05_develop_profile.py    <run_id>
uv run python pipeline/06_raw_export.py    <run_id>
uv run python pipeline/07_qc_report.py    <run_id>
```

Step 01 creates the timestamped run folder and prints its `run_id`; every later
step takes that `run_id`. Step 03 is the expensive full-dataset checker scan — it
caches every file and **resumes** if re-run with the same `run_id`, so a crash or
network drop never loses progress. Each script is independent and re-runnable.
All outputs land in `runs/YYYYMMDD_HHMMSS/` (or the `output_dir` from config).

**Render path (see DECISIONS.md D012):** the colour-correction matrix is a linear
3×3, and RawTherapee has no module that applies an arbitrary colour matrix (its
Channel Mixer is a B&W/blend tool). So step 06 is *hybrid*: RawTherapee develops
each RAW to a neutral 16-bit TIFF, then Python applies the matrix. Validate it with:

```bash
uv run python tools/verify_render.py    <run_id>   # renders the checker frames, reports ΔE
```

---

## Repository structure

```
derawtisation_pipe/
├── README.md               this file
├── DECISIONS.md            technical decisions log
├── config.yaml             all tunable parameters
├── pyproject.toml          uv environment definition
├── pipeline/               numbered processing scripts
├── runs/                   gitignored — one folder per run
├── docs/                   extended technical documentation
├── tests/                  unit tests
└── samples/                small test files for onboarding
```

---

## Contributing

See `DECISIONS.md` before modifying the pipeline logic — it explains why each approach was chosen. When adding a new decision or changing an existing one, update that file.

---

## Authors

Andrea Bracciali — Iconem, 2026
