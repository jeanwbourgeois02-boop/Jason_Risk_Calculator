"""tools/health.py: the audit the infra agent works from. Each measure is exercised on a
throw-away tree so the tests do not depend on the state of this repository; one test runs
the real audit end to end and checks its shape."""
import json
import textwrap

import pytest

from tools import health


def _tree(tmp_path):
    """A tiny app: data/ imports ui (forbidden), engine/ has a long function, two modules
    share a private helper, one module is imported by nothing, comments carry dates."""
    (tmp_path / "data").mkdir()
    (tmp_path / "engine").mkdir()
    (tmp_path / "ui").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "data" / "__init__.py").write_text("")
    (tmp_path / "engine" / "__init__.py").write_text("")
    (tmp_path / "ui" / "__init__.py").write_text("")
    (tmp_path / "data" / "feed.py").write_text(textwrap.dedent('''
        """Feed. Decided 2026-09-21 with the user."""
        import ui.app  # noqa: F401
        from engine import calc


        def _now():
            return calc.run()  # TODO tidy
    '''))
    (tmp_path / "engine" / "calc.py").write_text(textwrap.dedent('''
        def _now():
            return 1


        def run():
            # 2026-09-22: the long one
            x = 0
    ''') + "".join(f"    x += {i}\n" for i in range(130)) + "    return x\n")
    (tmp_path / "ui" / "app.py").write_text("def _now():\n    return 2\n\ntry:\n    pass\nexcept Exception:\n    pass\n")
    (tmp_path / "tools" / "orphan.py").write_text("VALUE = 1\n")
    (tmp_path / "tools" / "probe.py").write_text("VALUE = 2\n")
    (tmp_path / "tests" / "test_x.py").write_text("from data import feed\n\ndef test_a():\n    assert feed\n")
    return tmp_path


@pytest.fixture
def tree(tmp_path):
    return _tree(tmp_path)


def _cfg(**over):
    cfg = json.loads(json.dumps(health.DEFAULT_CONFIG))
    cfg["entry_points"] = ["tools/probe.py"]
    cfg.update(over)
    return cfg


def test_forbidden_imports_are_counted_with_their_files(tree):
    lay = health.measure_layering(health.app_files(tree), _cfg()["forbidden_imports"], tree)
    assert {(e["from"], e["to"], e["count"]) for e in lay["edges"]} == {("data", "ui", 1), ("data", "engine", 1)}
    assert lay["forbidden"] == [{"from": "data", "to": "ui", "count": 1, "files": ["data/feed.py"]}]


def test_unimported_modules_exempt_entry_points_and_packages(tree):
    files = health.app_files(tree)
    out = health.measure_unimported(files, files + health.test_files(tree), ["tools/probe.py"], tree)
    assert out == ["tools/orphan.py"]


def test_long_functions_duplicate_helpers_and_dated_comments(tree):
    files = health.app_files(tree)
    long_ones, syntax = health.measure_functions(files, 120, tree)
    assert syntax == []
    assert [(f["file"], f["name"]) for f in long_ones] == [("engine/calc.py", "run")]
    dups = health.measure_duplicate_helpers(files, 3, tree)
    assert dups == [{"name": "_now", "modules": ["data/feed.py", "engine/calc.py", "ui/app.py"]}]
    dated = health.measure_dated_citations(files, tree)
    assert dated == {"total": 2, "by_file": {"data/feed.py": 1, "engine/calc.py": 1}}
    assert health.measure_todos(files, tree)["total"] == 1
    assert health.measure_excepts(files, tree) == {"broad": 1, "bare": 0, "by_file": {"ui/app.py": 1}}


def test_collect_summary_breaches_and_ratchet(tree):
    cfg = _cfg()
    report = health.collect(tree, cfg)
    s = report["summary"]
    assert s["forbidden_imports"] == 1 and s["unimported_modules"] == 1 and s["long_functions"] == 1
    assert s["duplicate_helpers"] == 1 and s["dated_citations"] == 2 and s["test_functions"] == 1
    hard = {b["measure"] for b in health.breaches(s, cfg) if b["level"] == "hard"}
    assert hard == {"forbidden_imports", "unimported_modules"}
    worse = health.worse_than_baseline(s, {"dated_citations": 1, "long_functions": 5}, cfg["ratchet"])
    assert worse == [{"measure": "dated_citations", "baseline": 1, "value": 2}]
    text = health.render(report, cfg, worse)
    assert "HARD breaches" in text and "WORSE than baseline: dated_citations 1 -> 2" in text


def test_compare_summaries_names_direction():
    lines = health.compare_summaries({"unused_imports": 5, "app_lines": 10, "x": 1},
                                     {"unused_imports": 2, "app_lines": 12, "x": 3})
    assert lines == ["app_lines: 10 -> 12 (changed)", "unused_imports: 5 -> 2 (better)", "x: 1 -> 3 (worse)"]


def test_config_file_overrides_defaults_section_by_section(tmp_path):
    cfg_path = tmp_path / "health.yaml"
    cfg_path.write_text("thresholds:\n  function_lines: 50\nlimits:\n  hard:\n    undefined_names: 3\n")
    cfg = health.load_config(cfg_path)
    assert cfg["thresholds"]["function_lines"] == 50 and cfg["thresholds"]["module_lines"] == 1200
    assert cfg["limits"]["hard"]["undefined_names"] == 3 and cfg["limits"]["hard"]["syntax_errors"] == 0
    assert cfg["forbidden_imports"] == health.DEFAULT_CONFIG["forbidden_imports"]


def test_the_real_audit_runs_and_has_no_syntax_errors():
    report = health.collect(health.ROOT, health.load_config())
    assert report["summary"]["syntax_errors"] == 0
    assert report["summary"]["app_files"] > 50 and report["summary"]["test_functions"] > 1000
    assert "engine/options/vendor" not in " ".join(r["file"] for r in report["long_functions"])
