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
