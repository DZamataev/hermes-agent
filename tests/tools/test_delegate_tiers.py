"""Difficulty-tier routing for delegate_task children (easy / normal / hard).

Contract: the model picks a fixed-name tier per task; the operator maps tiers to routes in config.
``normal`` IS the base ``delegation.*`` block; ``easy``/``hard`` live under ``delegation.tiers`` and
merge over it. An unconfigured tier behaves exactly like ``normal``.
"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from tools.delegate_tool import DELEGATE_TASK_SCHEMA, delegate_task
from tools.delegate_tool_config import (
    DELEGATION_TIERS,
    _merge_tier_config,
    _normalize_tier,
    _routing_cfg_for_tier,
)


class TestNormalizeTier:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_blank_is_normal(self, raw):
        assert _normalize_tier(raw) == "normal"

    def test_case_insensitive(self):
        assert _normalize_tier(" HARD ") == "hard"

    def test_every_declared_tier_round_trips(self):
        assert [_normalize_tier(t) for t in DELEGATION_TIERS] == list(DELEGATION_TIERS)

    def test_unknown_names_the_valid_set(self):
        with pytest.raises(ValueError) as exc:
            _normalize_tier("huge")
        for tier in DELEGATION_TIERS:
            assert tier in str(exc.value)


class TestMergeTierConfig:
    BASE = {
        "model": "base-model", "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
        "api_key": "base-key", "api_mode": "chat_completions", "command": "x", "args": ["--y"],
        "reasoning_effort": "medium", "max_iterations": 50,
    }

    def test_same_provider_keeps_route_bundle(self):
        merged = _merge_tier_config(self.BASE, {"model": "cheap", "provider": "openrouter"})
        assert merged["model"] == "cheap"
        assert (merged["base_url"], merged["api_key"], merged["api_mode"]) == (
            self.BASE["base_url"], self.BASE["api_key"], self.BASE["api_mode"])

    def test_model_only_tier_keeps_base_provider(self):
        merged = _merge_tier_config(self.BASE, {"model": "cheap"})
        assert (merged["provider"], merged["model"]) == ("openrouter", "cheap")

    def test_provider_switch_drops_inherited_route_bundle(self):
        merged = _merge_tier_config(self.BASE, {"provider": "deepseek", "model": "deepseek-chat"})
        assert (merged["provider"], merged["model"]) == ("deepseek", "deepseek-chat")
        for key in ("base_url", "api_key", "api_mode", "command", "args"):
            assert key not in merged
        assert merged["max_iterations"] == 50  # non-route keys survive

    def test_endpoint_switch_drops_base_request_overrides(self):
        """Base request_overrides can carry endpoint-specific headers (tokens); a new endpoint must not get them."""
        base = {**self.BASE, "request_overrides": {"extra_headers": {"X-Token": "base"}}}
        assert "request_overrides" not in _merge_tier_config(base, {"base_url": "http://other/v1"})
        assert "request_overrides" not in _merge_tier_config(base, {"provider": "deepseek"})
        assert _merge_tier_config(base, {"model": "cheap"})["request_overrides"] == base["request_overrides"]

    def test_base_url_switch_drops_inherited_route_bundle(self):
        merged = _merge_tier_config(self.BASE, {"base_url": "http://localhost:1234/v1"})
        assert merged["base_url"] == "http://localhost:1234/v1"
        for key in ("provider", "model", "api_key", "api_mode"):
            assert key not in merged

    def test_blank_values_are_absent_overrides(self):
        merged = _merge_tier_config(self.BASE, {"provider": "", "base_url": "  ", "model": "cheap"})
        assert (merged["provider"], merged["base_url"], merged["model"]) == (
            "openrouter", self.BASE["base_url"], "cheap")

    def test_tier_effort_overlays_base_effort(self):
        assert _merge_tier_config(self.BASE, {"reasoning_effort": "low"})["reasoning_effort"] == "low"

    def test_base_is_not_mutated(self):
        before = dict(self.BASE)
        _merge_tier_config(self.BASE, {"provider": "deepseek"})
        assert self.BASE == before


class TestRoutingCfgForTier:
    CFG = {
        "provider": "anthropic", "model": "sonnet", "reasoning_effort": "medium",
        "tiers": {"easy": {"model": "haiku", "reasoning_effort": "low"}, "hard": {}},
    }

    def test_normal_is_the_base_block_itself(self):
        assert _routing_cfg_for_tier(self.CFG, "normal") is self.CFG

    def test_configured_tier_merges_over_base(self):
        cfg = _routing_cfg_for_tier(self.CFG, "easy")
        assert (cfg["provider"], cfg["model"], cfg["reasoning_effort"]) == ("anthropic", "haiku", "low")

    def test_empty_tier_block_falls_back_to_normal(self):
        assert _routing_cfg_for_tier(self.CFG, "hard") is self.CFG

    @pytest.mark.parametrize("tiers", [None, "oops", [], {"hard": "oops"}])
    def test_malformed_tiers_fall_back_to_normal(self, tiers):
        cfg = {"model": "sonnet", "tiers": tiers}
        assert _routing_cfg_for_tier(cfg, "hard") is cfg


# ── delegate_task end to end (children built for real, run patched) ─────────────────────────────

def _parent():
    parent = MagicMock()
    parent.base_url = "https://parent.example/v1"
    parent.api_key = "parent-key"
    parent.provider = "openrouter"
    parent.requested_provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "parent-model"
    parent.platform = "cli"
    parent.reasoning_config = {"enabled": True, "effort": "xhigh"}
    parent.providers_allowed = parent.providers_ignored = parent.providers_order = parent.provider_sort = None
    parent._session_db = None
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent._delegation_override = None
    parent.capabilities = {}
    return parent


CFG = {
    "max_iterations": 10, "model": "normal-model", "reasoning_effort": "medium",
    "tiers": {
        "easy": {"model": "easy-model", "reasoning_effort": "low"},
        "hard": {"base_url": "http://localhost:9/v1", "api_key": "hard-key", "model": "hard-model",
                 "reasoning_effort": "high"},
    },
}


def _done(i):
    return {"task_index": i, "status": "completed", "summary": "ok", "api_calls": 1, "duration_seconds": 0.1}


def _spawn(tasks, cfg=CFG, parent=None, **kw):
    """Run delegate_task synchronously; return (result dict, AIAgent kwargs per built child, in task order)."""
    parent = parent or _parent()
    with patch("tools.delegate_tool._load_config", return_value=cfg), \
            patch("tools.delegate_tool._run_single_child", side_effect=lambda task_index, **_: _done(task_index)), \
            patch("run_agent.AIAgent") as MockAgent:
        MockAgent.side_effect = lambda **_: MagicMock()
        result = json.loads(delegate_task(tasks=tasks, parent_agent=parent, **kw))
    return result, [c.kwargs for c in MockAgent.call_args_list]


GOAL_A = "Search the repository for every caller of the parser entry point"
GOAL_B = "Review the parser redesign for correctness and missing edge cases"


class TestDelegateTaskTiers:
    def test_tasks_route_by_their_own_tier(self):
        result, built = _spawn([{"goal": GOAL_A, "tier": "easy"}, {"goal": GOAL_B, "tier": "hard"}])
        assert "error" not in result
        easy, hard = built
        assert easy["model"] == "easy-model"
        assert easy["base_url"] == "https://parent.example/v1"  # model-only tier keeps the parent route
        assert easy["reasoning_config"] == {"enabled": True, "effort": "low"}
        assert (hard["model"], hard["base_url"], hard["api_key"]) == ("hard-model", "http://localhost:9/v1", "hard-key")
        assert hard["reasoning_config"] == {"enabled": True, "effort": "high"}

    def test_task_without_tier_is_normal(self):
        _, (child,) = _spawn([{"goal": GOAL_A}])
        assert child["model"] == "normal-model"
        assert child["reasoning_config"] == {"enabled": True, "effort": "medium"}

    def test_unconfigured_tier_is_normal(self):
        cfg = {**CFG, "tiers": {}}
        _, (child,) = _spawn([{"goal": GOAL_A, "tier": "hard"}], cfg=cfg)
        assert child["model"] == "normal-model"

    def test_nothing_configured_inherits_parent(self):
        _, (child,) = _spawn([{"goal": GOAL_A, "tier": "hard"}], cfg={"max_iterations": 10})
        assert (child["model"], child["base_url"]) == ("parent-model", "https://parent.example/v1")
        assert child["reasoning_config"] == {"enabled": True, "effort": "xhigh"}

    def test_task_effort_beats_tier_effort(self):
        _, (child,) = _spawn([{"goal": GOAL_A, "tier": "easy", "reasoning_effort": "xhigh"}])
        assert child["model"] == "easy-model"
        assert child["reasoning_config"] == {"enabled": True, "effort": "xhigh"}

    def test_task_effort_none_disables_thinking(self):
        _, (child,) = _spawn([{"goal": GOAL_A, "reasoning_effort": "none"}])
        assert child["reasoning_config"] == {"enabled": False}

    def test_bad_tier_fails_before_any_child_is_built(self):
        result, built = _spawn([{"goal": GOAL_A, "tier": "easy"}, {"goal": GOAL_B, "tier": "huge"}])
        assert "Invalid delegation tier" in result["error"] and "Task 1" in result["error"]
        assert built == []

    def test_bad_effort_fails_before_any_child_is_built(self):
        result, built = _spawn([{"goal": GOAL_A}, {"goal": GOAL_B, "reasoning_effort": "turbo"}])
        assert "reasoning_effort" in result["error"] and "Task 1" in result["error"]
        assert built == []

    def test_internal_route_owner_ignores_tiers(self):
        """/review passes its own route (credentials_cfg); a tier must not re-route it."""
        route = {"model": "review-model"}
        _, (child,) = _spawn([{"goal": GOAL_A, "tier": "hard"}], credentials_cfg=route)
        assert child["model"] == "review-model"


class TestSessionForcedRoute:
    """The Desktop composer's subagent pick: one route for every child of that session."""

    def _forced(self, **route):
        parent = _parent()
        parent._delegation_override = route
        return parent

    def test_forced_route_beats_tier_and_task_effort(self):
        parent = self._forced(model="forced-model", reasoning_effort="minimal")
        _, built = _spawn([{"goal": GOAL_A, "tier": "easy"},
                           {"goal": GOAL_B, "tier": "hard", "reasoning_effort": "xhigh"}], parent=parent)
        assert [c["model"] for c in built] == ["forced-model", "forced-model"]
        assert all(c["reasoning_config"] == {"enabled": True, "effort": "minimal"} for c in built)

    def test_forced_model_without_effort_keeps_normal_effort(self):
        parent = self._forced(model="forced-model")
        _, (child,) = _spawn([{"goal": GOAL_A, "tier": "easy"}], parent=parent)
        assert child["model"] == "forced-model"
        assert child["reasoning_config"] == {"enabled": True, "effort": "medium"}

    def test_forced_provider_switch_does_not_borrow_hard_tier_endpoint(self):
        parent = self._forced(provider="", model="forced-model")
        cfg = {**CFG, "base_url": "http://normal/v1", "api_key": "normal-key"}
        _, (child,) = _spawn([{"goal": GOAL_A, "tier": "hard"}], cfg=cfg, parent=parent)
        assert (child["model"], child["base_url"]) == ("forced-model", "http://normal/v1")

    def test_forced_model_without_effort_ignores_task_effort(self):
        """Under a forced route the model's own effort pick is ignored even when the pick names no effort."""
        parent = self._forced(model="forced-model")
        _, (child,) = _spawn([{"goal": GOAL_A, "reasoning_effort": "xhigh"}], parent=parent)
        assert child["reasoning_config"] == {"enabled": True, "effort": "medium"}

    def test_forced_provider_switch_drops_base_endpoint_key_and_overrides(self):
        """A forced provider must not inherit the base block's endpoint, key or request headers."""
        parent = self._forced(provider="deepseek", model="deepseek-v4")
        cfg = {**CFG, "base_url": "http://normal/v1", "api_key": "normal-key",
               "request_overrides": {"extra_headers": {"X-Token": "normal-only"}}}
        seen = {}

        def _creds(routing_cfg, parent_agent):
            seen.update(routing_cfg)
            return {"model": routing_cfg.get("model"), "provider": routing_cfg.get("provider"),
                    "base_url": None, "api_key": None, "api_mode": None}

        with patch("tools.delegate_tool._resolve_delegation_credentials", side_effect=_creds):
            _spawn([{"goal": GOAL_A}], cfg=cfg, parent=parent)
        assert (seen["provider"], seen["model"]) == ("deepseek", "deepseek-v4")
        for key in ("base_url", "api_key", "request_overrides"):
            assert key not in seen, key

    @pytest.mark.parametrize("override", [None, {}, {"model": ""}, "auto"])
    def test_auto_or_empty_override_leaves_tiers_in_charge(self, override):
        parent = _parent()
        parent._delegation_override = override
        _, (child,) = _spawn([{"goal": GOAL_A, "tier": "easy"}], parent=parent)
        assert child["model"] == "easy-model"

    def test_internal_route_owner_ignores_forced_route(self):
        parent = self._forced(model="forced-model")
        _, (child,) = _spawn([{"goal": GOAL_A}], parent=parent, credentials_cfg={"model": "review-model"})
        assert child["model"] == "review-model"


class TestSchemaContract:
    def test_tier_enum_is_the_declared_set(self):
        item = DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"]["properties"]
        assert tuple(item["tier"]["enum"]) == DELEGATION_TIERS

    def test_effort_enum_parses(self):
        from hermes_constants import parse_reasoning_effort
        item = DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"]["properties"]
        assert item["reasoning_effort"]["enum"]
        assert all(parse_reasoning_effort(v) is not None for v in item["reasoning_effort"]["enum"])

    def test_no_free_form_route_fields_for_the_model(self):
        item = DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"]["properties"]
        assert not {"model", "provider", "base_url", "api_key"} & set(item)

    def test_dynamic_overrides_keep_tier_fields(self):
        from tools.delegate_tool import _build_dynamic_schema_overrides
        props = _build_dynamic_schema_overrides()["parameters"]["properties"]["tasks"]["items"]["properties"]
        assert {"tier", "reasoning_effort"} <= set(props)
