---
name: pricing-conventions-doc-does-not-exist
description: docs/pricing_conventions.md is a reference-project-only file; this repo has no equivalent and none should be added under that name
metadata:
  type: project
---

The reference project ("Rates Swap Calculator", `C:\Users\jeanw\OneDrive\Documenti\Rates
Swap Calculator\swapcalc\`) refers constantly, in its own pricing module docstrings, to
`docs/pricing_conventions.md` for sign conventions, cross-currency collateral
assumptions, carry/roll-down definitions, etc. That file exists ONLY in the reference
project's own `docs/` tree, which was never ported.

**Why this matters**: a task brief for `engine/rates/` (2026-09-15) assumed
`docs/pricing_conventions.md` would exist in *this* repo (risk-monitor) and asked for
conventions notes to go there. It does not exist here, and per that task's own
correction, no such file is being added -- the conventions record instead lives in
`engine/rates/__init__.py`'s module docstring plus each ported module's own docstring
(`curves.py`, `conventions.py`, `store.py`; the swap modules were removed 2026-09-24).

**How to apply**: if a future task or another agent's docstring references
`docs/pricing_conventions.md` as if it's a real file in this repo, don't chase it --
it's a leftover mental model from the reference project. Point instead to
`engine/rates/__init__.py` (module map, curve convention)
and the individual module docstrings. If genuine cross-currency/term-rate/basis scope
is ever added to `engine/rates/`, that would be the moment to consider adding a real
`docs/pricing_conventions.md` here -- but that's outside this agent's directory
ownership (`docs/` is owned by the housekeeper), so it would need to be flagged to them,
not created directly.
