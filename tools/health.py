#!/usr/bin/env python
"""Health audit: the measures the infra agent works from, computed the same way every run.

    py 2_launcher.py health                 report; exit 1 on a hard breach (config/health.yaml)
    py 2_launcher.py health --json          the report as JSON on stdout
    py 2_launcher.py health --baseline      also fail when a ratchet measure is worse than
                                            config/health_baseline.json
    py 2_launcher.py health --update-baseline   record the current measures as the baseline
    python tools/health.py --compare A.json B.json   what changed between two reports

App code = data/, engine/, ui/, tools/ and the two root scripts; engine/options/vendor/ is
vendored as-is and never audited. Measures:

  ruff                unused imports / variables, undefined names, syntax errors (pyproject.toml)
  long_functions      functions over thresholds.function_lines
  large_modules       modules over thresholds.module_lines
  duplicate_helpers   a private helper name defined in thresholds.duplicate_helper_modules+ modules
  dated_citations     YYYY-MM-DD dates in comments and docstrings (chat provenance that belongs
                      in docs/decisions.md, not inline)
  unimported_modules  app modules nothing imports (config entry_points are exempt)
  layering            imports between top-level packages; config forbidden_imports are counted
  line_endings        tracked files by line ending, from `git ls-files --eol`
  broad_excepts       `except Exception:` and bare `except:` in app code
  todo_markers        TODO / FIXME / HACK / XXX in app code
  tests               test files and test functions under tests/

Nothing here reads the database or Bloomberg. Standard library plus PyYAML.
"""
from __future__ import annotations

import argparse
import ast
import collections
import io
import json
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "health.yaml"
BASELINE_PATH = ROOT / "config" / "health_baseline.json"
APP_DIRS = ("data", "engine", "ui", "tools")
ROOT_SCRIPTS = ("2_launcher.py", "3_diagnostic.py")
SKIP_PARTS = {"__pycache__", "vendor", ".venv", ".git"}
DATE_RE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
EXCEPT_RE = re.compile(r"^\s*except\s*(?P<what>[A-Za-z_][\w.]*)?\s*(?:as\s+\w+)?\s*:")
RUFF_PATHS = ("data", "engine", "ui", "tools", "tests", *ROOT_SCRIPTS)

DEFAULT_CONFIG: dict = {
    "thresholds": {"function_lines": 120, "module_lines": 1200, "duplicate_helper_modules": 3},
    "limits": {
        "hard": {"syntax_errors": 0, "undefined_names": 0, "mixed_line_endings": 0,
                 "forbidden_imports": 0, "unimported_modules": 0},
        "soft": {"unused_imports": 0, "unused_variables": 0, "long_functions": 0, "large_modules": 0,
                 "duplicate_helpers": 0, "dated_citations": 0, "todo_markers": 0, "crlf_files": 0},
    },
    "ratchet": ["ruff_total", "unused_imports", "unused_variables", "long_functions", "large_modules",
                "duplicate_helpers", "dated_citations", "todo_markers", "broad_excepts", "crlf_files"],
    "entry_points": list(ROOT_SCRIPTS) + ["tools/bloomberg_terminal_probe.py", "tools/health.py",
                                          "tools/lint_hook.py"],
    "forbidden_imports": [["data", "ui"], ["engine", "ui"], ["data", "tools"], ["engine", "tools"]],
}


