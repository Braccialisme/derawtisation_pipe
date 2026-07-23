# Pipeline steps — plain-language guide

A step-by-step tour of what each script does, what it reads and writes, and the
knobs you can turn in `config.yaml`. Written to be understood without reading the
code. Every step is independent and re-runnable; run them in order.

Each run creates one timestamped folder under `output_dir`, e.g.
`mpr_output_full/20260723_152955/`. That folder is the **run** — every step reads
and writes inside it. Step 01 creates it and prints a `run_id`; every later step
takes that `run_id` as an argument.

```bash
uv run python pipeline/01_exif_audit.py                 # prints the run_id
uv run python pipeline/02_blur_culling.py       <run_id>
uv run python pipeline/03_checker_detection.py  [run_id]   # no id = new scan; id = resume
uv run python pipeline/04_normalization.py      <run_id>
uv run python pipeline/05_develop_profile.py    <run_id>
uv run python pipeline/06_raw_export.py         <run_id>
uv run python pipeline/07_qc_report.py          <run_id>
```

---

## 01 — EXIF audit  (`01_exif_audit.py`)

**What it does.** Reads the camera metadata (EXIF) of every RAW file and builds an
inventory. Works out which camera group each file belongs to from its filename
prefix (`N02_` = Nikon, `R004`/`R100`/`R105` = the three Ricoh cards), and flags
anomalies: frames where the flash did not fire, and files whose prefix matches no
known group.

**Reads.** All `.NEF`/`.DNG` under `input_dir` (searches sub-folders too).
**Writes.** `exif_audit.csv` — one row per file: group, camera, ISO, shutter,
aperture, white-balance mode, flash state, and an `anomalies` column.
**Knobs.** `input_dir`, `camera_groups` (the prefix→camera map).
**How to read it.** Open the CSV; the `anomalies` column shows `NO_FLASH` on the
misfired frames. This is your ground-truth list of what was shot.

---

## 02 — Blur culling  (`02_blur_culling.py`)

**What it does.** Scores every RAW for sharpness (variance of the Laplacian — a
standard focus metric) on a fast half-size decode, and decides keep or reject.
A frame is rejected if it is below its group's sharpness threshold **or** was
flagged `NO_FLASH` in step 01.

**Why per-group thresholds.** The Nikon's 45-megapixel sensor scores lower on this
metric than the Ricoh for the same real sharpness, so each group has its own
threshold (see DECISIONS D009). A single global number would wrongly bin most
Nikon frames.

**Reads.** `exif_audit.csv` + the RAW files.
**Writes.** `blur_scores.csv` — filename, group, score, keep/reject, and the reason.
**Knobs.** `blur_thresholds` (per group), `blur_threshold_default`,
`reject_no_flash`. **Lower a threshold to keep more frames.** Nothing is deleted —
rejected frames simply aren't rendered later.
**How to read it.** Filter the CSV to `decision = reject` to see what was dropped
and why. If too much is being culled, nudge the thresholds down and re-run 02.

---

## 03 — ColorChecker detection  (`03_checker_detection.py`)

**What it does.** Hunts for the physical ColorChecker chart in the frames. Two
passes: a fast half-size scan of every file to find candidates, then a full-quality
re-decode of only the hits to read accurate patch colours. The chart was only
swept into frame opportunistically, so hits are rare — this is why it scans the
whole dataset.

**Built to survive a big slow scan.** It runs many files in parallel and writes a
cache (`scan_cache.jsonl`, `patches_cache.jsonl`) as it goes. If it crashes or you
stop it, **re-run with the same `run_id` and it resumes**, skipping everything
already done.

**Reads.** RAW files under `input_dir`.
**Writes.** `checker_detections.json` — for each hit: filename, group, and the 24
measured patch colours. Also the two cache files.
**Knobs.** `checker_confidence_min`, `scan_workers` (parallelism),
`scan_progress_every`.
**How to read it.** The console prints hits per group and warns about groups with
none. On our data: Nikon (N02) and R105 had hits; R004 and R100 had **zero** — the
chart never landed in frame for those cards.

---

## 04 — Normalization  (`04_normalization.py`)

**What it does.** Turns the measured chart patches into one **colour-correction
matrix (CCM)** per group — the recipe that pulls that camera's colour onto the
shared, accurate chart reference. This is the heart of the pipeline.

