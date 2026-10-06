"""``spawn_task``: the agent offers a side task; the desktop renders it as a chip.

The tool never starts anything. The user picks the model and the launch mode
on the chip, so the contract here is: valid args come back as an "offered"
receipt the model can read, invalid args are refused, and the tool lives only
in the desktop surface toolset.
"""

import json

from tools import spawn_task_tool as st
from tools.registry import registry
from toolsets import _HERMES_CORE_TOOLS, TOOLSETS


def test_offers_the_task_without_starting_it():
    out = json.loads(st.spawn_task_tool(
        title="  Fix flaky login test ", prompt="Investigate tests/test_login.py flake.",
        tldr="Login test flakes on CI"))

    assert out["success"] is True
    assert out["status"] == "offered"
    assert out["title"] == "Fix flaky login test"


def test_refuses_a_chip_with_nothing_to_run():
    for title, prompt in (("", "do it"), ("Title", "   ")):
        out = json.loads(st.spawn_task_tool(title=title, prompt=prompt))
        assert "error" in out


def test_lives_only_in_the_desktop_surface_toolset():
    assert "spawn_task" in TOOLSETS["desktop_ui"]["tools"]
    assert "spawn_task" not in _HERMES_CORE_TOOLS
    assert registry.get_toolset_for_tool("spawn_task") == "desktop_ui"