# ----------------------------------------------------------------------------- inputs
def load_config(path: Path = CONFIG_PATH) -> dict:
    """config/health.yaml over DEFAULT_CONFIG, section by section, so a key the file leaves
    out keeps its default and the script runs with no file at all."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path.exists():
        return cfg
    try:
        import yaml
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        return cfg
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            for sub, sub_value in value.items():
                if isinstance(sub_value, dict) and isinstance(cfg[key].get(sub), dict):
                    cfg[key][sub].update(sub_value)
                else:
                    cfg[key][sub] = sub_value
        else:
            cfg[key] = value
    return cfg


def rel(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


def app_files(root: Path = ROOT) -> List[Path]:
    out: List[Path] = []
    for d in APP_DIRS:
        base = root / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if SKIP_PARTS.isdisjoint(p.relative_to(root).parts):
                out.append(p)
    out.extend(root / s for s in ROOT_SCRIPTS if (root / s).exists())
    return out


def test_files(root: Path = ROOT) -> List[Path]:
    base = root / "tests"
    if not base.exists():
        return []
    return [p for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse(path: Path, root: Path = ROOT) -> Tuple[Optional[ast.AST], Optional[str]]:
    try:
        return ast.parse(read(path)), None
    except SyntaxError as exc:
        return None, f"{rel(path, root)}:{exc.lineno}: {exc.msg}"


def module_name(path: Path, root: Path = ROOT) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ----------------------------------------------------------------------------- measures
def measure_functions(files: List[Path], min_lines: int, root: Path = ROOT) -> Tuple[List[dict], List[str]]:
    long_ones, syntax_errors = [], []
    for p in files:
        tree, err = parse(p, root)
        if err:
            syntax_errors.append(err)
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n = node.end_lineno - node.lineno + 1
                if n >= min_lines:
                    long_ones.append({"file": rel(p, root), "name": node.name, "line": node.lineno, "lines": n})
    long_ones.sort(key=lambda r: (-r["lines"], r["file"], r["line"]))
    return long_ones, syntax_errors


def measure_modules(files: List[Path], min_lines: int, root: Path = ROOT) -> Tuple[List[dict], int]:
    large, total = [], 0
    for p in files:
        n = read(p).count("\n") + 1
        total += n
        if n >= min_lines:
            large.append({"file": rel(p, root), "lines": n})
    large.sort(key=lambda r: (-r["lines"], r["file"]))
    return large, total


def measure_duplicate_helpers(files: List[Path], min_modules: int, root: Path = ROOT) -> List[dict]:
    where: Dict[str, set] = collections.defaultdict(set)
    for p in files:
        tree, err = parse(p)
        if err:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name.startswith("_") and not name.startswith("__") and name != "_main":
                    where[name].add(rel(p, root))
    out = [{"name": n, "modules": sorted(ms)} for n, ms in where.items() if len(ms) >= min_modules]
    out.sort(key=lambda r: (-len(r["modules"]), r["name"]))
    return out


def _comment_and_docstring_text(path: Path) -> str:
    src = read(path)
    pieces: List[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                pieces.append(tok.string)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "\n".join(pieces)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                pieces.append(doc)
    return "\n".join(pieces)


def measure_dated_citations(files: List[Path], root: Path = ROOT) -> dict:
    by_file = {}
    for p in files:
        n = len(DATE_RE.findall(_comment_and_docstring_text(p)))
        if n:
            by_file[rel(p, root)] = n
    return {"total": sum(by_file.values()), "by_file": dict(sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0])))}


def _imports_of(path: Path) -> List[str]:
    tree, err = parse(path)
    if err:
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def measure_unimported(files: List[Path], all_files: List[Path], entry_points: List[str], root: Path = ROOT) -> List[str]:
    imported: set = set()
    for p in all_files:
        for name in _imports_of(p):
            imported.add(name)
            parts = name.split(".")
            imported.update(".".join(parts[:i]) for i in range(1, len(parts)))
    exempt = set(entry_points)
    out = []
    for p in files:
        r = rel(p, root)
        if r in exempt or p.name == "__init__.py":
            continue
        if module_name(p, root) not in imported:
            out.append(r)
    return sorted(out)


def _import_statements(path: Path) -> List[str]:
    """One dotted module per import statement (`from a.b import c, d` counts once as a.b)."""
    tree, err = parse(path)
    if err:
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def measure_layering(files: List[Path], forbidden: List[List[str]], root: Path = ROOT) -> dict:
    counts: Dict[Tuple[str, str], int] = collections.Counter()
    where: Dict[Tuple[str, str], set] = collections.defaultdict(set)
    for p in files:
        parts = p.relative_to(root).parts
        pkg = parts[0] if len(parts) > 1 else "root"
        for name in _import_statements(p):
            top = name.split(".")[0]
            if top in APP_DIRS and top != pkg:
                counts[(pkg, top)] += 1
                where[(pkg, top)].add(rel(p, root))
    edges = [{"from": a, "to": b, "count": n} for (a, b), n in sorted(counts.items())]
    bad = {tuple(pair) for pair in forbidden}
    violations = [{"from": a, "to": b, "count": n, "files": sorted(where[(a, b)])}
                  for (a, b), n in sorted(counts.items()) if (a, b) in bad]
    return {"edges": edges, "forbidden": violations}


def measure_line_endings(root: Path = ROOT) -> dict:
    if not shutil.which("git") or not (root / ".git").exists():
        return {"available": False}
    try:
        out = subprocess.run(["git", "ls-files", "--eol"], cwd=str(root), capture_output=True,
                             text=True, check=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError):
        return {"available": False}
    counts: Dict[str, int] = collections.Counter()
    crlf_files, mixed_files = [], []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        meta, path = cols[0], cols[-1]
        index = next((m[2:] for m in meta.split() if m.startswith("i/")), "")
        if not index:
            continue
        counts[index] += 1
        if index == "mixed":
            mixed_files.append(path)
        elif index == "crlf" and "eol=crlf" not in meta:      # *.cmd / *.ps1 are meant to be CRLF
            crlf_files.append(path)
    return {"available": True, "counts": dict(counts), "crlf_files": crlf_files, "mixed_files": mixed_files}


def measure_excepts(files: List[Path], root: Path = ROOT) -> dict:
    broad, bare, by_file = 0, 0, collections.Counter()
    for p in files:
        for line in read(p).splitlines():
            m = EXCEPT_RE.match(line)
            if not m:
                continue
            what = m.group("what")
            if what is None:
                bare += 1
                by_file[rel(p, root)] += 1
            elif what in ("Exception", "BaseException"):
                broad += 1
                by_file[rel(p, root)] += 1
    return {"broad": broad, "bare": bare, "by_file": dict(by_file.most_common())}


def measure_todos(files: List[Path], root: Path = ROOT) -> dict:
    by_file = collections.Counter()
    for p in files:
        n = len(TODO_RE.findall(_comment_and_docstring_text(p)))
        if n:
            by_file[rel(p, root)] = n
    return {"total": sum(by_file.values()), "by_file": dict(by_file.most_common())}


def measure_tests(root: Path = ROOT) -> dict:
    files = test_files(root)
    functions = 0
    for p in files:
        tree, err = parse(p)
        if err:
            continue
        functions += sum(1 for node in ast.walk(tree)
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"))
    return {"files": len(files), "functions": functions}


def ruff_command() -> Optional[List[str]]:
    """`python -m ruff` from this interpreter first (the venv's), then a ruff on PATH."""
    try:
        subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True, check=True, timeout=30)
        return [sys.executable, "-m", "ruff"]
    except (subprocess.SubprocessError, OSError):
        pass
    exe = shutil.which("ruff")
    return [exe] if exe else None