**Trust, not blind faith.** It doesn't assume a detection is good just because 24
patches were found. It fits the matrix, re-measures, and computes the colour error
(ΔE). A glare/shadow/tilted chart produces a big error and is thrown out. Good
detections land around ΔE 3 (very good). Groups with no usable chart **borrow** the
matrix of the most similar group — the three Ricoh cards borrow each other before
ever borrowing the Nikon (see DECISIONS D010).

**Reads.** `checker_detections.json`.
**Writes.** `normalization.json` — per group: status (`trusted` / `low_confidence`
/ `borrowed` / `failed`), the 3×3 matrix, and how many detections fed it.
**Knobs.** `normalization.residual_deltaE_warn` / `_reject` (trust gates),
`normalization.fallback_order` (who borrows from whom).
**How to read it.** The summary table shows each group's status and ΔE. On our run:
N02 ΔE 3.15 (trusted), R105 ΔE 3.87 (trusted), R004 + R100 borrowed from R105.

> Sanity tool: `uv run python tools/inspect_detection.py <run_id>` breaks a group's
> error down per patch, so you can see whether one blown patch is dragging the
> average or the whole chart is soft.

---

## 05 — Develop profile  (`05_develop_profile.py`)

**What it does.** Prepares the render without doing heavy work. Writes one neutral
RawTherapee profile (`develop.pp3`) used for every file, and a **manifest** listing
exactly which frames to export (the kept, colour-correctable ones) each paired with
its group's matrix.

**Reads.** `normalization.json` + `blur_scores.csv`.
**Writes.** `develop.pp3`, `render_manifest.json`.
**How to read it.** The console prints how many frames per group will render and
notes any dropped because their group had no usable correction.

---

## 06 — RAW export  (`06_raw_export.py`)  ← the deliverable

**What it does.** Produces the final corrected JPEGs. For each frame:
RawTherapee develops the RAW to a high-bit-depth neutral image (great demosaic,
as-shot white balance), then Python applies the colour matrix and saves a JPEG.

**Why two engines (the "hybrid").** RawTherapee has no tool that applies an
arbitrary colour matrix — its Channel Mixer is a black-and-white/blend tool and
mangles the matrix. So RawTherapee does what it's best at (developing the RAW) and
Python does the colour maths in the exact form the matrix was built for. See
DECISIONS D012.

**Runs in parallel and resumes.** Re-running skips frames whose JPEG already
exists, so an interrupted batch just continues.

**Reads.** `render_manifest.json`, `develop.pp3`, the RAW files.
**Writes.** `export_jpegs/*.jpg` (keeps the original filename, so the prefix still
tells you the camera). **Knobs.** `jpeg_quality`, `render_workers`.

> Proof tool: `uv run python tools/verify_render.py <run_id>` renders the chart
> frames through this exact path and reports the ΔE, so you can confirm the colour
> came out right rather than trusting it.

---

## 07 — QC report  (`07_qc_report.py`)

**What it does.** Checks the exported set is actually homogeneous before it goes to
photogrammetry. Measures each JPEG's brightness and colour, then per group reports
the median, the spread, and flags any frame that is far from its group's median.
Draws overlaid brightness histograms so you can see at a glance whether the groups
line up.

**Reads.** `export_jpegs/*.jpg` (+ the manifest for group labels).
**Writes.** `qc_report.html` (open in a browser) and `qc_stats.csv`.
**Knobs.** `qc_luminance_tolerance` (how far from the group median before a frame
is flagged an outlier).
**How to read it.** Open the HTML. Groups whose histograms overlap are consistent.
Note: the pipeline currently equalises **colour**, not brightness — so a residual
brightness offset between Nikon and Ricoh is expected here (a future enhancement).

---

## Where things live

```
<run_id>/
├── exif_audit.csv          (01)
├── blur_scores.csv         (02)
├── scan_cache.jsonl        (03, resumable cache)
├── patches_cache.jsonl     (03, resumable cache)
├── checker_detections.json (03)
├── normalization.json      (04)
├── develop.pp3             (05)
├── render_manifest.json    (05)
├── export_jpegs/*.jpg      (06)  ← final output
├── qc_report.html          (07)
└── qc_stats.csv            (07)
```
