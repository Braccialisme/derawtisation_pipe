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

*Add new entries below as decisions are made. Format: D00N — short title, date, decision, reason.*