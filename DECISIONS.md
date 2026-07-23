# Technical Decisions Log

This file records every significant technical choice made in building this pipeline, with the reasoning behind it. Before modifying pipeline logic, read the relevant section here. When you make a new decision or change an existing one, add an entry.

---

## D001 — uv over conda for environment management

**Date:** 2026-06  
**Decision:** Use `uv` + `pyproject.toml` instead of conda or plain venv.  
**Reason:** conda is heavy, slow to resolve, and causes conflicts across machines. uv produces a `uv.lock` file that pins every dependency exactly — colleagues clone the repo, run `uv sync`, and have an identical environment in seconds. Works identically on Windows, Mac, and Linux.

---

## D002 — RawTherapee over darktable for batch RAW rendering

**Date:** 2026-06  
**Decision:** Use RawTherapee CLI (`rawtherapee-cli`) for batch RAW-to-JPEG export.  
**Reason:** darktable was tested on a previous batch project and found too rigid — it requires manual preset creation and cannot self-detect correction parameters. Its CLI requires SQLite database manipulation which is fragile to automate. RawTherapee's `.pp3` sidecar format is plain text (INI-style), fully generatable by Python, and its CLI is clean and well-documented.

---

## D003 — Per-card correction matrices, not per-camera

**Date:** 2026-06  
**Decision:** Compute one color correction matrix per memory card, not one per camera model.  
**Reason:** EXIF audit revealed that the three Ricoh GR II bodies used different white balance modes (`Multi-P Auto` on cards 100_0512 and 111RICOH, `Auto` on card 104RICOH). Treating all Ricoh files as one group would average over real per-body differences. Card origin is reliably detectable from filename prefix (`R004`, `R100`, `R105`) without EXIF lookup.

---

## D004 — Nikon D850 as color reference

**Date:** 2026-06  
**Decision:** Treat the Nikon D850 group as the reference and normalize Ricoh groups to it.  
**Reason:** The Nikon used a controlled external Godox flash with fixed white balance mode "Flash" — the most consistent and predictable lighting in the dataset. Normalizing to a synthetic neutral (e.g. D50) would introduce an artificial reference not grounded in the actual shoot. Normalizing Ricoh to Nikon keeps the pipeline anchored to something physical.

---

## D005 — Reject no-flash frames before processing

**Date:** 2026-06  
**Decision:** Flag and reject all frames where flash did not fire before any color processing.  
**Reason:** EXIF audit identified frames with `FlashMode = "Auto, Did not fire"` — these occur when the Ricoh interval shooting timer does not allow enough time for autofocus to complete before triggering. These frames are significantly darker and color-shifted; normalizing them into the same color space as flash-lit frames is not viable. They are rejected at step 02 (blur culling) alongside out-of-focus frames.

---

## D006 — ColorChecker detection on fast rawpy decode

**Date:** 2026-06  
**Decision:** Run ColorChecker detection on a half-size fast rawpy decode, not on the full-resolution image or the JPEG proxy.  
**Reason:** OpenCV and colour-checker-detection work on numpy arrays — rawpy can decode directly to numpy with `half_size=True`, which is significantly faster than full decode and sufficient for patch detection. Using JPEG proxies would require managing a separate file set and introduces an extra lossy encoding step before color measurement.

---

## D007 — JPEG output for photogrammetry

**Date:** 2026-06  
**Decision:** Export to JPEG (quality 95) rather than TIFF for photogrammetry input.  
**Reason:** Both RealityCapture and Metashape accept JPEG well. TIFF output would be 3–4× larger with no meaningful benefit for photogrammetric alignment. Disk space and transfer time across the NAS are real constraints at this scale.

---

## D008 — Runs folder is gitignored

**Date:** 2026-06  
**Decision:** All pipeline outputs go to `runs/YYYYMMDD_HHMMSS/` which is gitignored.  
**Reason:** Output data (CSVs, correction matrices, JPEGs) belongs on the NAS, not in the repository. The repo tracks code and config only. Each run folder is self-contained and auditable — it contains the full trace of every decision made during that run.

