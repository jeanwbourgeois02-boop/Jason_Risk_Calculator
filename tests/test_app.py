"""ui/app.py: the assembled top-bar layout. Narrow coverage for the 2026-09-17 change
(coordinator request mid-task): the "build <commit>" tag that used to sit in the
top-bar next to the tab strip is removed from the headline entirely -- it was sourced
from `ui.launch.build_label()` (still printed in the launcher's console banner,
ui/launch.py's `main()`, so the information is not lost, just no longer in the UI)."""
from __future__ import annotations

import pytest

dash = pytest.importorskip("dash", reason="dash is not installed in this environment")

from ui import app as uiapp  # noqa: E402


def _walk(component):
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for child in children:
            if hasattr(child, "children") or hasattr(child, "className"):
                yield from _walk(child)
    elif hasattr(children, "children") or hasattr(children, "className"):
        yield from _walk(children)


def test_build_tag_is_not_rendered_anywhere_in_the_layout(tmp_path):
    data = uiapp.empty_summary("database not found: missing")
    layout = uiapp.build_layout(data)
    class_names = [getattr(c, "className", None) or "" for c in _walk(layout)]
    assert not any("build-tag" in cls for cls in class_names)


def test_top_bar_still_has_the_tab_strip_and_upload_control(tmp_path):
    data = uiapp.empty_summary("database not found: missing")
    layout = uiapp.build_layout(data)
    top_bar = layout.children[0]
    assert top_bar.className == "top-bar"
    # Tabs + the upload strip -- no third "build" span between them any more.
    assert len(top_bar.children) == 2
