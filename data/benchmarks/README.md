# Benchmark artifacts

Benchmark problem text is not stored in the public Git repository. This avoids
mixing the MIT-licensed code with third-party datasets and makes acceptance of
upstream terms explicit.

Build the permissively licensed/default set locally:

```bash
uv run python scripts/build_benchmark_suite.py
```

To additionally generate the MathArena sources licensed
`CC-BY-NC-SA-4.0`, explicitly opt in:

```bash
uv run python scripts/build_benchmark_suite.py --include-noncommercial
```

Raw downloads are cached under `_src/`; generated JSONL files and the generated
`manifest.json` are ignored by Git. The manifest records the upstream source,
URL, declared license, transformations, and row count for each artifact.

Do not redistribute generated files without checking the upstream license and
underlying problem-statement rights. In particular, non-commercial data must
not be shipped in a commercial product.
