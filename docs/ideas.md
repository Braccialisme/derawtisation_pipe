# Ideas & future work

Running list of things worth doing later — not decisions (those live in
`DECISIONS.md`), just notes to ourselves.

## Simple GUI to set thresholds & paths
Instead of hand-editing `config.yaml`, a small local GUI would let a
non-developer set the tunables and launch steps. Candidate scope:
- pick `input_dir` / `output_dir`, RawTherapee path;
- sliders/fields for `blur_thresholds` (per group), `checker_confidence_min`,
  `residual_deltaE_warn/_reject`, `jpeg_quality`, worker counts;
- buttons to run each step and show its console output + the resulting CSV/JSON;
- ideally show a few thumbnails (kept vs rejected, before vs after).
Lightweight options: a small **Streamlit** or **Gradio** app reading/writing the
same `config.yaml` (keeps the config-driven design intact — the GUI is just a
front-end over it), or a Tkinter/PySide window if we want no browser. The pipeline
scripts already take a `run_id` and are independent, so the GUI only has to set
config values and shell out to them.

## Luminance / exposure equalisation (per-group)
The pipeline normalises colour + white balance but not brightness, so group
medians still differ (Nikon ~20% brighter than the Ricoh cards). A per-group
luminance gain (scale each group's median to a common target) applied in the
step-06 Python stage would make the set tonally homogeneous too. This matches the
brief's "consistent … exposure" goal. Would be a good D013.

## Dedicated ColorChecker re-shoot (process, not code)
On the next museum visit, shoot 2–3 dedicated chart frames per memory card,
especially for the Nikon reference. R004 and R100 had **zero** detections in the
whole dataset and had to borrow; one opportunistic detection per card is too thin
to anchor four groups. See DECISIONS D010/D011.

## Parallelise step 02
Blur culling still decodes serially (~10 min on the full set). It could use the
same `ProcessPoolExecutor` pattern as steps 03/06 to run in a couple of minutes.

## Multi-camera "beauty shoot" convergence (future direction)
Goal: shoot a reference scene with every camera we use, then have the pipeline
learn how each camera should converge toward a common look — so any future shoot
from any of these bodies lands in the same colour space automatically. This is the
natural extension of the per-group CCM: build and store a per-camera profile once,
reuse it across shoots instead of needing the chart in every capture.

## Reference tools (shared by Andrea, 2026-07-23)
- **Charter app** — https://ssh4net.github.io/charter-app/guide/workflow
  A GUI colour-calibration tool that does exactly what our steps 03/04 automate:
  load a ColorChecker (CGATS chart, LAB/XYZ under D50), place it on the image,
  estimate white balance from the achromatic patches, estimate a CCM mapping
  camera → reference. **Validates our method** (fit in linear native/​camera space,
  WB from neutral greys, D50 reference). Good model for our own config GUI, and a
  cross-check for the maths. Worth reading its workflow before building the GUI.
- **FrameDistill** — https://framedistill.vk.land/
  Web tool that extracts the best frames from video for Gaussian Splatting
  (blur + similarity thresholds, PNG/JPEG export). Relevant two ways: (1) a clean
  UI reference for our threshold GUI, (2) if we move toward Gaussian Splatting /
  video capture, this is the frame-selection front-end analogue of our step 02.

## CCM/WB: mean vs median (robustness)
Current step 04 fits the CCM by pooled least-squares (a regression = mean-like) and
computes white balance from the **mean** of the three neutral grey patches — no
median anywhere. A single blown/glared patch can therefore tug the result. Options:
median of neutrals for WB, or a robust/weighted fit (e.g. down-weight high-residual
patches) for the CCM. Low effort, could tighten ΔE further.