def measure_ruff(root: Path = ROOT) -> dict:
    cmd = ruff_command()
    if cmd is None:
        return {"available": False, "total": 0, "by_code": {}, "by_file": {}, "findings": []}
    paths = [p for p in RUFF_PATHS if (root / p).exists()]
    try:
        proc = subprocess.run([*cmd, "check", "--output-format", "json", "--exit-zero", *paths],
                              cwd=str(root), capture_output=True, text=True, timeout=300)
        findings = json.loads(proc.stdout or "[]")
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return {"available": False, "error": repr(exc), "total": 0, "by_code": {}, "by_file": {}, "findings": []}
    by_code, by_file = collections.Counter(), collections.Counter()
    rows = []
    for f in findings:
        code = f.get("code") or "SYNTAX"
        filename = f.get("filename", "")
        try:
            filename = Path(filename).resolve().relative_to(root).as_posix()
        except ValueError:
            pass
        by_code[code] += 1
        by_file[filename] += 1
        rows.append({"file": filename, "line": (f.get("location") or {}).get("row"), "code": code,
                     "message": f.get("message", "")})
    rows.sort(key=lambda r: (r["file"], r["line"] or 0, r["code"]))
    return {"available": True, "total": len(rows), "by_code": dict(sorted(by_code.items())),
            "by_file": dict(by_file.most_common()), "findings": rows}


