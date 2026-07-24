# Findings report — full dataset run & pipeline completion (2026-07-23)

Durable record of the analysis from the session that took the pipeline from
"steps 01–04 drafted" to a full end-to-end run + GUI. Decisions themselves live in
`DECISIONS.md` (D010–D012); this is the narrative and the evidence behind them.

## 1. Starting state (audit)

Steps 01–04 existed; 05–07 did not. Audit found, and this session fixed:
- a stray root duplicate of step 03;
- duplicate keys in `config.yaml`;
- steps 01/02 used flat globbing → couldn't scan the nested NAS tree (now `rglob`);
- Unicode console crashes on Windows cp1252 (ΔE / → / ×) → forced UTF-8 output;
- wrong RawTherapee CLI path in config.

## 2. Full ColorChecker scan (all 1249 RAW files, run `20260723_123506`)

Step 03 was hardened first: parallel decode+detect, crash-safe resumable cache
(re-run with the same run_id resumes). Scan took ~4 min.

| Group | Detections | Result |
|---|---|---|
| N02 (Nikon, reference) | 3 found, 1 rejected, 2 used | own matrix |
| R105 | 2 found, 2 used | own matrix |
| R004 | **0 anywhere** | borrows R105 |
| R100 | **0 anywhere** | borrows R105 |

**Key finding:** R004 and R100 have *zero* ColorChecker frames across the entire
dataset — the chart never swept into a detectable frame for those two cards. Not a
sampling artifact. They borrow R105 (Ricoh-borrows-Ricoh). Strongest argument for a
dedicated checker re-shoot per card next time.

## 3. Two bugs caught by verifying instead of trusting numbers

### 3a. Step-04 patch-scale bug (CCM off by ~1e6)
`colour-checker-detection` normalises its swatch output to 0..1 regardless of input
bit depth, but step 04 divided by 65535 (assumed 16-bit). This crushed patches to
~1e-5 and fit a ~1e6 CCM to compensate. The fit residual stayed *self-consistent*,
so step 04's own ΔE looked like a plausible ~7 and hid the bug — it was only caught
by rendering a frame and watching the colour blow out.

After the fix, the true 3×3 residual is **ΔE ~3** (N02 3.15, R105 3.87) — both
groups now pass the trust gate. **Lesson: never trust a fit residual alone; render
and re-measure.** (`tools/verify_render.py` is that gate.)

### 3b. RawTherapee ChannelMixer cannot apply a colour matrix
The plan was to write the CCM into RawTherapee's Channel Mixer. Empirically it is a
channel-blend / black-and-white tool, not a matrix multiply:
- CCM through it rendered at **ΔE ~30** (worse than no correction);
- a coefficient-scale probe on a neutral patch was **non-monotonic** — only the exact
  identity (100) behaved; every other value collapsed to a dark regime.

## 4. Render-path decision (evidence)

Measured on the 5 checker frames with `tools/verify_render.py`:

| Render path | N02 ΔE | R105 ΔE | verdict |
|---|---|---|---|
| No correction (RT default develop) | ~5 | ~6 | baseline |
| RawTherapee ChannelMixer | **~31** | **~33** | broken — rejected |
| Pure Python (rawpy + CCM) | **2.65** | 4.86 | correct |
| **Hybrid: RT develop → Python CCM (chosen)** | **2.6–4.3** | | correct + keeps RT quality |

Chosen: **hybrid** (D012) — RawTherapee develops a neutral 16-bit sRGB TIFF, Python
applies the linear CCM (the exact domain it was fitted in) and writes the JPEG. The
CCM already absorbs white balance (fitted on a camera-WB decode), so WB is not
re-applied — that would double-correct.

## 5. Final pipeline state

01–07 built and run end-to-end (run `20260723_152955`):
- 1249 RAWs → **617 kept** (632 dropped: 533 blurry, 99 no-flash);
- kept per group: N02 224, R004 206, R100 125, R105 62;
- **617 colour-corrected JPEGs** exported (later re-exported at q100 + 4:4:4 chroma
  into `hq_q100_444`, ~16 GB);
- QC report generated: colour/WB normalized to a shared reference; ΔE ~3 on the
  measurable groups.

## 6. Open items

- **Luminance not equalised across groups** — colour + WB are normalized, brightness
  is not (group medians N02 ~83 vs R100 ~69). Candidate future step (per-group
  luminance gain). See `docs/ideas.md`.
- **R100 is the weakest card** (lowest luminance; Auto-WB). R004 + R100 rest on a
  borrowed matrix.
- **Re-shoot dedicated ColorChecker frames per card** next museum visit — the real
  upstream fix for the thin/zero detections.
- Robustness idea: median/robust fit instead of mean for WB/CCM (`docs/ideas.md`).

## 7. What the numbers mean for the goal

The goal is cross-camera *homogeneity* anchored to one shared chart reference, for
photogrammetry texture — not lab-grade accuracy. ΔE ~3 (just-noticeable) comfortably
meets that. Any low-res look in RealityScan is a reconstruction-detail setting
(Normal vs High Detail + simplification), not a colour or JPEG-quality issue.
