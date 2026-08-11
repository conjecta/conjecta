# Knowledge Graph Consolidation

This document describes the current memory-consolidation design for the
knowledge graph layer. The implementation keeps the existing
`facts` / `intuitions` / `tricks` memory views working while adding a graph
model for longer-term mathematical knowledge.

## Current Shape

Consolidation now treats `knowledge_graph` as the primary structured extraction
format:

```json
{
  "knowledge_graph": {
    "nodes": [
      {
        "ref": "n0",
        "kind": "theorem",
        "title": "Second moment existence theorem",
        "statement": "A positive second moment ratio implies existence.",
        "body": "Reusable probabilistic existence criterion.",
        "evidence": "The proof bounded the first two moments.",
        "source_type": "agent_trace",
        "status": "candidate",
        "metadata": {}
      }
    ],
    "edges": [
      {
        "ref": "e0",
        "source": "n0",
        "target": "n1",
        "kind": "uses_technique",
        "evidence": "The theorem is proved by bounding the first two moments.",
        "status": "candidate",
        "metadata": {}
      }
    ]
  },
  "facts": [],
  "intuitions": [],
  "tricks": []
}
```

The legacy arrays are still accepted for backward compatibility, but new
consolidation should prefer graph nodes and edges.

## Node Kinds

Supported node kinds are:

- `definition`
- `theorem`
- `lemma`
- `proposition`
- `corollary`
- `exercise`
- `technique`
- `intuition`
- `paper`
- `question`
- `viewpoint`
- `topic`
- `source`

The old `tricks` concept maps to graph node kind `technique`.

## Edge Kinds

Supported semantic edge kinds are:

- `depends_on`
- `uses_technique`
- `has_intuition`
- `generalizes`
- `special_case_of`
- `equivalent_to`
- `analogy_with`
- `formalizes_as`
- `connects_to`
- `introduces`
- `refines`
- `answers_question`
- `arises_from`

Edges must use local node refs from the same consolidation output, such as
`source: "n0"` and `target: "n1"`.

## Review Rules

Review now scores both graph nodes and graph edges.

Node scoring:

- `0.85-1.00`: accurate, specific, evidenced, and clearly reusable.
- `0.65-0.84`: likely useful but somewhat narrow or lightly evidenced.
- `0.40-0.64`: plausible but weak, vague, risky, or missing applicability.
- `0.00-0.39`: wrong, duplicate, too vague, unsafe, or not reusable.

Edge scoring:

- `0.85-1.00`: relation type and direction are correct, evidence directly
  supports the relationship, and the edge is useful for retrieval or future
  reasoning.
- `0.65-0.84`: relation is likely correct and useful, but evidence or direction
  is somewhat implicit.
- `0.40-0.64`: relation is plausible but underspecified, weakly evidenced, too
  generic, or the edge kind may be imprecise.
- `0.00-0.39`: unsupported, wrong direction, wrong relation type, connects the
  wrong nodes, duplicates trivial proximity, or would mislead downstream search.

Score-to-status mapping:

- `score >= 0.75` becomes `reviewed`.
- `0.40 <= score < 0.75` becomes `questioned`.
- `score < 0.40` becomes `rejected`.

Consolidation itself should only produce `candidate`, except when an item is
exactly tied to the accepted Lean-verified artifact for the solve. In that case
the system may mark it `verified`.

## Legacy Synchronization

The frontend and existing retrieval pipeline still read the old memory views:

- `facts`
- `intuitions`
- `tricks`

To keep that stable, graph nodes are synchronized into the old views:

- `theorem`, `definition`, `lemma`, `proposition`, `corollary`, and `exercise`
  become `facts`.
- `intuition` becomes `intuitions`.
- `technique` becomes `tricks`.

This means the current frontend should continue to work without schema changes.
The graph layer is additive.

## Current Persistence Boundary

The local `ProjectStore` can persist graph nodes and graph edges, including
review metadata such as `score`, `status`, and `review_note`.

Cloud Supabase persistence has not been changed. The current cloud-backed
knowledge store still uses the existing `facts`, `intuitions`, and `tricks`
tables.

## Design Intent

The graph model is intended to become the durable knowledge representation over
time. The old three-list memory model remains as a compatibility view for the
current frontend, search, and prompt injection paths.
