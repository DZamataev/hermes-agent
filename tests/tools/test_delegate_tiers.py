"""Difficulty-tier routing for delegate_task children (easy / normal / hard).

Contract: the model picks a fixed-name tier per task; the operator maps tiers to routes in config.
``normal`` IS the base ``delegation.*`` block; ``easy``/``hard`` live under ``delegation.tiers`` and
merge over it. An unconfigured tier behaves exactly like ``normal``.
"""

import pytest

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
