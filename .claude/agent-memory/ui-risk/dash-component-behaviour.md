---
name: dash-component-behaviour
description: Dash facts learned building the Risk tab (unset props are absent attributes, title hovers, DataTable tooltip props, numeric columns carrying strings), the zsh "=word" gotcha and the Windows `py -3 -` heredoc hang
metadata:
  type: project
---

Dash behaviour worth remembering when writing or testing a tab:

- A Dash component only has an attribute for a prop that was passed: `html.Span("x").style`
  raises AttributeError. In tests read `getattr(node, "style", None)`; in code pass
  `style={}` only when you want the attribute to exist.
- `html.Div(title=...)` / `html.Span(title=...)` is the plain browser hover; the header
  and the Risk cards use it for definitions and reasons. A nested title wins over the
  outer one, so a value span's reason hover sits inside a card whose hover is the definition.
- `dash_table.DataTable`: `tooltip_header={col_id: text}` gives column-header hovers;
  `tooltip_data` is one dict per row `{col: {"value": str, "type": "text"}}` and must be
  the same length as `data`; `tooltip_duration=None` keeps a tooltip open (documented).
  `ranking.with_footer` copies `tooltip_delay` / `tooltip_duration` to the footer only
  when set (not None).
- A `type: "numeric"` column shows a string cell raw ("n/a") and `ranking.sortable` ranks
  those last; a `filter_query` like `{col} = 'n/a'` styles them (muted italic).
- `app._setup_server()` runs Dash's layout validation without serving; with the header's
  as-of store and `revision.components()` in a stub layout it proves the tab's callback
  ids all exist. `dash._utils.to_json(component)` proves the render serialises (NaN or
  numpy scalars would fail here, not in pytest).
- Bash tool gotcha on this Mac (zsh): `echo ======X` fails with "=====X not found"
  (zsh expands a leading `=word`); quote the separator, and quote `--include='*.py'` for
  grep -r.

- Bash on the Windows PC (Git Bash): `py -3 -` with a heredoc or piped stdin hangs until the
  120 s timeout and leaves a stray python process (the housekeeper forbids it). Use `py -3 -c "..."`
  or a test; to clean up, match the PID through `/proc/<pid>/cmdline` and kill only your own.

**Why:** each cost a failed run (2026-09-22 building ui/tabs/risk.py; the py heredoc twice on 2026-09-24).
**How to apply:** when writing tests over Dash trees or eyeballing a render from a script.
