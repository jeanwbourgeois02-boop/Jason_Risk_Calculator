---
name: parallel-tab-wiring
description: How ui-shell wires a new tab whose module another lane is still writing, and the lint-hook noise when cwd is .claude/agents
metadata:
  type: project
---

When a tab lane writes `ui/tabs/<tab>.py` in parallel, ui-shell still imports it plainly in `ui/app.py` (like curve / expiries), so `ui.app` will not import until the module lands. To verify the wiring before then, run the ui-shell tests with a throwaway pytest plugin from the scratchpad (`-p <stub>` with the scratchpad on PYTHONPATH) that registers a stand-in `ui.tabs.<tab>` in `sys.modules` on the expiries interface (BODY_ID, REFRESH_ID, build_layout, register_callbacks). Never put the stub in the repo.

**Why:** the housekeeper brief (Phase 3, Spreads tab, 2026-09-24) said not to create another lane's module. The full wiring test waits on that lane.

**How to apply:** do the same for any new tab. Say in the Handoff that `ui.app` and every test that imports it stay red until the tab module lands.

Separately: the PostToolUse lint hook runs `tools/lint_hook.py` relative to the cwd. When the agent's cwd is `.claude/agents`, it errors "can't open file" after every Edit or Write. That error is environmental and the edit still lands. ruff is not installed in `.venv` either.
