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


def test_title_cannot_claim_a_reserved_session_name_or_overflow():
    """The title becomes the new session's title: the canonical Bot Chat name
    is a registry key, and the store silently drops titles over its limit."""
    from hermes_state import SessionDB

    assert "error" in json.loads(st.spawn_task_tool(title=" bot chat ", prompt="p"))
    long = json.loads(st.spawn_task_tool(title="x" * 300, prompt="p"))
    assert long["success"] is True
    assert len(long["title"]) <= SessionDB.MAX_TITLE_LENGTH


def test_delegated_children_never_get_it():
    """A child's chip would render nowhere; the model must not be told the user
    sees one. Checked through the real schema assembly, not the deny list —
    a name in DELEGATE_BLOCKED_TOOLS alone does not strip a tool out of a
    mixed toolset like ``desktop_ui``. No cache resets between the builds: a
    desktop session builds first, then delegates (and the reverse, a child in
    one session then a new desktop session) inside the check_fn cache TTL."""
    from agent.delegation_context import delegated_child_context
    from model_tools import _clear_tool_defs_cache, get_tool_definitions
    from tools.registry import invalidate_check_fn_cache

    def names():
        return {
            row["function"]["name"]
            for row in get_tool_definitions(
                enabled_toolsets=["desktop_ui"], quiet_mode=True, skip_tool_search_assembly=True)
        }

    def child_names():
        with delegated_child_context():
            return names()

    invalidate_check_fn_cache()
    _clear_tool_defs_cache()
    assert "spawn_task" in names()  # parent first
    child = child_names()
    assert "spawn_task" not in child
    assert "focus_pane" in child  # only the offer tool is withdrawn, not the toolset

    invalidate_check_fn_cache()
    _clear_tool_defs_cache()
    assert "spawn_task" not in child_names()  # child first
    assert "spawn_task" in names()


def test_lives_only_in_the_desktop_surface_toolset():
    assert "spawn_task" in TOOLSETS["desktop_ui"]["tools"]
    assert "spawn_task" not in _HERMES_CORE_TOOLS
    assert registry.get_toolset_for_tool("spawn_task") == "desktop_ui"