# ----------------------------------------------------------------------------- report
def git_head(root: Path = ROOT) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(root), capture_output=True,
                              text=True, check=True, timeout=30).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def collect(root: Path = ROOT, config: Optional[dict] = None) -> dict:
    cfg = config or load_config()
    th = cfg["thresholds"]
    files = app_files(root)
    tests = test_files(root)
    long_functions, syntax_errors = measure_functions(files, int(th["function_lines"]), root)
    large_modules, app_lines = measure_modules(files, int(th["module_lines"]), root)
    report = {
        "git_head": git_head(root),
        "app_files": len(files),
        "app_lines": app_lines,
        "syntax_errors": syntax_errors,
        "ruff": measure_ruff(root),
        "long_functions": long_functions,
        "large_modules": large_modules,
        "duplicate_helpers": measure_duplicate_helpers(files, int(th["duplicate_helper_modules"]), root),
        "dated_citations": measure_dated_citations(files, root),
        "unimported_modules": measure_unimported(files, files + tests, list(cfg["entry_points"]), root),
        "layering": measure_layering(files, cfg["forbidden_imports"], root),
        "line_endings": measure_line_endings(root),
        "excepts": measure_excepts(files, root),
        "todo_markers": measure_todos(files, root),
        "tests": measure_tests(root),
    }
    report["summary"] = summarise(report)
    return report


def summarise(report: dict) -> Dict[str, int]:
    ruff = report["ruff"]
    by_code = ruff.get("by_code", {})
    eol = report["line_endings"]
    return {
        "syntax_errors": len(report["syntax_errors"]) + by_code.get("SYNTAX", 0),
        "undefined_names": by_code.get("F821", 0),
        "unused_imports": by_code.get("F401", 0),
        "unused_variables": by_code.get("F841", 0),
        "ruff_total": ruff.get("total", 0),
        "long_functions": len(report["long_functions"]),
        "large_modules": len(report["large_modules"]),
        "duplicate_helpers": len(report["duplicate_helpers"]),
        "dated_citations": report["dated_citations"]["total"],
        "unimported_modules": len(report["unimported_modules"]),
        "forbidden_imports": sum(v["count"] for v in report["layering"]["forbidden"]),
        "mixed_line_endings": len(eol.get("mixed_files", [])),
        "crlf_files": len(eol.get("crlf_files", [])),
        "broad_excepts": report["excepts"]["broad"],
        "bare_excepts": report["excepts"]["bare"],
        "todo_markers": report["todo_markers"]["total"],
        "app_files": report["app_files"],
        "app_lines": report["app_lines"],
        "test_files": report["tests"]["files"],
        "test_functions": report["tests"]["functions"],
    }


def breaches(summary: Dict[str, int], cfg: dict) -> List[dict]:
    out = []
    for level in ("hard", "soft"):
        for measure, limit in cfg["limits"].get(level, {}).items():
            value = summary.get(measure, 0)
            if value > limit:
                out.append({"level": level, "measure": measure, "value": value, "limit": limit})
    return out


def worse_than_baseline(summary: Dict[str, int], baseline: Dict[str, int], ratchet: List[str]) -> List[dict]:
    return [{"measure": m, "baseline": baseline[m], "value": summary.get(m, 0)}
            for m in ratchet if m in baseline and summary.get(m, 0) > baseline[m]]


def compare_summaries(a: Dict[str, int], b: Dict[str, int]) -> List[str]:
    lines = []
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        if va != vb:
            arrow = "better" if isinstance(va, int) and isinstance(vb, int) and vb < va else "worse"
            if key in ("app_files", "app_lines", "test_files", "test_functions"):
                arrow = "changed"
            lines.append(f"{key}: {va} -> {vb} ({arrow})")
    return lines