---

## D009 — Per-group blur thresholds

**Date:** 2026-06  
**Decision:** Use separate Laplacian variance thresholds per camera group rather than one global value.  
**Reason:** The Nikon D850 (45MP) produces much larger half-size decoded images than the Ricoh GR II. Laplacian variance is diluted across more pixels, producing systematically lower scores for the same perceived sharpness. A single threshold of 80 would reject most valid Nikon frames. Thresholds were calibrated empirically on mpr_sample2.

---

## D010 — Trust gate and fallback hierarchy for sparse ColorChecker detections

**Date:** 2026-06  
**Decision:** How step 04 decides whether to trust a group's own ColorChecker detection(s), and what a group does when it has none.

1. **Trust is measured by fit residual, not by step 03's "confidence".** Step 03's confidence only reports whether 24 patches were found — it says nothing about glare, shadow, or a mis-oriented chart. So step 04 fits the CCM, re-measures the patches, and computes the mean ΔE2000 against the reference. Gates (in `config.yaml > normalization`): mean ΔE ≤ `residual_deltaE_warn` (5.0) → *trusted*; between warn and `residual_deltaE_reject` (10.0) → *low_confidence* (used, but flagged for a human to eyeball); above reject → the detection is thrown out. A scrambled/oblique chart lands around ΔE 28–30, so this gate reliably catches bad detections.
2. **Multiple detections are pooled, not averaged as matrices.** Each detection is individually screened against the reject gate; the survivors' patches are stacked and one CCM is fitted on the combined set. Pooling averages out per-frame noise and is more stable than averaging independently-fitted matrices.
3. **A single surviving detection is trusted but flagged `thin`.** One detection is one sample of one geometry under one flash pop; it is used, but surfaced in QC so a human knows the group rests on thin evidence.
4. **Groups with no usable detection borrow a CCM**, following an explicit per-group `fallback_order` in config. The order encodes physical similarity: the three Ricoh bodies share a sensor and the native flash, so they borrow each other first; the Nikon group (different Godox flash) is only a last resort for them. **Nikon borrowing a Ricoh is an emergency** and is logged loudly — the reference group having no checker undermines the whole normalization, so it should trigger a re-shoot rather than a silent borrow. A group with no detection and no available donor is marked `failed`.
5. **The correction target is the absolute ColorChecker reference, not a relative transform to Nikon.** Because every group is mapped to the same chart reference, all groups converge to a consistent (and colorimetrically accurate) result. "Normalize toward N02" (D004) is therefore the *fallback anchor* role, not a separate maths path — N02 matters as the most-trusted source to borrow from, not as a per-pixel target.

**Reason:** The checker was only swept into frame opportunistically, so detection counts per group are uneven and some groups may have one or zero. We need a rule that (a) refuses to trust a bad detection just because it exists, (b) degrades gracefully toward the most physically similar group, and (c) makes the weak spots visible rather than hiding them. **Process note:** the cleanest fix is upstream — on the next museum visit, shoot 2–3 dedicated ColorChecker frames per memory card (especially for the Nikon reference group) so borrowing is never required. This is not overkill; one detection for the reference group is too thin to anchor four groups on.

---

## D011 — Full-dataset scan outcome + patch-scale bug + 3×3 CCM is enough

**Date:** 2026-07-23
**Decision:** Ship on a per-group 3×3 linear CCM. Record what the full scan found
and the scale bug it surfaced.

**Full checker scan (all 1249 RAW files, run `20260723_123506`):**
- **N02 (Nikon reference):** 3 detections found, 1 rejected by the D010 gate at
  ΔE ~30 (oblique/bad-geometry chart), 2 pooled → own matrix, **mean ΔE 3.15,
  trusted**.
