# Talagrand Lean Project

This is a standard Lean 4 project for Conjecta formalization candidates.

## Setup

```bash
cd conjecta/formalizations/talagrand
lake update       # fetches mathlib4 and generates lake-manifest.json
lake build        # builds the project
```

## Layout

```text
.
├── lakefile.lean
├── lean-toolchain              # pins Lean v4.30.0
├── AI4Math/
│   └── Talagrand/
│       └── Basic.lean          # placeholder for theorem statements
└── .lake/                      # build artifacts (ignored)
```

## Usage

When `LeanVerifier.verify_file()` is called on a `.lean` file inside this
directory, it will automatically detect `lakefile.lean` and run:

```bash
lake env lean <file>
```

This ensures imports resolve against the pinned `lean-toolchain` and mathlib
revision.
