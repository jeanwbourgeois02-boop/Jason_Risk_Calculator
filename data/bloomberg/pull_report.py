"""Reads a pull_marks.py "<out>.diag.json" and prints a plain-text diagnostic report.

Unlike pull_marks.py (which must stay a single dependency-light file for the Bloomberg
machine), this module runs on a normal dev machine and may import from the repository.

CLI:
    py -3 data/bloomberg/pull_report.py path/to/marks.csv.diag.json
    py -3 data/bloomberg/pull_report.py path/to/probe.diag.json --open-questions docs/open-questions.md

Note: this is a report *renderer* for a completed pull_marks.py run, not a live
connectivity/correctness checker. For "is Bloomberg reachable and are official marks
correct right now", see tools/bbg_diagnostics.py (run_bloomberg_diagnostics, re-exported
at data/bloomberg/bbg_diagnostics.py for the UI). For a fully standalone terminal probe
with zero repo imports, see tools/bloomberg_terminal_probe.py.

Report sections: environment, probe results (if the diag JSON has any probe_name-tagged
requests), per-request-type counts of each outcome, every failure with its message, and
-- for a marks run -- which FWD_OUTRIGHT rows came from the direct path vs BBG_INTERP vs
failed. It also lists open-questions.md items 27-31, each with the probe evidence that
answers it (or "no evidence" if that probe step was never run or failed).

Exit code: non-zero if the diag JSON records a failure. The definition differs by mode
(mirrors pull_marks.py's own exit-code table -- see W-1):

- pull mode: an unhandled exception, a non-"OK" summary.outcome, summary.marks_csv_partial
  true, or a non-empty summary.failures list. A request-level classification (e.g. a
  FIELD_EXCEPTION on one ticker of a batch) does NOT by itself cause a non-zero exit here
  -- pull_marks.py's own requested-vs-written reconciliation is the sole source of truth
  for whether a pull actually lost data, and it already feeds marks_csv_partial/failures.
  Per-classification counts are still shown in the report so a "successful" run that
  papered over a Bloomberg-side field exception remains visible.
- probe mode: an unhandled exception, a non-"OK" summary.outcome (folds in
  PROBE_COMPLETE_WITH_FAILURES), or any non-candidate request classified as
  FIELD_EXCEPTION / SECURITY_ERROR / TIMEOUT / SESSION_ERROR / EXCEPTION. Candidate
  (`fwd_outright_direct_*`) probe steps are expected to fail sometimes and never count.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OPEN_QUESTIONS = REPO / "docs" / "open-questions.md"

# Which probe step(s) (pull_marks.run_probe's probe_name tags) answer each numbered
# open-questions item.
QUESTION_PROBE_NAMES: Dict[int, List[str]] = {
    27: ["fwd_outright_direct_primary", "fwd_outright_direct_alt_reference_date",
         "fwd_outright_direct_alt_fwd_outright_field"],
    28: ["tenor_1m", "tenor_3m", "fwd_points_scale"],
    29: ["spot_reference", "spot_historical"],
    30: ["fwd_outright_direct_primary", "tenor_1m", "tenor_3m"],
    31: ["es_settle_px_settle", "es_settle_px_last"],
}

FAILURE_CLASSIFICATIONS = {"FIELD_EXCEPTION", "SECURITY_ERROR", "TIMEOUT", "SESSION_ERROR", "EXCEPTION"}


def load_diag(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_open_questions(path) -> Dict[int, str]:
    """Extract the one-line text of questions 27-31 from docs/open-questions.md
    (numbered list items, "27. **Title.** rest of the line..."). Read-only: this module
    never edits docs/open-questions.md. Returns {} if the file is absent.

    Only the bold lead ("**Title.**") is kept, truncated at its closing `**` -- the full
    item can run several hundred characters and this report just needs a short label."""
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    out: Dict[int, str] = {}
    for m in re.finditer(r"^(\d+)\.\s+(.*)$", text, re.MULTILINE):
        n = int(m.group(1))
        if n in QUESTION_PROBE_NAMES:
            title = m.group(2).strip()
            bold = re.match(r"(\*\*.*?\*\*)", title)
            out[n] = bold.group(1) if bold else title
    return out


def _counts_by_request_type(requests: List[dict]) -> Dict[str, Dict[str, int]]:
    counts: Dict[str, Dict[str, int]] = {}
    for r in requests:
        rt = r.get("request_type", "UNKNOWN")
        c = r.get("classification") or "UNKNOWN"
        counts.setdefault(rt, {})
        counts[rt][c] = counts[rt].get(c, 0) + 1
    return counts


def _failures(requests: List[dict]) -> List[dict]:
    return [r for r in requests if r.get("classification") in FAILURE_CLASSIFICATIONS]


def _hard_failures(requests: List[dict]) -> List[dict]:
    """Like _failures(), but excludes probe candidate steps (W-5): the fwd_outright_direct_*
    alternatives are guesses tried on purpose expecting some to fail, so a candidate
    failing is not evidence anything is broken and must not make has_failure() report a
    problem. Non-candidate failures (and every failure in a pull run, which has no
    candidate steps) still count."""
    return [r for r in _failures(requests) if not r.get("candidate")]


def render_report(diag: dict, open_questions_path=DEFAULT_OPEN_QUESTIONS) -> str:
    lines: List[str] = []

    env = diag.get("environment", {}) or {}
    lines.append("=== environment ===")
    for k in ["python_version", "blpapi_version", "hostname", "host", "port",
              "session_started", "service_opened", "machine_time_local",
              "machine_time_america_new_york", "machine_timezone"]:
        if k in env:
            lines.append(f"  {k}: {env[k]}")

    requests = diag.get("requests", []) or []
    probe_requests = [r for r in requests if r.get("probe_name")]

    if probe_requests:
        lines.append("")
        lines.append("=== probe results ===")
        for r in probe_requests:
            candidate = " [candidate]" if r.get("candidate") else ""
            lines.append(f"  {r.get('probe_name')}{candidate}: {r.get('probe_description', '')}")
            lines.append(f"    request_type={r.get('request_type')} tickers={r.get('tickers')} "
                         f"fields={r.get('fields')} overrides={r.get('overrides')}")
            lines.append(f"    classification={r.get('classification')} detail={r.get('detail')}")
            if "scalar" in r:
                lines.append(f"    scalar={r['scalar']}")
            if r.get("late_responses"):
                lines.append(f"    late_responses={r['late_responses']}")

    lines.append("")
    lines.append("=== request counts by type and outcome ===")
    counts = _counts_by_request_type(requests)
    if not counts:
        lines.append("  (no requests recorded)")
    for rt, c in counts.items():
        lines.append(f"  {rt}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))

    failures = _failures(requests)
    lines.append("")
    lines.append(f"=== failures ({len(failures)}) ===")
    for r in failures:
        lines.append(f"  [{r.get('classification')}] {r.get('request_type')} "
                     f"tickers={r.get('tickers')} fields={r.get('fields')}: {r.get('detail')}")
        for sec in r.get("raw_response", []) or []:
            for fx in sec.get("fieldExceptions", []) or []:
                lines.append(f"      fieldException {sec.get('security')}.{fx.get('fieldId')}: {fx.get('message')}")
            if sec.get("securityError"):
                lines.append(f"      securityError {sec.get('security')}: {sec['securityError'].get('message')}")

    fwd_results = diag.get("fwd_outright_results", []) or []
    if fwd_results:
        lines.append("")
        lines.append("=== FWD_OUTRIGHT rows: direct vs BBG_INTERP vs failed ===")
        for r in fwd_results:
            lines.append(f"  {r['instrument_id']} {r['settle_date']}: {r['outcome']} "
                         f"(source={r.get('source')}, value={r.get('value')}, detail={r.get('detail')})")

    exc = diag.get("exception")
    if exc:
        lines.append("")
        lines.append("=== unhandled exception ===")
        lines.append(f"  {exc.get('type')}: {exc.get('message')}")
        lines.append(f"  traceback:\n{exc.get('traceback')}")

    summary = diag.get("summary", {}) or {}
    lines.append("")
    lines.append("=== summary ===")
    for k in ["mode", "outcome", "exit_code", "marks_csv_partial", "requested_rows", "written_rows"]:
        if k in summary:
            lines.append(f"  {k}: {summary[k]}")
    if summary.get("failures"):
        lines.append("  failures:")
        for f in summary["failures"]:
            lines.append(f"    {f.get('stage')}: {f.get('error')}")

    questions = _read_open_questions(open_questions_path)
    lines.append("")
    lines.append("=== open questions 27-31 ===")
    # W-6: a FIELD_EXCEPTION/SECURITY_ERROR is strong evidence (e.g. "BAD_FLD" tells you
    # the field name is wrong) and must be shown, not folded into "no evidence". "no
    # evidence" is reserved for steps that were never run at all, or that never got far
    # enough to say anything about the field/override being tested (TIMEOUT, or the
    # session/service never opened).
    NO_INFO_CLASSIFICATIONS = {None, "TIMEOUT", "SESSION_ERROR"}
    for n in range(27, 32):
        title = questions.get(n, "(docs/open-questions.md not found or item missing)")
        lines.append(f"  {n}. {title}")
        evidence = [r for r in probe_requests if r.get("probe_name") in QUESTION_PROBE_NAMES[n]]
        if not evidence:
            lines.append("     evidence: no evidence (probe not run)")
            continue
        informative = [r for r in evidence if r.get("classification") not in NO_INFO_CLASSIFICATIONS]
        if not informative:
            lines.append("     evidence: no evidence (probe step(s) never resolved: TIMEOUT/SESSION_ERROR)")
            continue
        for r in informative:
            lines.append(f"     evidence: {r.get('probe_name')} -> {r.get('classification')}: {r.get('detail')}")

    return "\n".join(lines)


def has_failure(diag: dict) -> bool:
    """W-1: the definition of "failure" differs by mode. In pull mode it follows
    summary.outcome / summary.marks_csv_partial / summary.failures / exception only --
    a request-level classification (e.g. one tenor ticker of eight coming back
    FIELD_EXCEPTION while interpolation still succeeds and every requested key is
    written) is reported in the counts/detail sections but must NOT by itself flip the
    exit code, since pull_marks.py's own requested-vs-written reconciliation is already
    the authoritative signal for whether the pull actually lost data. Probe mode has no
    such reconciliation (there's no "requested rows" to write), so it keeps the
    request-level check: any non-candidate hard failure among the probe steps counts."""
    if diag.get("exception"):
        return True
    summary = diag.get("summary") or {}
    outcome = summary.get("outcome")
    if outcome not in (None, "OK"):
        # Any non-"OK" outcome counts, including "PROBE_COMPLETE_WITH_FAILURES" (a
        # non-candidate probe step failed) and "FAILED"/"PROBE_SESSION_FAILED".
        return True
    if summary.get("marks_csv_partial"):
        return True
    if summary.get("failures"):
        return True
    if summary.get("mode") == "probe":
        if _hard_failures(diag.get("requests", []) or []):
            return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("diag_json", help="path to a <out>.diag.json written by pull_marks.py")
    parser.add_argument("--open-questions", default=str(DEFAULT_OPEN_QUESTIONS),
                         help="path to docs/open-questions.md (read-only)")
    args = parser.parse_args(argv)

    diag = load_diag(args.diag_json)
    print(render_report(diag, args.open_questions))
    return 1 if has_failure(diag) else 0


if __name__ == "__main__":
    sys.exit(main())
