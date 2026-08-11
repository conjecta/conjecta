import Lake
open Lake DSL

require mathlib from git
  "https://github.com/leanprover-community/mathlib4.git" @ "v4.30.0"

package «Talagrand» where
  -- add package configuration options here

@[default_target]
lean_lib AI4Math where
  -- add library configuration options here