def render(report: dict, cfg: dict, baseline_worse: Optional[List[dict]] = None) -> str:
    s = report["summary"]
    brs = breaches(s, cfg)
    hard = [b for b in brs if b["level"] == "hard"]
    soft = [b for b in brs if b["level"] == "soft"]
    out = [f"health  (code {report['git_head'] or 'n/a'}; {s['app_files']} app files, {s['app_lines']} lines; "
           f"{s['test_functions']} tests in {s['test_files']} files)"]
    ruff = report["ruff"]
    out.append("  ruff            " + (f"{ruff['total']} finding(s) " + ", ".join(f"{k} {v}" for k, v in ruff["by_code"].items())
                                       if ruff.get("available") else "not installed (py 2_launcher.py setup)"))
    out.append(f"  syntax errors   {s['syntax_errors']}")
    out.append(f"  long functions  {s['long_functions']} over {cfg['thresholds']['function_lines']} lines"
               + (": " + ", ".join(f"{r['file']}::{r['name']} ({r['lines']})" for r in report["long_functions"][:5]) if report["long_functions"] else ""))
    out.append(f"  large modules   {s['large_modules']} over {cfg['thresholds']['module_lines']} lines"
               + (": " + ", ".join(f"{r['file']} ({r['lines']})" for r in report["large_modules"][:5]) if report["large_modules"] else ""))
    out.append(f"  dup helpers     {s['duplicate_helpers']} name(s) in {cfg['thresholds']['duplicate_helper_modules']}+ modules"
               + (": " + ", ".join(r["name"] for r in report["duplicate_helpers"][:8]) if report["duplicate_helpers"] else ""))
    out.append(f"  dated comments  {s['dated_citations']} in {len(report['dated_citations']['by_file'])} file(s)")
    out.append(f"  unimported      {s['unimported_modules']}" + (": " + ", ".join(report["unimported_modules"]) if report["unimported_modules"] else ""))
    out.append(f"  layering        {s['forbidden_imports']} forbidden import(s)"
               + (": " + "; ".join(f"{v['from']}->{v['to']} in {', '.join(v['files'])}" for v in report["layering"]["forbidden"]) if report["layering"]["forbidden"] else ""))
    eol = report["line_endings"]
    out.append("  line endings    " + (f"{eol['counts']}, {s['crlf_files']} CRLF outside *.cmd/*.ps1, {s['mixed_line_endings']} mixed"
                                       if eol.get("available") else "git not available"))
    out.append(f"  excepts         {s['broad_excepts']} broad, {s['bare_excepts']} bare")
    out.append(f"  todo markers    {s['todo_markers']}")
    if hard:
        out.append("HARD breaches: " + "; ".join(f"{b['measure']} {b['value']} > {b['limit']}" for b in hard))
    if soft:
        out.append("soft breaches: " + "; ".join(f"{b['measure']} {b['value']} > {b['limit']}" for b in soft))
    if baseline_worse:
        out.append("WORSE than baseline: " + "; ".join(f"{w['measure']} {w['baseline']} -> {w['value']}" for w in baseline_worse))
    elif baseline_worse is not None:
        out.append("baseline: nothing worse")
    return "\n".join(out)


# ----------------------------------------------------------------------------- cli
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="health", description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--out", type=Path, help="also write the JSON report to this path")
    ap.add_argument("--baseline", nargs="?", const=str(BASELINE_PATH), default=None, metavar="PATH",
                    help=f"fail when a ratchet measure is worse than this baseline (default {BASELINE_PATH.name})")
    ap.add_argument("--update-baseline", nargs="?", const=str(BASELINE_PATH), default=None, metavar="PATH",
                    help="write the current summary as the baseline")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"), help="print what changed from A to B")
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = ap.parse_args(argv)

    if args.compare:
        a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare)
        lines = compare_summaries(a.get("summary", a), b.get("summary", b))
        print("\n".join(lines) if lines else "no change")
        return 0

    cfg = load_config(args.config)
    report = collect(ROOT, cfg)
    if args.out:
        args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    baseline_worse = None
    if args.baseline:
        bp = Path(args.baseline)
        if bp.exists():
            baseline = json.loads(bp.read_text(encoding="utf-8"))
            baseline_worse = worse_than_baseline(report["summary"], baseline.get("summary", baseline), cfg["ratchet"])
        else:
            baseline_worse = []
    if args.update_baseline:
        Path(args.update_baseline).write_text(
            json.dumps({"git_head": report["git_head"], "summary": report["summary"]}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        print(render(report, cfg, baseline_worse))
    hard = [b for b in breaches(report["summary"], cfg) if b["level"] == "hard"]
    return 1 if hard or baseline_worse else 0


if __name__ == "__main__":
    sys.exit(main())
