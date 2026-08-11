# Memory Metadata Design

This document defines the first-version metadata contract for long-term
mathematical memories. The goal is to preserve provenance and confidence so the
agent can reuse domain knowledge without treating every extracted item as a
verified fact.

## Common Fields

All memory kinds may carry these fields:

| Field | Meaning |
| --- | --- |
| `source_type` | Origin of the item: `user_prompt`, `agent_trace`, `pdf`, `web`, `lean_verified`, or `manual`. |
| `source_ref` | Stable reference to the source, such as a session id, URL, PDF filename/page, or Lean declaration name. |
| `source_title` | Optional human-readable source title. |
| `evidence` | Short supporting excerpt, trace observation, or Lean declaration snippet. |
| `confidence` | Confidence score from `0.0` to `1.0`; this is not a proof of truth. |
| `status` | Review state: `candidate`, `approved`, `reviewed`, `rejected`, or `verified`. |

| `domain` | Coarse mathematical domain, for example `number_theory`, `algebra`, `analysis`, or `combinatorics`. |
| `tags` | Comma-separated topic tags for v1, for example `prime,gcd,modular_arithmetic`. |
| `created_by` | Pipeline that created the item: `memory_consolidation`, `pdf_extraction`, `web_extraction`, `lean_promotion`, or `user`. |
| `review_note` | Optional note from a reviewer or user. |

Default trust policy:

| Source | Suggested confidence | Suggested status |
| --- | ---: | --- |
| Lean verified code | `1.0` | `verified` |
| Manual user-approved item | `0.9` | `approved` |
| PDF or web extraction with evidence | `0.6`-`0.8` | `candidate` |
| Agent trace consolidation | `0.5`-`0.75` | `candidate` |
| User prompt extraction | `0.4`-`0.8` | `candidate` |

Only `approved`, `reviewed`, and `verified` memories are eligible for default
solve-time context injection (`KnowledgeTrustPolicy.SOLVE_RETRIEVAL`).
`candidate` items may be shown in review flows or injected only under explicit
experimental modes.

## Facts

Facts are reusable mathematical statements: definitions, theorem statements,
lemmas, propositions, and claims.

Core fields:

| Field | Meaning |
| --- | --- |
| `statement` | Concise mathematical statement. |
| `why` | Why this fact is useful for future problems. |
| `formal_status` | `informal`, `formalized`, or `lean_verified`. |
| `lean_name` | Optional Lean declaration name, such as `Nat.Prime.dvd_mul`. |

Example:

```json
{
  "statement": "If p is prime and p divides ab, then p divides a or p divides b.",
  "why": "Useful for divisibility arguments in elementary number theory.",
  "formal_status": "lean_verified",
  "lean_name": "Nat.Prime.dvd_mul",
  "source_type": "lean_verified",
  "source_ref": "Nat.Prime.dvd_mul",
  "source_title": "Mathlib",
  "evidence": "theorem Nat.Prime.dvd_mul ...",
  "confidence": "1.0",
  "status": "verified",
  "domain": "number_theory",
  "tags": "prime,divisibility,gcd",
  "created_by": "lean_promotion"
}
```

## Intuitions

Intuitions are explanatory or strategic ideas. They should guide search, not be
treated as verified claims.

Core fields:

| Field | Meaning |
| --- | --- |
| `title` | Short label for the intuition. |
| `body` | The reusable insight or explanation. |
| `kind` | `heuristic`, `strategy`, `motivation`, `analogy`, `warning`, or `other`. |

Example:

```json
{
  "title": "Use modular residues to rule out square forms",
  "body": "When proving an integer cannot be a square, check its residue modulo a small base such as 4, 8, or 3.",
  "kind": "heuristic",
  "source_type": "agent_trace",
  "source_ref": "20260707-123000-abcd1234",
  "source_title": "User session",
  "evidence": "The proof succeeded after checking the expression modulo 4.",
  "confidence": "0.72",
  "status": "candidate",
  "domain": "number_theory",
  "tags": "modular_arithmetic,squares,contradiction",
  "created_by": "memory_consolidation"
}
```

## Tricks

Tricks are reusable proof actions or tactics. They should include applicability
and failure conditions where possible, because tricks are easy to over-apply.

Core fields:

| Field | Meaning |
| --- | --- |
| `title` | Short name of the proof technique. |
| `body` | Reusable tactic or method. |
| `category` | `descent`, `contradiction`, `induction`, `wlog`, `pigeonhole`, `modular`, `factorization`, `bounding`, `construction`, or `other`. |
| `applicability` | Conditions under which the trick is likely useful. |
| `failure_mode` | Common reason this trick may fail or cause hallucinated reasoning. |

Example:

```json
{
  "title": "Infinite descent on coprime equation",
  "body": "Assume a minimal positive solution, derive a smaller positive solution using divisibility, then contradict minimality.",
  "category": "descent",
  "applicability": "Diophantine equations with divisibility constraints and a natural size measure.",
  "failure_mode": "Does not apply if the derived solution is not strictly smaller or leaves the integer domain.",
  "source_type": "pdf",
  "source_ref": "fermat_notes.pdf#page=7",
  "source_title": "Notes on Infinite Descent",
  "evidence": "The proof constructs a smaller coprime pair from the original solution.",
  "confidence": "0.81",
  "status": "candidate",
  "domain": "number_theory",
  "tags": "infinite_descent,diophantine,coprime",
  "created_by": "pdf_extraction"
}
```
