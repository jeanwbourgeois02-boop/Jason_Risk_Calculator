---
name: windows-bash-paths
description: How to pass Windows paths to pytest through the Bash tool in this repo (backslashes get eaten)
metadata:
  type: reference
---

In the Bash tool (Git Bash) a path like `C:\Users\jeanw\risk-monitor\tests\test_ingest.py` loses its backslashes and pytest reports "file not found". Use forward slashes: `py -3 -m pytest C:/Users/jeanw/risk-monitor/tests/test_ingest.py -v -p no:cacheprovider`. `py -3` is Python 3.14.7, pytest 9.1.1.

**How to apply:** any pytest / file command the parent agent hands over with backslashes should be rewritten with forward slashes before running.
