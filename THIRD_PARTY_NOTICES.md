# Third-Party Notices

## Runtime dependencies

Python and JavaScript dependencies are installed from `uv.lock` and
`math_agent/web/frontend/package-lock.json` under their respective upstream
licenses. Production frontend dependencies bundled into the committed static
assets are listed with their license text in
`THIRD_PARTY_FRONTEND_LICENSES.txt`.

## Benchmark data

Third-party benchmark artifacts are intentionally not committed. The build
script can download and transform data from AIME, MathArena, Omni-MATH,
OlympiadBench, miniF2F, PutnamBench, Compfiles, and CombiBench. Generated files
retain their upstream terms.

The default build excludes sources marked `CC-BY-NC-SA-4.0`. Passing
`--include-noncommercial` opts into those sources for non-commercial use and
does not relicense them under MIT. Review the generated
`data/benchmarks/manifest.json` before redistribution, training, or commercial
use.
