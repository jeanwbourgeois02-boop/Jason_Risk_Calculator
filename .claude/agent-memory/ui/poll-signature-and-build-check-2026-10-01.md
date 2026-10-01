---
name: poll-signature-and-build-check-2026-10-01
description: Never change _poll's outputs (incl. ui.app's tick outputs) casually — 26ad429 did and old tabs 500'd every 15 s with no reload; now answer_unknown_polls + assets/build_check.js; restart-proof recipe
metadata:
  type: feedback
---

My 26ad429 change of the day roll's spare output (`header-as-of-picker.date` -> `.data`, to clear a console
"Invalid prop" error) changed `_poll`'s output key. Open tabs on the user's Bloomberg PC then got
`KeyError: Callback function not found` (500) every 15 s, and since the reload flag `page-stale` rides on
that poll, they never reloaded. User: "fix this".

**Why:** a page asks a newer server for the exact output key it was built with; an unknown key is a 500,
not a reload. I had wrongly claimed "the fingerprint reload covers it" — that reload rode on the same poll.

**How to apply:**
- Any change to `ui/revision.py::_poll` outputs or `ui/app.py`'s `tick` outputs is an interface change for
  every open browser tab. Prefer leaving them; never drop `page-stale.data` from them.
- Safety nets now (2026-10-01): `revision.answer_unknown_polls` (Flask before_request on
  `/_dash-update-component`: unknown output key containing `page-stale.data` -> 200
  `{"multi": true, "response": {"page-stale": {"data": true}}}`, logged once at INFO), and
  `ui/assets/build_check.js` (every 20 s + on tab visible: `/_risk_monitor_identity` vs
  `<meta name="risk-monitor-build">` = `launch.identity(build)` baked by `create_app`'s `meta_tags`; failed
  fetch / non-app answer = no reload; sessionStorage `risk-monitor-reloaded-for` stops loops). The identity
  route exists only under the launcher, so test apps never reload.
- Proof recipe: scratch `reload_check.py <db> poll|js|same` (.venv python, playwright): serve build A, restart
  as B on the same port, watch `window.__oldPage` vanish, capture server log records and the console.
