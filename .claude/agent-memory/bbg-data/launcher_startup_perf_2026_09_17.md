---
name: launcher-startup-perf-2026-09-17
description: Measured causes and fixes for a slow `pnl` / `py 2_launcher.py start`, especially on a Bloomberg PC without a running Terminal or with slow GitHub/PyPI access.
metadata:
  type: project
---

Coordinator-reported complaint (2026-09-17): "the launcher is also too slow." Measured on
a non-Bloomberg dev PC, reproduced exactly by faking `blpapi` importable + nothing
listening on 8194 (matches a Bloomberg PC whose Terminal isn't running yet).

## Confirmed costs and fixes (all in data/bloomberg/live.py and 2_launcher.py, bbg-data's scope)

1. **`data.bloomberg.live.availability()` dual-stack "localhost" resolution.** Measured
   2.04s per call at the old `timeout=1.0` with no host remap; 0.514s after fixing (see
   below) — reproduced live on this dev PC by injecting a fake `blpapi` module. Cause:
   `socket.create_connection(("localhost", port))` tries `::1` first (getaddrinfo returns
   IPv6 before IPv4 on this Windows setup) and pays the full timeout before falling back
   to the working `127.0.0.1`. Fix: `availability()` now special-cases the literal string
   `"localhost"` -> connects to `"127.0.0.1"` directly (an explicit non-"localhost" host,
   e.g. a real B-PIPE address, is left untouched), and the default timeout dropped
   1.0s -> 0.5s. `2_launcher.py::tcp_open` got the identical fix for the same reason
   (used by `doctor`/`setup`'s Bloomberg-Terminal heuristic).
   - This call happens **twice** in `create_app(start_feed=True)`: once inside
     `start_feed_if_available`, once inside `start_auto_backfill` (both call
     `availability()` independently against the same host:port). Fixing the one function
     fixes both call sites — no need to touch `ui/app.py` (out of scope) at all.
   - Reproduced end-to-end: `create_app(start_feed=True)` timing with fake blpapi + no
     listener went from **4.07s -> 1.21s** on this dev PC (matches the coordinator's
     reported 4.18s -> ~1s expectation almost exactly).
   - Could NOT reproduce this number on this sandbox without faking blpapi (it isn't
     installed here, so the real `availability()` short-circuits on `ImportError` before
     ever touching a socket) — the fake-module trick above is how the timing was captured;
     worth remembering as the technique for any future "no Bloomberg" perf work here.

2. **`2_launcher.py::venv_imports_ok()` unconditional subprocess.** Measured 1.745s for
   the raw `VENV_PY -c "import dash, pandas, ..."` subprocess on this dev PC — paid on
   *every* `start`, even when nothing had changed, and paid *again* by whatever imports
   the same packages once `start` re-execs into `.venv`. Fix: `.venv/packages.stamp`
   (hash of `PACKAGES + DEV_PACKAGES` + `sys.version_info.major.minor`), written after a
   successful install (both in `cmd_setup` and inside `venv_imports_ok()` itself after a
   successful fallback check). `venv_imports_ok()` now checks the stamp first and returns
   `True` in ~0.002s when it matches, only falling back to the real subprocess when the
   stamp is missing or stale. Measured 1.745s -> 0.002s on a matching stamp.
   - Kept `venv_imports_ok()` a strict zero-arg function (no `force` parameter) on
     purpose: `tests/test_risk_cli.py` (out of scope, not ours to edit) monkeypatches it
     with a bare `lambda: True` in several tests; adding a parameter would have broken
     those call sites the moment `cmd_start` tried to pass it.

3. **`--refresh-packages` used to force an unconditional `pip install`.** After any
   GitHub code update, `sync_with_github()` re-execs with `--refresh-packages`, and the
   old condition `args.refresh_packages or not venv_imports_ok()` short-circuited
   `venv_imports_ok()` entirely whenever the flag was set — i.e. `pip install` ran on
   *every* start after *every* code change, regardless of whether `PACKAGES` itself had
   changed. On a PC with slow PyPI access this was reportedly the single biggest cost.
   Fix: the flag is no longer read in that condition at all; only `venv_imports_ok()`'s
   own (now stamp-backed) result decides. The `--refresh-packages` CLI flag is still
   defined/parsed (still passed by the re-exec) but is now effectively inert inside
   `cmd_start`.

4. **`sync_with_github()`'s git fetch timeout.** Was the shared `_git()` default of 90s;
   a slow/blocked GitHub (reported from a China-based Bloomberg PC) could stall every
   `start` for up to that long. Fix: the fetch call specifically now passes `timeout=10`,
   and a `subprocess.TimeoutExpired` on that call is reported as `"GitHub slow, running
   the code on disk"` (previously it fell into the generic `"git unavailable (...)"`
   branch, still correct behaviourally but a worse message). Every other `_git()` call
   (stash, merge --ff-only, reset --hard) keeps the 90s default — those are local,
   fast operations once fetch has already succeeded or failed.

5. **`setup --sample` no longer unconditionally seeds sample data.** Per the 2026-09-17
   "blotter upload replaces everything" rule (data-ingest's change to
   `data/ingest/upload.py`), sample data must only ever seed a genuinely empty database.
   `cmd_setup` now checks `_trades_count()` (a direct `SELECT COUNT(*) FROM trades`, no
   `ui.app` import needed) before running `_load_sample`, and prints a one-line skip
   message instead when trades already exist.

## Not changed / explicitly out of scope

- `ui/app.py:246-259` (`start_bloomberg_feed`, `create_app`) — owned by another agent;
  the fix above needed zero changes there since it works by making the shared
  `availability()` function itself fast rather than restructuring the call sites.
- Did not restructure `start_feed_if_available` or `start_auto_backfill` into a
  "return immediately, probe inside the thread" shape (what the coordinator's message
  literally asked for) because **both have existing out-of-scope tests
  (`tests/test_live.py`'s own test was fine to change, but `tests/test_auto_backfill.py`
  is not ours) that assert a synchronous `None` return when `availability()` reports
  unavailable** (`test_start_auto_backfill_without_bloomberg_writes_status_reason`
  asserts `t is None` synchronously). Restructuring to fully async would have broken that
  contract. The `availability()` speed fix achieves nearly all of the same wall-clock win
  without an architecture change or risk to that test.
