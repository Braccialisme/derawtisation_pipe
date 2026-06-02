# RawTherapee CLI — notes and .pp3 reference

## Running RawTherapee CLI

```bash
rawtherapee-cli -o output_folder -p sidecar.pp3 -j95 -js3 -c input.NEF
```

Flags used:
- `-o` output folder
- `-p` path to .pp3 sidecar profile
- `-j95` JPEG output at quality 95
- `-js3` JPEG subsampling (3 = best quality chroma)
- `-c` input RAW file

## .pp3 format

Plain text INI-style format. Each section controls one RawTherapee processing module.
Sections we write programmatically:

```ini
[Version]
AppVersion=5.10
Version=349

[White Balance]
Setting=Custom
Temperature=5200
Green=1.0

[Exposure]
Auto=false
Compensation=0.00
Brightness=0
Contrast=0

[Color Management]
InputProfile=...
WorkingProfile=sRGB
OutputProfile=sRGB

[RAW]
HighlightRecovery=1
```

Any section not present in the .pp3 falls back to RawTherapee defaults.

## Installation

Download from https://rawtherapee.com/ — use the installer and make sure
`rawtherapee-cli.exe` is accessible. Update `rawtherapee_cli` in `config.yaml`
with the correct path.
