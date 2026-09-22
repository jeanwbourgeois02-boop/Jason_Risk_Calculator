"""PostToolUse hook (.claude/settings.json): ruff on the Python file Claude just edited.

Reads the hook's JSON from stdin, runs `python -m ruff check` (pyproject.toml rules) on the
file when it is a .py under this repository and outside engine/options/vendor, and hands the
findings back as additional context so the session sees them at once. It never blocks
(exit 0 always) and never edits the file: the fix is the model's to make.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINDING_RE = re.compile(r"^.+?:\d+:\d+: [A-Z]+\d+ ")     # ruff --output-format concise lines only


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool_input = payload.get("tool_input") or {}
    tool_response = payload.get("tool_response") or {}
    path = tool_input.get("file_path") or tool_response.get("filePath") or ""
    if not path.endswith(".py"):
        return 0
    target = Path(path)
    try:
        relative = target.resolve().relative_to(ROOT)
    except (ValueError, OSError):
        return 0
    if "vendor" in relative.parts or ".venv" in relative.parts or not target.exists():
        return 0
    try:
        proc = subprocess.run([sys.executable, "-m", "ruff", "check", "--output-format", "concise",
                               "--exit-zero", str(target)],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=25)
    except (subprocess.SubprocessError, OSError):
        return 0
    lines = [ln for ln in proc.stdout.splitlines() if FINDING_RE.match(ln)]
    if not lines:
        return 0
    message = f"ruff: {len(lines)} finding(s) in {relative.as_posix()}"
    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {"hookEventName": "PostToolUse",
                               "additionalContext": message + "\n" + "\n".join(lines[:12])},
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