- **R105:** 2 detections, both used → own matrix, **mean ΔE 3.87, trusted**.
- **R004 and R100:** **zero detections across the entire dataset** — the chart
  never swept into a detectable frame for these two cards. Both borrow R105
  (Ricoh-borrows-Ricoh, per D010 fallback_order). A genuine data gap, not a
  sampling artifact, and the strongest argument for the re-shoot note below.

**Patch-scale bug (fixed this session):** step 04 originally divided the stored
patch values by 65535, assuming 16-bit. But colour-checker-detection normalises
its swatch output to 0..1 regardless of input depth, so step 03 already stores
0..1. The stray divide crushed patches to ~1e-5 and fit a ~1e6 CCM to compensate.
Because the fit stayed self-consistent, the residual still *looked* like a
plausible ΔE ~7 — the bug was invisible in step 04's own numbers and was only
caught by rendering a frame and seeing the colour blow out. After the fix the true
3×3 residual is **ΔE ~3**, and both usable groups pass the trust gate. Lesson:
never trust a fit residual alone — render and re-measure (see tools/verify_render.py).

**Why 3×3 is enough:** at ΔE ~3 (just-noticeable) the linear 3×3 comfortably meets
the goal — cross-camera *homogeneity* anchored to one shared chart reference, for
photogrammetry texture, not lab-grade accuracy. A root-polynomial CCM
(`Finlayson 2015`) could reach ~ΔE 1–2 but is unnecessary here and would complicate
the render. Not adopted.

**Process note (reaffirms D010):** the clean fix for R004/R100 — and for anchoring
on only 2 opportunistic Nikon frames — is upstream: on the next museum visit shoot
2–3 dedicated ColorChecker frames per memory card. Zero or one opportunistic
detection per card is too thin to anchor a four-group normalization.

---

## D012 — Hybrid render: RawTherapee develops, Python applies the CCM

**Date:** 2026-07-23
**Decision:** Steps 05/06 render final JPEGs in two stages — RawTherapee develops
each RAW to a neutral 16-bit sRGB TIFF (demosaic, as-shot white balance, highlight
handling), then Python applies the group's 3×3 linear CCM and writes the JPEG.

**Reason:** the CCM is a colorimetric 3×3 fitted in linear sRGB (step 04).
RawTherapee has **no module that applies an arbitrary colour matrix**. We tested
its [Channel Mixer] thoroughly and it is a channel-blend / black-and-white tool,
not a matrix multiply: pushed through it the CCM produced ΔE ~30 (worse than no
correction), and a coefficient-scale probe on a neutral patch was non-monotonic —
only the exact identity (100) behaved, every other value collapsed. So a CCM
simply cannot be expressed as a RawTherapee ChannelMixer.

Three render paths were on the table (all empirically checked on the 5 checker
frames with tools/verify_render.py):
- **RawTherapee ChannelMixer** — ΔE ~30. Rejected: structurally impossible.
- **Pure Python (rawpy decode + CCM)** — ΔE ~3. Correct, but drops RawTherapee.
- **Hybrid (chosen)** — RawTherapee develop → Python CCM — **ΔE ~2.6–4.3**, on par
  with the fit residual. Keeps RawTherapee's superior demosaic/highlight/noise
  handling (the spirit of D002) while applying the matrix in the exact linear
  domain it was fitted in.

The CCM already absorbs white balance (fitted on a camera-WB decode), so the
develop profile uses `WhiteBalance=Camera` and the render does **not** apply the
wb_multipliers separately — doing so would double-correct. This **supersedes the
render half of D002**: RawTherapee stays, as a developer, not as the colour-matrix
applier. The proper RawTherapee-native alternative (a per-group DCP/ICC camera
profile via dcamprof) was considered and set aside as heavier tooling for no
quality gain over the hybrid.

---

*Add new entries below as decisions are made. Format: D00N — short title, date, decision, reason.*