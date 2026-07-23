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
