"""pu-agent — a minimal, transparent, local-first PU.

Four pillars, one module each:
  harness  → pu/runtime + pu/gateway  (scaffolding around the raw LLM)
  loop     → pu/loop                      (observe → reason → act → repeat)
             pu/graph                     (opt-in structure around the loop — extends this pillar)
  memory   → pu/memory                    (procedural / semantic / episodic)
  ops      → pu/ops + evals/              (trace → eval → gate → release)
"""

__version__ = "0.1.0"
