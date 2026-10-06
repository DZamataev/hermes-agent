"""The async-registry model label of a background delegate_task unit.

Zero config must keep the old label (the call's route model, None = "?"); routed tasks name their own models,
and a split unit names only ITS tasks' models.
"""

from __future__ import annotations

from types import SimpleNamespace

from tools.delegate_tool_dispatch import _Batch, _unit_model_label


def _unit(children_idx, task_route_models, creds_model=None):
    children = [(i, None, SimpleNamespace(model="parent-model")) for i in children_idx]
    return _Batch(
        task_list=[{"goal": f"g{i}"} for i in range(4)], children=children, parent_agent=None,
        creds={"model": creds_model}, context=None, top_role="leaf", max_children=4, live_deleg_id=None,
        live_writers=[], live_paths=[], origin_wake_sid="", origin_ui_session_id="", origin_owner_transport=None,
        origin_owner_session_record=None, origin_session_history_delivery=False, overall_start=0.0,
        task_route_models=task_route_models,
    )


def test_zero_config_keeps_the_old_label():
    """No route anywhere: the label is the call's creds model (None), NOT the parent's model on the child."""

    assert _unit_model_label(_unit([0, 1], [None, None, None, None])) is None


def test_a_split_unit_names_only_its_own_tasks_models():
    per_task = ["easy-model", "hard-model", "easy-model", None]
    assert _unit_model_label(_unit([1], per_task, creds_model="easy-model, hard-model")) == "hard-model"
    assert _unit_model_label(_unit([0, 2], per_task, creds_model="easy-model, hard-model")) == "easy-model"
    assert _unit_model_label(_unit([0, 1], per_task)) == "easy-model, hard-model"
    # An unrouted task in a routed call runs on the parent's model and is named, not dropped.
    assert _unit_model_label(_unit([3], per_task, creds_model="easy-model, hard-model")) == "parent-model"
    assert _unit_model_label(_unit([1, 3], per_task)) == "hard-model, parent-model"


def test_without_per_task_routes_falls_back_to_the_call_label():
    assert _unit_model_label(_unit([0], None, creds_model="m")) == "m"


def test_dispatch_registers_the_unit_label_not_the_call_summary():
    """The async registry gets THIS unit's label (what the parent model reads in the completion block)."""
    from unittest.mock import patch

    from tools.delegate_tool_dispatch import _dispatch_unit

    unit = _unit([1], ["easy-model", "hard-model", None, None], creds_model="easy-model, hard-model")
    with patch("tools.async_delegation.dispatch_async_delegation_batch", return_value={}) as dispatch:
        _dispatch_unit(unit, "call-1", None, {})
    assert dispatch.call_args.kwargs["model"] == "hard-model"


# ── through the real delegate_task: routes planned, children built, dispatch captured at the registry seam ──

def _labels(cfg, tasks):
    import threading
    from unittest.mock import MagicMock, patch

    from tools.delegate_tool import delegate_task

    parent = MagicMock()
    parent.base_url, parent.api_key, parent.provider = "https://api.anthropic.com", "k", "anthropic"
    parent.requested_provider, parent.api_mode, parent.model = "anthropic", "anthropic_messages", "claude-sonnet"
    parent.platform, parent.session_id = "cli", "parent-sid"
    parent.reasoning_config = {"enabled": True, "effort": "medium"}
    parent.providers_allowed = parent.providers_ignored = parent.providers_order = parent.provider_sort = None
    parent._session_db, parent._delegate_depth, parent._active_children = None, 0, []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = parent.tool_progress_callback = parent.thinking_callback = None
    parent._delegation_override, parent.capabilities, parent.request_overrides = None, {}, {}

    def _child(**kw):
        c = MagicMock()
        c.model = kw.get("model") or "claude-sonnet"
        return c

    seen = []

    def _dispatch(**kw):
        seen.append((kw.get("task_indexes"), kw.get("model")))
        return {"status": "dispatched", "delegation_id": f"d{len(seen)}"}

    with patch("tools.delegate_tool._load_config", return_value=cfg), \
            patch("tools.delegate_tool_config._load_config", return_value=cfg), \
            patch("tools.async_delegation.dispatch_async_delegation_batch", side_effect=_dispatch), \
            patch("run_agent.AIAgent", side_effect=_child):
        delegate_task(tasks=tasks, background=True, parent_agent=parent)
    return seen


GOAL = "Search the repository for every caller of the parser entry point and list them"
HARD = {"tiers": {"hard": {"model": "hard-model"}}}


def _t(i, **kw):
    return {"goal": f"{GOAL} #{i}", **kw}


def test_e2e_zero_config_label_is_unchanged():
    assert _labels({"independent_completions": True}, [_t(0), _t(1)]) == [([0], None), ([1], None)]


def test_e2e_mixed_unit_names_parent_and_tier_models():
    assert _labels(HARD, [_t(0), _t(1, tier="hard")]) == [(None, "claude-sonnet, hard-model")]


def test_e2e_split_units_name_only_their_own_children():
    seen = _labels({**HARD, "independent_completions": True}, [_t(0), _t(1, tier="hard"), _t(2, tier="easy")])
    assert seen == [([0], "claude-sonnet"), ([1], "hard-model"), ([2], "claude-sonnet")]


def test_e2e_group_unit_keeps_task_alignment():
    seen = _labels({**HARD, "independent_completions": True},
                   [_t(0, group="g1"), _t(1, tier="hard"), _t(2, group="g1", tier="hard")])
    assert sorted(seen, key=str) == sorted([([0, 2], "claude-sonnet, hard-model"), ([1], "hard-model")], key=str)
