---
name: vendor-api-quirks
description: Real gotchas found wiring engine/options/vendor/options_calc/fx into pricer.py/store.py -- read before touching Phase 5-7 pricer wiring.
metadata:
  type: project
---

Found while building engine/options/pricer.py (Phase 2/4, 2026-09-17):

1. **Submodule-name shadowing.** `engine/options/vendor/options_calc/fx/__init__.py`
   does `from .one_touch import one_touch, no_touch`. Because the imported
   NAME equals the submodule's own name (`one_touch`), this REBINDS the
   `fx` package's `one_touch` attribute from the submodule object to the
   FUNCTION object. So `from .vendor.options_calc.fx import one_touch`
   anywhere outside that package imports the FUNCTION, not the submodule --
   `one_touch.one_touch(...)` raises `AttributeError: 'function' object has
   no attribute 'one_touch'`. Same risk applies to any other vendored
   submodule whose own name collides with something `fx/__init__.py` (or
   `options_calc/__init__.py`) re-exports under the same name. `barrier`,
   `european`, `digital`, `american`, `asian` are all re-exported as
   `price_barrier`/`price_european`/etc (different name), so they don't
   collide -- only `one_touch`/`no_touch` do, because those two happen to
   keep their own function names on export.

2. **Local parameter names shadowing a lazy import.** A silly but real bug:
   if a wrapper function has a parameter named e.g. `barrier` (the numeric
   level) and then does `from .vendor.options_calc.fx import barrier`
   inside that same function body, the import REBINDS the parameter to the
   module object, silently corrupting the numeric value passed to
   QuantLib (`TypeError: in method 'new_BarrierOption', argument 2 of type
   'Real'` was the resulting error -- not obviously about a name collision
   at first glance). Fix: alias the import (`import barrier as _barrier_mod`)
   whenever a wrapper's own parameter name matches a vendored submodule
   name.

3. **`options_calc/_engine.py::build_process` always uses
   `ql.Date.todaysDate()`** (the real system clock date) as QuantLib's
   evaluation date -- NOT whatever `as_of` a caller passes in. This is
   harmless, not a bug to fix: every date the engine builds (option
   maturity) is `today + T*365 days`, so pricing is purely a function of
   `T` (year fraction), never of the absolute calendar date. `pricer.py`
   computes `T = (expiry - as_of).days / 365.0` (Act/365, matching the
   vendored `ql.Actual365Fixed()`) and passes only `T` through, so a
   historical `as_of` prices identically regardless of the real clock date.
   Do not try to force QuantLib's evaluationDate to the historical `as_of`
   directly -- unnecessary, and the vendored engine isn't built to expect it
   (each pricer call constructs its own fresh process/dates internally).

4. **The whole vendor package requires QuantLib just to import, at every
   level.** `engine/options/vendor/options_calc/__init__.py` eagerly
   imports every asset-class subpackage (fx, equity, rates, commodity),
   each of which (e.g. `fx/__init__.py`) eagerly imports its own pricer
   modules, which import QuantLib unconditionally. This means even
   `from engine.options.vendor.options_calc.structures import combine`
   (a submodule with literally zero imports of its own) still triggers the
   full QuantLib-requiring import chain via the parent packages' `__init__.py`.
   Every one of `pricer.py`/`inputs.py`/`store.py`/`structures.py` therefore
   imports from `vendor` LOCALLY (inside each function), never at module
   top level -- mirrors `engine/rates/store.py`'s own placement of
   `import QuantLib as ql` inside `bootstrap_and_store`, not at module top.
   Keep this pattern for any future engine/options module: importing
   `engine.options.*` itself must stay safe without QuantLib installed;
   only calling into a pricing function should require it.

5. **`marks_official` view has no CASE branch for GAMMA/THETA/VEGA/RHO.**
   `data/ingest/schema.py::OFFICIAL_MARK_SOURCE` only maps
   SPOT/FWD_OUTRIGHT/FUTURE_PX/PAR_RATE/PV_USD/DV01_USD/DELTA/PREMIUM.
   `store.py` writes GAMMA/THETA/VEGA/RHO marks too (task requirement), but
   they land in `marks` only -- `marks_official` silently excludes them
   (the view's `CASE mark_type ... END` has no ELSE, so an unmapped
   mark_type never matches any `source` and the row is filtered out). This
   is correct/expected per the task instructions, not a bug -- CLAUDE.md's
   mark_type list needs those four added by the housekeeper before any
   downstream reader can rely on `marks_official` for them. See
   [[phase-2-4-landed-2026-09-17]] for the full follow-up list.
