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

*Add new entries below as decisions are made. Format: D00N — short title, date, decision, reason.*
