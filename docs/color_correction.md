# Color correction — methodology notes

## How correction matrices are computed

For each camera group where a ColorChecker is detected, we measure the 24 patch values from the image and compare them to the known reference values from the Macbeth ColorChecker Classic specification.

A 3×3 color correction matrix (CCM) is computed using least-squares fitting:

```
CCM = argmin || M · measured_patches - reference_patches ||²
```

This matrix, when applied to any pixel from that camera group, maps the camera's color response toward the reference color space.

## Why per-group matrices

Each camera group (per memory card) had different white balance behavior — see DECISIONS.md D003. Averaging across groups would introduce systematic error. Each group gets its own CCM derived from checker detections within that group.

If a group has no checker detections, it inherits the matrix from the most similar group (Ricoh groups fall back to each other before falling back to Nikon).

## Reference group

The Nikon D850 group is the reference (see DECISIONS.md D004). Ricoh groups are normalized to match the Nikon's color response, not to an abstract neutral.

## Target color space

sRGB — appropriate for JPEG output going into RealityCapture or Metashape.
